#!/usr/bin/env python3
"""Fake semgrep/gitleaks/trivy for Phase 3 chaos tests: deterministic output, logged invocations,
and an optional "hang" (trivy) controlled by a flag file so a test can kill the worker mid-scan."""
import json
import os
import sys
import time
from pathlib import Path

name = Path(sys.argv[0]).name
state = Path(os.environ["P3_SHIM_STATE"])
args = sys.argv[1:]
if "--version" in args or "version" in args[:1]:
    print(f"{name} 0.0.0-shim")
    sys.exit(0)
with (state / "calls.log").open("a") as f:
    f.write(f"{name} {os.getpid()}\n")
src = args[-1] if name != "gitleaks" else args[args.index("--source") + 1]
if name == "semgrep":
    res = [{"check_id": f"p3.rule{i}", "path": f"{src}/app.py", "start": {"line": i + 1}, "end": {"line": i + 1},
            "extra": {"severity": ["ERROR", "WARNING", "INFO"][i % 3], "message": f"m{i}", "metadata": {}}}
           for i in range(4)]
    print(json.dumps({"results": res, "errors": []}))
elif name == "gitleaks":
    rp = args[args.index("--report-path") + 1]
    Path(rp).write_text(json.dumps([{"RuleID": "generic-api-key", "File": f"{src}/conf.env", "StartLine": 1,
                                     "Description": "key", "Secret": "REDACTED", "Match": "k=REDACTED"}]))
elif name == "trivy":
    (state / "trivy.started").write_text(str(os.getpid()))
    while (state / "hang").exists():
        time.sleep(0.1)
    print(json.dumps({"Results": [{"Target": "requirements.txt", "Vulnerabilities": [
        {"VulnerabilityID": "CVE-2019-1010083", "PkgName": "flask", "InstalledVersion": "0.12",
         "FixedVersion": "1.0", "Severity": "HIGH", "Title": "flask dos"}]}]}))
