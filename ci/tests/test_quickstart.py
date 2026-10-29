"""Phase 9 quickstart: port + WORK_DIR configurable via .env, dev user without hand-made X-User-Id."""
import re
import yaml
from conftest import ROOT


def test_env_example_has_port_and_workdir():
    env = (ROOT / ".env.example").read_text()
    assert re.search(r"^AISCAN_HTTP_PORT=8080$", env, re.M) and re.search(r"^WORK_DIR=/work$", env, re.M)


def test_compose_uses_them():
    c = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    assert c["frontend"]["ports"] == ["${AISCAN_HTTP_PORT:-8080}:8080"]
    assert c["gateway"]["environment"]["PUBLIC_URL"] == "http://localhost:${AISCAN_HTTP_PORT:-8080}"
    ra = c["review-api"]
    assert ra["environment"]["WORK_DIR"] == "${WORK_DIR:-/work}" and "work:${WORK_DIR:-/work}" in ra["volumes"]
    assert ra["ports"] == ["127.0.0.1:8001:8001"]   # internal API stays on localhost


def test_readme_uses_devseed():
    r = (ROOT / "README.md").read_text()
    assert "python -m app.devseed" in r and "ensure_user(s,1" not in r
