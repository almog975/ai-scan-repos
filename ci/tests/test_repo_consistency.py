"""Version badge wiring and the pause-only-from-running rule across API, contract, UI, mock and docs."""
import re
from conftest import ROOT


def read(p):
    return (ROOT / p).read_text()


def test_version_badge_wiring():
    assert "--build-arg=VITE_APP_VERSION=${env.GIT_SHA}" in read("Jenkinsfile")
    df = read("services/frontend/Dockerfile")
    assert "ARG VITE_APP_VERSION" in df and "ENV VITE_APP_VERSION=$VITE_APP_VERSION" in df
    assert "import.meta.env.VITE_APP_VERSION" in read("services/frontend/src/version.ts")


def test_pause_only_from_running_everywhere():
    st = read("services/review-api/app/state.py")
    tr = dict(re.findall(r"State\.(\w+): \{([^}]*)\}", st))
    assert [s for s, t in tr.items() if "State.paused" in t] == ["running"]
    assert 'r.state != State.running' in read("services/review-api/app/routers/control.py")
    assert "Allowed only from running" in read("services/contracts/review-api.openapi.yaml")
    assert "pause: (s: State) => s === 'running'" in read("services/frontend/src/api/client.ts")
    assert 'PAUSABLE = {"running"}' in read("services/frontend/e2e/mock_stack.py")
    assert "only while `running`" in read("docs/user-guide.md")
