"""Run Semgrep / Gitleaks / Trivy and normalize findings."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO", "UNKNOWN"]


def _norm_sev(s: str | None) -> str:
    s = (s or "").upper()
    return {"ERROR": "HIGH", "WARNING": "MEDIUM", "NOTE": "LOW"}.get(s, s if s in SEV_ORDER else "UNKNOWN")


def parse_semgrep(data: dict) -> list[dict]:
    return [{"scanner": "semgrep", "rule": r.get("check_id"), "severity": _norm_sev(r.get("extra", {}).get("severity")),
             "file": r.get("path"), "line": r.get("start", {}).get("line"),
             "message": (r.get("extra", {}).get("message") or "")[:500]} for r in data.get("results", [])]


def parse_gitleaks(data: list) -> list[dict]:
    # Never keep the secret value itself in the report.
    return [{"scanner": "gitleaks", "rule": r.get("RuleID"), "severity": "HIGH", "file": r.get("File"),
             "line": r.get("StartLine"), "message": f"Possible secret: {r.get('Description', '')}"} for r in data or []]


def parse_trivy(data: dict) -> list[dict]:
    out = []
    for res in data.get("Results", []) or []:
        for v in res.get("Vulnerabilities", []) or []:
            out.append({"scanner": "trivy", "rule": v.get("VulnerabilityID"), "severity": _norm_sev(v.get("Severity")),
                        "file": res.get("Target"), "line": None,
                        "message": f"{v.get('PkgName')} {v.get('InstalledVersion')} -> {v.get('FixedVersion') or 'no fix'}: {v.get('Title') or ''}"[:500]})
        for m in res.get("Misconfigurations", []) or []:
            out.append({"scanner": "trivy", "rule": m.get("ID"), "severity": _norm_sev(m.get("Severity")),
                        "file": res.get("Target"), "line": None, "message": (m.get("Title") or "")[:500]})
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
