"""Valid report fixtures are also demo data: summary severity counts must match the listed findings."""
import json
import pathlib

import pytest

VALID = sorted((pathlib.Path(__file__).parent / "fixtures" / "valid").glob("report__*.json"))


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_summary_counts_match_findings(path):
    d = json.loads(path.read_text())
    for sev in ("critical", "high", "medium", "low", "info"):
        assert d["summary"][sev] == sum(f["severity"] == sev for f in d["findings"]), sev
