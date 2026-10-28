"""Run Semgrep / Gitleaks / Trivy and normalize findings."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

SEV_ORDER = ["critical", "high", "medium", "low", "info"]  # finding.schema.json severity


def finding_id(tool: str, rule: str, file: str, line) -> str:
    """finding.schema.json recipe: sha1(tool\nrule\nfile\n(line or ''))."""
    return hashlib.sha1(f"{tool}\n{rule}\n{file}\n{'' if line is None else line}".encode()).hexdigest()


def _norm_sev(s: str | None) -> str:
    s = (s or "").lower()
    s = {"error": "high", "warning": "medium", "note": "low", "unknown": "info"}.get(s, s)
    return s if s in SEV_ORDER else "info"


def make_finding(tool, category, rule, severity, file, line, title, description, **extra) -> dict:
    rule, file = str(rule or "unknown"), str(file or "")
    line = int(line) if isinstance(line, int) or (isinstance(line, str) and line.isdigit()) else None
    if line is not None and line < 1:
        line = None
    f = {"id": finding_id(tool, rule, file, line), "tool": tool, "category": category, "rule": rule,
         "severity": _norm_sev(severity), "file": file, "line": line,
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
                                line_end=end if isinstance(end, int) and end >= 1 else None, cwe=cwe or None))
    return out


def parse_gitleaks(data: list) -> list[dict]:
    # Never keep the secret value itself in the report.
    return [make_finding("gitleaks", "secret", r.get("RuleID"), "high", r.get("File"), r.get("StartLine"),
                         f"Possible secret: {r.get('Description', '')}", f"Possible secret: {r.get('Description', '')}",
                         secret_redacted=True) for r in data or []]


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


def run_scanner(name: str, src: Path, timeout: int, runner=_run, which=shutil.which) -> dict:
    """Returns {"status": ok|skipped|error, "findings": [...], "error"?: str}."""
    if not which(name):
        return {"status": "skipped", "findings": [], "error": f"{name} not installed"}
    try:
        if name == "semgrep":
            p = runner(["semgrep", "scan", "--config", "auto", "--json", "--quiet", "--metrics", "off", str(src)], timeout)
            return {"status": "ok", "findings": parse_semgrep(json.loads(p.stdout or "{}"))}
        if name == "gitleaks":
            with tempfile.TemporaryDirectory() as td:
                rep = Path(td) / "gl.json"
                runner(["gitleaks", "detect", "--no-git", "--redact", "--source", str(src),
                        "--report-format", "json", "--report-path", str(rep), "--exit-code", "0"], timeout)
                data = json.loads(rep.read_text() or "[]") if rep.exists() else []
            return {"status": "ok", "findings": parse_gitleaks(data)}
        if name == "trivy":
            p = runner(["trivy", "fs", "--quiet", "--format", "json", "--scanners", "vuln,misconfig", str(src)], timeout)
            return {"status": "ok", "findings": parse_trivy(json.loads(p.stdout or "{}"))}
        raise ValueError(name)
    except subprocess.TimeoutExpired:
        return {"status": "error", "findings": [], "error": f"{name} timed out after {timeout}s"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "findings": [], "error": str(exc)[:500]}


SCANNERS = ["semgrep", "gitleaks", "trivy"]
