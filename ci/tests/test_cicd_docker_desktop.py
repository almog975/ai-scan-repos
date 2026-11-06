"""CI/CD + GitOps on Docker Desktop (docs/cicd-docker-desktop.md): static checks that the Jenkins job,
the chart's values-images.yaml and the Argo CD Application stay wired together."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "ai-scan"
JF = (ROOT / "ci/cicd/Jenkinsfile.docker-desktop").read_text()
APP = yaml.safe_load((ROOT / "deploy/argocd/docker-desktop/ai-scan.yaml").read_text())
JENKINS = yaml.safe_load((ROOT / "deploy/jenkins/local/values-local.yaml").read_text())
needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")

# 'svc' : ['dir', 'valuesKey', 'image']
SERVICES = {m[0]: (m[1], m[2], m[3]) for m in
            re.findall(r"'([\w-]+)'\s*:\s*\['([\w-]+)',\s*'(\w+)',\s*'([\w-]+)'\]", JF)}


def _render(*args):
    cmd = ["helm", "template", "ai-scan", str(CHART), "-n", "ai-scan-repos", *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def test_jenkinsfile_services_match_chart():
    assert set(SERVICES) == {"frontend", "gateway", "review-api"}
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    tags = yaml.safe_load((CHART / "values-images.yaml").read_text())["imageTags"]
    for svc, (src, key, image) in SERVICES.items():
        assert (ROOT / src / "Dockerfile").is_file()
        assert key in tags and key in values["imageTags"]
        assert values["images"][key] == image, f"{svc}: Jenkins pushes {image}, chart pulls {values['images'][key]}"


def test_bot_sed_rewrites_exactly_one_line_per_service():
    text = (CHART / "values-images.yaml").read_text()
    for svc, (_, key, _) in SERVICES.items():
        pat = re.compile(rf"^(  {key}:).*$", re.M)            # same regex as the sed in the Jenkinsfile
        assert len(pat.findall(text)) == 1, key
        bumped = pat.sub(r'\1 "0123456789ab"', text)
        assert yaml.safe_load(bumped)["imageTags"][key] == "0123456789ab"
    assert f"sed -i -E 's|^(  ${{SERVICES[s][1]}}:).*|" in JF


def test_tags_are_git_sha_never_latest_and_registry_is_ghcr():
    assert "def REGISTRY  = 'ghcr.io/almog975'" in JF
    assert ":${env.GIT_SHA}" in JF and ":latest" not in JF


def test_loop_guard_and_skip_ci():
    assert "[skip ci]" in JF.split("stage('Loop guard')")[1].split("stage(")[0]
    assert re.search(r"commit -q -m 'ci\(gitops\): .*\[skip ci\]'", JF)


def test_ci_never_deploys_or_touches_the_cluster():
    code = "\n".join(l for l in JF.splitlines() if not l.lstrip().startswith("//"))
    for word in ("kubectl", "helm ", "argocd app sync", "/var/run/docker.sock", "privileged"):
        assert word not in code, word
    tmpl = yaml.safe_load(JENKINS["agent"]["podTemplates"]["ai-scan-ci"])[0]
    assert tmpl["serviceAccount"] == "default"
    assert {c["name"] for c in tmpl["containers"]} == {"jnlp", "python", "node", "kaniko", "trivy", "crane"}


def test_token_never_in_a_url_or_echoed():
    push = JF.split("withCredentials([usernamePassword(credentialsId: 'github-push'")[1]
    assert "set +x" in push and "credential.helper" in push
    assert "$GT@" not in JF and "${GT}@" not in JF


def test_jenkins_job_polls_and_uses_this_jenkinsfile():
    job = JENKINS["controller"]["JCasC"]["configScripts"]["ai-scan-cicd"]
    assert "scmpoll_spec('H/2 * * * *')" in job
    assert "scriptPath('ci/cicd/Jenkinsfile.docker-desktop')" in job
    assert "inheritFrom 'ai-scan-ci'" in JF


def test_argocd_app_values_files_exist():
    src = APP["spec"]["source"]
    assert src["path"] == "charts/ai-scan" and src["helm"]["releaseName"] == "ai-scan"
    for f in src["helm"]["valueFiles"]:
        assert (CHART / f).is_file(), f
    assert src["helm"]["valueFiles"][-1] == "values-images.yaml"   # bot tags win
    assert APP["spec"]["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}


def test_argocd_uses_annotation_tracking():
    cm = yaml.safe_load((ROOT / "infra/argocd/values-docker-desktop.yaml").read_text())["configs"]["cm"]
    assert cm["application.resourceTrackingMethod"] == "annotation"


@needs_helm
def test_argocd_render_has_no_secrets_and_uses_per_image_tags():
    out = _render("-f", str(CHART / "values-docker-desktop.yaml"), "-f", str(CHART / "values-images.yaml"),
                  "--set", "imageTags.frontend=0123456789ab")
    assert "kind: Secret\n" not in out
    assert "image: ghcr.io/almog975/ai-scan-repos-frontend:0123456789ab" in out
    assert "image: ghcr.io/almog975/ai-scan-repos-gateway:latest" in out


@needs_helm
def test_default_install_still_renders_secrets():
    assert _render().count("kind: Secret\n") == 4


def test_first_build_builds_all_and_no_change_is_not_built():
    guard = JF.split("stage('Loop guard')")[1].split("stage('Test')")[0]
    code = "\n".join(l for l in guard.splitlines() if not l.lstrip().startswith("//"))
    assert "HEAD~1" not in code
    assert "env.GIT_PREVIOUS_SUCCESSFUL_COMMIT ?: ''" in guard
    assert "(params.BUILD_ALL || !haveBase)" in guard
    no_change = guard.split("if (!changed)")[1].split("}")[0]
    assert "NOT_BUILT" in no_change


def test_tool_containers_work_in_the_agent_workspace():
    # kaniko --cleanup deletes its image WORKDIR (/workspace); a later `sh` must not chdir there.
    tmpl = yaml.safe_load(JENKINS["agent"]["podTemplates"]["ai-scan-ci"])[0]
    for c in tmpl["containers"]:
        if c["name"] != "jnlp":
            assert c.get("workingDir") == "/home/jenkins/agent", c["name"]


def test_one_stage_per_service_per_step_and_split_gitops():
    for step in ("Test", "Build", "Scan", "Push"):
        for svc in ("frontend", "gateway", "review-api"):
            assert f"stage('{step}: {svc}')" in JF
            assert f"{{ expression {{ svcOn('{svc}') }} }};" in JF.split(f"stage('{step}: {svc}')")[1].split("\n")[0]
    assert "stage('GitOps: commit')" in JF and "stage('GitOps: push')" in JF
    assert "pipeline-graph-view:" in (ROOT / "deploy/jenkins/local/values-local.yaml").read_text()
