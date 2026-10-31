"""Deploy stage PR flow (ci/deploy/open-pr.sh) against a local bare repo served over smart HTTP with Basic auth
(git http-backend) and a fake GitHub REST API: branch push via GIT_ASKPASS, PR creation, idempotent 422, no token
leakage. Also checks that what Almog's merge/squash of the PR puts on main is skipped by the Loop guard."""
import base64, json, os, re, shutil, subprocess, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from conftest import ROOT

SCRIPT = ROOT / "ci/deploy/open-pr.sh"
JF = (ROOT / "Jenkinsfile").read_text()
TOKEN = "github_pat_FAKE0000TESTONLY1111"   # fake, never a real credential
USER = "ai-scan-ci-bot"
BOT = ["-c", "user.name=ai-scan-ci-bot", "-c", "user.email=ci-bot@ai-scan.local"]
ALMOG = ["-c", "user.name=almog975", "-c", "user.email=almog975@users.noreply.github.com"]
pytestmark = pytest.mark.skipif(not (shutil.which("curl") and shutil.which("git")), reason="needs git + curl")


def git(*a, cwd, env=None):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()


class Fake(BaseHTTPRequestHandler):
    prs, api_auth, bare = {}, [], None

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _git(self):
        good = "Basic " + base64.b64encode(f"{USER}:{TOKEN}".encode()).decode()
        if self.headers.get("Authorization") != good:
            self.send_response(401); self.send_header("WWW-Authenticate", 'Basic realm="git"')
            self.send_header("Content-Length", "0"); self.end_headers(); return
        path, _, qs = self.path.partition("?")
        n = int(self.headers.get("Content-Length") or 0)
        env = {**os.environ, "GIT_PROJECT_ROOT": str(self.bare.parent), "GIT_HTTP_EXPORT_ALL": "1",
               "PATH_INFO": path[len("/git"):], "QUERY_STRING": qs, "REQUEST_METHOD": self.command,
               "CONTENT_TYPE": self.headers.get("Content-Type", ""), "CONTENT_LENGTH": str(n), "REMOTE_USER": USER}
        out = subprocess.run(["git", "http-backend"], input=self.rfile.read(n), env=env, capture_output=True).stdout
        head, _, body = out.partition(b"\r\n\r\n")
        status = 200
        hdrs = [l.split(": ", 1) for l in head.decode().split("\r\n") if ": " in l]
        for k, v in hdrs:
            if k.lower() == "status":
                status = int(v.split()[0])
        self.send_response(status)
        for k, v in hdrs:
            if k.lower() != "status":
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/git/"):
            return self._git()
        self.api_auth.append(self.headers.get("Authorization"))
        head = re.search(r"head=[^:&]+:([^&]+)", self.path).group(1)
        self._json(200, [p for p in self.prs.values() if p["head"] == head])

    def do_POST(self):
        if self.path.startswith("/git/"):
            return self._git()
        self.api_auth.append(self.headers.get("Authorization"))
        assert self.path == "/repos/almog975/ai-scan-repos/pulls"
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if req["head"] == "deploy/bad-0000000":
            return self._json(422, {"message": "Validation Failed", "errors": [{"message": "No commits between"}]})
        if req["head"] in self.prs:
            return self._json(422, {"message": "Validation Failed", "errors": [
                {"resource": "PullRequest", "code": "custom", "message": f"A pull request already exists for almog975:{req['head']}."}]})
        n = len(self.prs) + 1
        self.prs[req["head"]] = {**req, "number": n, "html_url": f"https://github.com/almog975/ai-scan-repos/pull/{n}"}
        self._json(201, self.prs[req["head"]])


