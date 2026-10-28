"""Run Semgrep / Gitleaks / Trivy and normalize findings."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import logging
from pathlib import Path

log = logging.getLogger(__name__)
SEV_ORDER = ["critical", "high", "medium", "low", "info"]  # finding.schema.json severity


def finding_id(tool: str, rule: str, file: str, line) -> str:
    """finding.schema.json recipe: sha1(tool\nrule\nfile\n(line or ''))."""
    return hashlib.sha1(f"{tool}\n{rule}\n{file}\n{'' if line is None else line}".encode()).hexdigest()


# docs/severity-mapping.md. Keys are upper-cased tool values; anything else -> info + warning log.
SEVERITY_MAP = {
    "semgrep": {"UNKNOWN": "info", "CRITICAL": "critical", "ERROR": "high", "HIGH": "high", "WARNING": "medium", "MEDIUM": "medium",
                "INFO": "low", "LOW": "low", "NOTE": "low", "INVENTORY": "info", "EXPERIMENT": "info"},
    "trivy": {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium", "LOW": "low", "UNKNOWN": "info"},
    "gitleaks": {"HIGH": "high"},
    "ai": {s.upper(): s for s in SEV_ORDER},
}
MAX_SNIPPET_LINES = 10
SECRET_MASK = "********"


def map_severity(tool: str, s: str | None) -> str:
    v = str(s or "").strip().upper()
    out = SEVERITY_MAP.get(tool, {}).get(v)
    if out is None:
        log.warning("unknown %s severity %r -> info", tool, s)
        return "info"
    return out


def _norm_sev(s: str | None, tool: str = "ai") -> str:
    return map_severity(tool, s)


def clip_snippet(text: str | None) -> str | None:
    if not text or not str(text).strip() or str(text).strip() == "requires login":
        return None
    return "\n".join(str(text).rstrip("\n").splitlines()[:MAX_SNIPPET_LINES])[:4000]


def mask_secret(text: str | None, secret: str | None) -> str | None:
    """Gitleaks snippet: the secret value never reaches the report (even if --redact was not honoured)."""
    if not text:
        return None
    if secret:
        text = text.replace(secret, SECRET_MASK)
    return text.replace("REDACTED", SECRET_MASK)


def make_finding(tool, category, rule, severity, file, line, title, description, **extra) -> dict:
    rule, file = str(rule or "unknown"), str(file or "")
    line = int(line) if isinstance(line, int) or (isinstance(line, str) and line.isdigit()) else None
    if line is not None and line < 1:
        line = None
    f = {"id": finding_id(tool, rule, file, line), "tool": tool, "category": category, "rule": rule,
         "severity": map_severity(tool, severity), "file": file, "line": line,
         "title": (title or rule)[:200], "description": (description or "")[:1000]}
    f.update({k: v for k, v in extra.items() if v is not None})
    return f


def parse_semgrep(data: dict) -> list[dict]:
    out = []
    for r in data.get("results", []):
        ex = r.get("extra", {})
        cwe = [m.group(0) for c in (ex.get("metadata", {}).get("cwe") or []) for m in [re.search(r"CWE-[0-9]+", str(c))] if m]
        end = r.get("end", {}).get("line")
        out.append(make_finding("semgrep", "sast", r.get("check_id"), ex.get("severity"), r.get("path"),
                                r.get("start", {}).get("line"), r.get("check_id"), ex.get("message"),
                                line_end=end if isinstance(end, int) and end >= 1 else None, cwe=cwe or None,
                                snippet=clip_snippet(ex.get("lines"))))
    return out


def parse_gitleaks(data: list) -> list[dict]:
    # Never keep the secret value itself in the report.
    return [make_finding("gitleaks", "secret", r.get("RuleID"), "high", r.get("File"), r.get("StartLine"),
                         f"Possible secret: {r.get('Description', '')}", f"Possible secret: {r.get('Description', '')}",
                         secret_redacted=True,
                         snippet=clip_snippet(mask_secret(r.get("Match"), r.get("Secret"))))
            for r in data or []]


def parse_trivy(data: dict) -> list[dict]:
    out = []
    for res in data.get("Results", []) or []:
        for v in res.get("Vulnerabilities", []) or []:
            vid = v.get("VulnerabilityID")
            out.append(make_finding(
                "trivy", "dependency", f"{vid}:{v.get('PkgName')}", v.get("Severity"), res.get("Target"), None,
                v.get("Title") or vid,
                f"{v.get('PkgName')} {v.get('InstalledVersion')} -> {v.get('FixedVersion') or 'no fix'}",
                cve=vid if str(vid or "").startswith("CVE-") else None, package=v.get("PkgName"),
                fixed_version=v.get("FixedVersion") or None))
        for m in res.get("Misconfigurations", []) or []:
            out.append(make_finding("trivy", "misconfig", m.get("ID"), m.get("Severity"), res.get("Target"), None,
                                    m.get("Title"), m.get("Description") or m.get("Title")))
    return out


def _run(cmd: list[str], timeout: int, ok_codes=(0, 1)) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode not in ok_codes:
        raise RuntimeError(f"{cmd[0]} exit {p.returncode}: {p.stderr[-500:]}")
    return p


SEMGREP_CONFIG = os.environ.get("SEMGREP_CONFIG", "p/default")


def relativize(findings: list[dict], src: Path) -> list[dict]:
    """finding.file relative to the repo root (tools may report absolute clone paths); id recomputed so it
    is stable across attempts (each attempt clones into a fresh temp dir)."""
    roots = {str(src).rstrip("/") + "/"}
    try:
        roots.add(str(Path(src).resolve()).rstrip("/") + "/")
    except OSError:
        pass
    for f in findings:
        fp = f["file"]
        for r in roots:
            if fp.startswith(r):
                fp = fp[len(r):]
        fp = fp.lstrip("./") if fp.startswith("./") else fp
        if fp != f["file"]:
            f["file"] = fp
            f["id"] = finding_id(f["tool"], f["rule"], fp, f["line"])
    return findings


def run_scanner(name: str, src: Path, timeout: int, runner=_run, which=shutil.which) -> dict:
    """Returns {"status": ok|skipped|error, "findings": [...], "error"?: str}."""
    if not which(name):
        return {"status": "skipped", "findings": [], "error": f"{name} not installed"}
    try:
        if name == "semgrep":
            # --config auto needs metrics on (semgrep exits 2); the pinned registry pack works offline-of-metrics.
            p = runner(["semgrep", "scan", "--config", SEMGREP_CONFIG, "--json", "--quiet", "--metrics", "off",
                        "--disable-version-check", str(src)], timeout)
            return {"status": "ok", "findings": relativize(parse_semgrep(json.loads(p.stdout or "{}")), src)}
        if name == "gitleaks":
            with tempfile.TemporaryDirectory() as td:
                rep = Path(td) / "gl.json"
                runner(["gitleaks", "detect", "--no-git", "--redact", "--source", str(src),
                        "--report-format", "json", "--report-path", str(rep), "--exit-code", "0"], timeout)
                data = json.loads(rep.read_text() or "[]") if rep.exists() else []
            return {"status": "ok", "findings": relativize(parse_gitleaks(data), src)}
        if name == "trivy":
            p = runner(["trivy", "fs", "--quiet", "--format", "json", "--scanners", "vuln,misconfig", str(src)], timeout)
            return {"status": "ok", "findings": relativize(parse_trivy(json.loads(p.stdout or "{}")), src)}
        raise ValueError(name)
    except subprocess.TimeoutExpired:
        return {"status": "error", "findings": [], "error": f"{name} timed out after {timeout}s"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "findings": [], "error": str(exc)[:500]}


SCANNERS = ["semgrep", "gitleaks", "trivy"]


_VERSION_CMDS = {"semgrep": ["semgrep", "--version"], "gitleaks": ["gitleaks", "version"],
                 "trivy": ["trivy", "--version"], "git": ["git", "--version"]}


_TV_CACHE: dict[str, str] | None = None


def tool_versions(runner=None, which=shutil.which) -> dict[str, str]:
    """Cached per process when called with the real runner (one Job == one process)."""
    global _TV_CACHE
    if runner is None and which is shutil.which:
        if _TV_CACHE is None:
            _TV_CACHE = _tool_versions(None, which)
        return dict(_TV_CACHE)
    return _tool_versions(runner, which)


def _tool_versions(runner, which) -> dict[str, str]:
    """{tool: version} for installed tools (report.tool_versions); missing/broken tools are omitted."""
    import re
    out: dict[str, str] = {}
    for name, cmd in _VERSION_CMDS.items():
        if not which(name):
            continue
        try:
            p = (runner or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=20)))(cmd)
            m = re.search(r"\d+\.\d+(?:\.\d+)?", (p.stdout or "") + (p.stderr or ""))
            if m and getattr(p, "returncode", 0) == 0:
                out[name] = m.group(0)
        except Exception:  # noqa: BLE001
            pass
    return out
