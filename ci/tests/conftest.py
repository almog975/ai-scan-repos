import glob, shutil, subprocess
from pathlib import Path
import pytest, yaml

ROOT = Path(__file__).resolve().parents[2]


def build(overlay):
    return [d for d in yaml.safe_load_all(subprocess.run(["kustomize", "build", str(overlay)], capture_output=True,
                                                         text=True, check=True).stdout) if d]


@pytest.fixture(scope="session")
def k3s():
    if not shutil.which("kustomize"):
        pytest.skip("kustomize not on PATH")
    docs = []
    for o in sorted(glob.glob(str(ROOT / "deploy/*/overlays/k3s"))) + sorted(glob.glob(str(ROOT / "infra/*/overlays/k3s"))):
        for d in build(o):
            d.setdefault("metadata", {})["_overlay"] = o
            docs.append(d)
    return docs
