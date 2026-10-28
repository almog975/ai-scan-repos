import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def fixture_scans():
    from scanner.tools import parse_gitleaks, parse_semgrep, parse_trivy
    return {"semgrep": {"status": "ok", "findings": parse_semgrep(load("semgrep.json"))},
            "gitleaks": {"status": "ok", "findings": parse_gitleaks(load("gitleaks.json"))},
            "trivy": {"status": "skipped", "findings": [], "error": "trivy not installed"}}