@pytest.fixture
def world(tmp_path):
    bare = tmp_path / "ai-scan-repos.git"
    git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("config", "http.receivepack", "true", cwd=bare)
    work = tmp_path / "work"
    git("init", "-q", "-b", "main", str(work), cwd=tmp_path)
    ov = work / "deploy/gateway/overlays/k3s"; ov.mkdir(parents=True)
    (ov / "kustomization.yaml").write_text("images:\n- name: ai-scan/gateway\n  newTag: aaaaaaa\n")
    (work / "gateway").mkdir(); (work / "gateway/app.py").write_text("v=1\n")
    git(*ALMOG, "add", "-A", cwd=work); git(*ALMOG, "commit", "-qm", "base", cwd=work)
    git("push", "-q", str(bare), "main", cwd=work)
    Fake.prs, Fake.api_auth, Fake.bare = {}, [], bare
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield work, bare, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def bump(work, sha, branch, comps="gateway"):
    git("checkout", "-q", "-B", branch, "main", cwd=work)
    (work / "deploy/gateway/overlays/k3s/kustomization.yaml").write_text(f"images:\n- name: ai-scan/gateway\n  newTag: {sha}\n")
    git("add", "deploy/gateway/overlays/k3s/kustomization.yaml", cwd=work)
    git(*BOT, "commit", "-qm", f"ci(k3s): {comps} -> {sha} [skip ci]", cwd=work)


def run(work, url, branch, sha, token=TOKEN, comps="gateway"):
    env = {"PATH": os.environ["PATH"], "HOME": str(work.parent), "GU": USER, "GT": token, "DEPLOY_BRANCH": branch,
           "COMPONENTS": comps, "GIT_SHA": sha, "TARGET": "k3s", "GITHUB_API": url,
           "PUSH_URL": f"{url}/git/ai-scan-repos.git", "BUILD_URL": "http://jenkins/job/1/"}
    return subprocess.run(["bash", str(SCRIPT)], cwd=work, env=env, capture_output=True, text=True)


def test_push_pr_and_422_idempotent_no_leak(world):
    work, bare, url = world
    br = "deploy/gateway-bbbbbbb"
    bump(work, "bbbbbbb", br)
    r = run(work, url, br, "bbbbbbb")
    assert r.returncode == 0, r.stderr
    assert "Opened PR: https://github.com/almog975/ai-scan-repos/pull/1" in r.stdout
    assert git("rev-parse", br, cwd=bare) == git("rev-parse", "HEAD", cwd=work)
    pr = Fake.prs[br]
    assert pr["base"] == "main" and "[skip ci]" in pr["title"] and "gateway" in pr["body"] and "bbbbbbb" in pr["body"]
    first = git("rev-parse", br, cwd=bare)
    # re-run (Jenkins replay: new commit, same bump) -> branch kept (no force push), 422 handled, still 1 PR
    bump(work, "bbbbbbb", br)
    env = {**os.environ, "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"}  # a distinct commit, same tree
    git(*BOT, "commit", "-q", "--amend", "--no-edit", "--date=2000-01-01T00:00:00Z", cwd=work, env=env)
    assert git("rev-parse", "HEAD", cwd=work) != first
    r2 = run(work, url, br, "bbbbbbb")
    assert r2.returncode == 0, r2.stderr
    assert "already has this tag bump" in r2.stdout
    assert "PR already exists (422)" in r2.stdout and "/pull/1" in r2.stdout and len(Fake.prs) == 1
    assert git("rev-parse", br, cwd=bare) == first
    # token: used (Bearer on every API call) but never printed or stored
    assert Fake.api_auth and all(a == f"Bearer {TOKEN}" for a in Fake.api_auth)
    for out in (r.stdout, r.stderr, r2.stdout, r2.stderr, (work / ".git/config").read_text()):
        assert TOKEN not in out
    assert git("remote", cwd=work) == ""


def test_existing_branch_with_other_content_is_not_overwritten(world):
    work, bare, url = world
    br = "deploy/gateway-ddddddd"
    bump(work, "ddddddd", br)
    assert run(work, url, br, "ddddddd").returncode == 0
    first = git("rev-parse", br, cwd=bare)
    bump(work, "eeeeeee", br)  # different tree on the same branch name
    r = run(work, url, br, "ddddddd")
    assert r.returncode == 1 and "refusing to overwrite" in r.stderr and TOKEN not in r.stdout + r.stderr
    assert git("rev-parse", br, cwd=bare) == first and git("rev-parse", "main", cwd=bare) != first


