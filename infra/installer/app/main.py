"""Installer wizard: collects the target, then prints/runs the matching script (dry-run by default)."""
import os
import shlex
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "app" / "templates"))
app = FastAPI(title="AI Scan Repos installer")
TARGETS = {"local": "install-local.sh", "ssh-k3s": "install-ssh-k3s.sh", "eks": "install-eks.sh"}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def form(request: Request):
    return templates.TemplateResponse(request, "index.html", {"result": None})


@app.post("/plan", response_class=HTMLResponse)
def plan(request: Request, target: str = Form(...), domain: str = Form("ai-scan.local"),
         ssh_host: str = Form(""), ssh_user: str = Form("ubuntu"),
         aws_region: str = Form("eu-central-1"), cluster_name: str = Form("ai-scan")):
    if target not in TARGETS:
        return templates.TemplateResponse(request, "index.html", {"result": "unknown target"}, status_code=400)
    env = {"DOMAIN": domain}
    if target == "ssh-k3s":
        env |= {"SSH_HOST": ssh_host, "SSH_USER": ssh_user}
    if target == "eks":
        env |= {"AWS_REGION": aws_region, "CLUSTER_NAME": cluster_name}
    cmd = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items()) + f" scripts/{TARGETS[target]}"
    # Execution is intentionally not wired yet (skeleton). Show the plan only.
    return templates.TemplateResponse(request, "index.html", {"result": cmd})