def test_script_never_force_pushes_or_targets_main():
    sh = SCRIPT.read_text()
    code = "\n".join(l for l in sh.splitlines() if not l.lstrip().startswith("#"))
    assert "--force" not in code and " -f " not in code and "+HEAD" not in code and "HEAD:main" not in code


def test_wrong_token_fails_and_other_422_fails(world):
    work, bare, url = world
    bump(work, "ccccccc", "deploy/gateway-ccccccc")
    r = run(work, url, "deploy/gateway-ccccccc", "ccccccc", token="wrong")
    assert r.returncode != 0 and "wrong" not in r.stdout + r.stderr   # proves the push really authenticates
    bump(work, "0000000", "deploy/bad-0000000")
    r = run(work, url, "deploy/bad-0000000", "0000000")
    assert r.returncode == 1 and "No commits between" in r.stderr and TOKEN not in r.stdout + r.stderr


def guard(work, base):
    """Python model of the Jenkinsfile Loop guard on main (branch checks are not involved there)."""
    msg = git("log", "-1", "--pretty=%B", cwd=work)
    author = git("log", "-1", "--pretty=%ae", cwd=work)
    files = git("diff", "--name-only", base, "HEAD", cwd=work).split()
    reasons = []
    if author == "ci-bot@ai-scan.local": reasons.append("bot")
    if "[skip ci]" in msg: reasons.append("skip-ci")
    if files and all(f.startswith("deploy/") for f in files): reasons.append("deploy-only")
    return reasons


@pytest.mark.parametrize("how", ["merge", "squash", "rebase"])
def test_almog_merge_of_deploy_pr_is_skipped(world, how):
    work, _, _ = world
    base = git("rev-parse", "main", cwd=work)          # = GIT_PREVIOUS_SUCCESSFUL_COMMIT (source build)
    br = "deploy/gateway-bbbbbbb"
    bump(work, "bbbbbbb", br)
    git("checkout", "-q", "main", cwd=work)
    title = "ci(k3s): gateway -> bbbbbbb [skip ci]"
    if how == "merge":       # GitHub "Create a merge commit": author = Almog, subject = Merge pull request, body = title
        git(*ALMOG, "merge", "-q", "--no-ff", br, "-m", f"Merge pull request #1 from almog975/{br}\n\n{title}", cwd=work)
    elif how == "squash":    # GitHub squash: author = PR author (bot), committer GitHub, subject = "<title> (#1)"
        git("merge", "-q", "--squash", br, cwd=work); git(*BOT, "commit", "-qm", f"{title} (#1)", cwd=work)
    else:                    # rebase: bot commit replayed
        git("merge", "-q", "--ff-only", br, cwd=work)
    r = guard(work, base)
    assert "deploy-only" in r, r            # the deploy/**-only check alone skips it, whatever the message
    # stricter: even if Almog edits the message to drop [skip ci] on a merge commit, deploy-only still skips
    if how == "merge":
        git(*ALMOG, "commit", "-q", "--amend", "-m", "Merge pull request #1 (edited)", cwd=work)
        assert guard(work, base) == ["deploy-only"]


def test_jenkinsfile_wires_the_script():
    dep = JF[JF.index("stage('Deploy')"):JF.index("  post {")]
    assert "usernamePassword(credentialsId: 'github-bot', usernameVariable: 'GU', passwordVariable: 'GT')" in dep
    assert "sh 'set +x; ci/deploy/open-pr.sh'" in dep           # single quotes: no Groovy interpolation of GT
    assert "deploy/${changed.size() == 1 ? changed[0] : 'multi'}-${env.GIT_SHA}" in dep
    assert "@github.com" not in JF and "${GT}" not in JF
    assert "CHANGE_BRANCH" in JF and "startsWith('deploy/')" in JF
