"""Scanner Job launchers: one batch/v1 Job per review attempt.

Kubernetes: the Job is rendered from Infra's `scanner-job-template` ConfigMap (mounted file
SCANNER_JOB_TEMPLATE, placeholders ${REVIEW_ID} ${ATTEMPT} ${DEPTH} ${REPO_URL} ${IMAGE}); review-api then
enforces the contract fields: name scan-<id>-a<attempt>, labels app=scanner/review-id/attempt/budget-no,
activeDeadlineSeconds 21600, backoffLimit 0, the `reports` PVC, env DEPTH/REVIEW_ID/ATTEMPT/BUDGET_NO/COMMIT_SHA,
and a pinned image (`:latest` rejected).
Secrets are never put in args or the Job spec: the per-review AI key goes into a Secret owned by the Job.
"""
from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from string import Template
from typing import Protocol

import yaml

from .config import Settings

MAX_ATTEMPTS = 5  # per budget (contract)
log = logging.getLogger(__name__)
SECRET_LABEL = "ai-scan-repos/per-review-secret"
_SAFE = re.compile(r"^[A-Za-z0-9:/._@-]*$")


@dataclass
class JobSpec:
    review_id: str
    attempt: int
    budget_no: int
    depth: str
    repo_url: str
    commit_sha: str | None = None
    ref: str = "HEAD"
    repo_private: bool = False

    @classmethod
    def of(cls, r) -> "JobSpec":
        return cls(r.id, r.attempt, r.budget_no, getattr(r.depth, "value", r.depth), r.repo_url, r.commit_sha, r.ref,
                   bool(getattr(r, "repo_private", False)))


class JobLauncher(Protocol):
    def launch(self, spec: JobSpec, secret_env: dict[str, str] | None = None) -> str: ...

    def delete(self, job_name: str) -> None: ...

    def cleanup(self, review_id: str) -> None: ...

    def status(self, job_name: str) -> str: ...  # active | succeeded | failed | missing


def job_name_for(review_id: str, attempt: int) -> str:
    """Deterministic: scan-<reviewId>-a<attempt> (idempotent create; <= 63 chars for a uuid)."""
    return f"scan-{review_id}-a{attempt}"[:63].rstrip("-")


def check_image(image: str) -> None:
    tag = image.rsplit("/", 1)[-1]
    if "@sha256:" in image:
        return
    if ":" not in tag or tag.endswith(":latest"):
        raise ValueError("SCANNER_IMAGE must be pinned to a tag other than :latest")


def contract_env(spec: JobSpec) -> list[dict]:
    env = {"REVIEW_ID": spec.review_id, "ATTEMPT": str(spec.attempt), "BUDGET_NO": str(spec.budget_no),
           "DEPTH": spec.depth, "REPO_URL": spec.repo_url, "REF": spec.ref, "COMMIT_SHA": spec.commit_sha or "",
           "REPO_PRIVATE": "true" if spec.repo_private else "false"}
    return [{"name": k, "value": v} for k, v in env.items()]


def render_template(text: str, s: Settings, spec: JobSpec) -> dict:
    values = {"REVIEW_ID": spec.review_id, "ATTEMPT": str(spec.attempt), "DEPTH": spec.depth,
              "REPO_URL": spec.repo_url, "IMAGE": s.scanner_image, "BUDGET_NO": str(spec.budget_no)}
    for k, v in values.items():
        if not _SAFE.match(v):
            raise ValueError(f"unsafe value for ${{{k}}}")
    doc = yaml.safe_load(Template(text).safe_substitute(values))
    if doc.get("kind") == "ConfigMap":  # accept the whole ConfigMap file too
        doc = yaml.safe_load(Template(doc["data"]["job.yaml"]).safe_substitute(values))
    return doc


def build_job_manifest(s: Settings, spec: JobSpec, ai_secret: str | None = None) -> dict:
    check_image(s.scanner_image)
    name = job_name_for(spec.review_id, spec.attempt)
    if s.scanner_job_template:
        with open(s.scanner_job_template) as f:
            m = render_template(f.read(), s, spec)
    else:
        m = _built_in_manifest(s)
    labels = {"app": "scanner", "review-id": spec.review_id, "attempt": str(spec.attempt),
              "budget-no": str(spec.budget_no)}
    md = m.setdefault("metadata", {})
    md.update(name=name, namespace=s.k8s_namespace)
    md.setdefault("labels", {}).update(labels)
    js = m.setdefault("spec", {})
    js.pop("suspend", None)
    js.update(backoffLimit=0, activeDeadlineSeconds=s.scanner_active_deadline_seconds)  # retries = watchdog
    js.setdefault("ttlSecondsAfterFinished", 3600)
    pod = js.setdefault("template", {})
    pod.setdefault("metadata", {}).setdefault("labels", {}).update(labels)
    ps = pod.setdefault("spec", {})
    ps["restartPolicy"] = "Never"
    ps["serviceAccountName"] = s.scanner_service_account
    ps["automountServiceAccountToken"] = False
    c = ps.setdefault("containers", [{}])[0]
    c.setdefault("name", "scanner")
    c["image"] = s.scanner_image
    c["args"] = ["--review-id", spec.review_id]
    ours = contract_env(spec) + [{"name": "REPORTS_DIR", "value": "/reports"},
                                 {"name": "REVIEW_API_URL", "value": s.review_api_internal_url}]
    names = {e["name"] for e in ours}
    c["env"] = [e for e in c.get("env", []) if e["name"] not in names] + ours
    c["envFrom"] = [{"secretRef": {"name": s.scanner_secret_name}}] + (
        [{"secretRef": {"name": ai_secret}}] if ai_secret else [])
    mounts = [v for v in c.get("volumeMounts", []) if v["name"] != "reports"]
    c["volumeMounts"] = mounts + [{"name": "reports", "mountPath": "/reports"}]
    vols = [v for v in ps.get("volumes", []) if v["name"] != "reports"]
    ps["volumes"] = vols + [{"name": "reports", "persistentVolumeClaim": {"claimName": s.reports_pvc}}]
    return m


def _built_in_manifest(s: Settings) -> dict:
    """Fallback when no template is mounted (dev/tests); same hardening as the ConfigMap."""
    return {
        "apiVersion": "batch/v1", "kind": "Job", "metadata": {},
        "spec": {"ttlSecondsAfterFinished": 3600, "template": {"metadata": {}, "spec": {
            "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001,
                                "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{
                "name": "scanner",
                "env": [{"name": "WORK_DIR", "value": "/work"}, {"name": "HOME", "value": "/tmp"}],
                "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                    "runAsNonRoot": True, "runAsUser": 10001, "capabilities": {"drop": ["ALL"]}},
                "resources": {"requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"cpu": "2", "memory": "2Gi"}},
                "volumeMounts": [{"name": "work", "mountPath": "/work"}, {"name": "tmp", "mountPath": "/tmp"}],
            }],
            "volumes": [{"name": "work", "emptyDir": {"sizeLimit": "2Gi"}}, {"name": "tmp", "emptyDir": {"sizeLimit": "1Gi"}}],
        }}},
    }


class LocalLauncher:
    """Dev/test launcher: runs the worker as a subprocess."""

    def __init__(self, settings: Settings, popen=subprocess.Popen):
        self.s, self._popen = settings, popen
        self.launched: list[tuple[str, int]] = []
        self.deleted: list[str] = []
        self.procs: dict[str, object] = {}

    def launch(self, spec: JobSpec, secret_env: dict[str, str] | None = None) -> str:
        name = job_name_for(spec.review_id, spec.attempt)
        self.launched.append((spec.review_id, spec.attempt))
        cmd = shlex.split(self.s.local_worker_cmd) + ["--review-id", spec.review_id]
        env = {**os.environ, **{e["name"]: e["value"] for e in contract_env(spec)},
               "REPORTS_DIR": self.s.reports_dir, "REVIEW_API_URL": os.environ.get("REVIEW_API_URL", "http://localhost:8001"),
               **(secret_env or {})}  # AI key: env only, never argv
        try:
            self.procs[name] = self._popen(cmd, cwd=self.s.local_worker_cwd or None, env=env)
        except FileNotFoundError:
            log.warning("local worker command not found: %s", cmd)
        return name

    def delete(self, job_name: str) -> None:
        self.deleted.append(job_name)
        p = self.procs.pop(job_name, None)
        if p is not None and hasattr(p, "terminate"):
            p.terminate()

    def cleanup(self, review_id: str) -> None:
        pass

    def status(self, job_name: str) -> str:
        p = self.procs.get(job_name)
        if p is None:
            return "missing"
        rc = p.poll() if hasattr(p, "poll") else None
        return "active" if rc is None else "succeeded" if rc == 0 else "failed"


class KubernetesJobLauncher:
    def __init__(self, settings: Settings, batch_api=None, core_api=None, sleep=time.sleep):
        self.s, self.sleep = settings, sleep
        if batch_api is None or core_api is None:
            from kubernetes import client, config
            config.load_incluster_config()
            batch_api = batch_api or client.BatchV1Api()
            core_api = core_api or client.CoreV1Api()
        self.api, self.core = batch_api, core_api

    def _create(self, body: dict):
        """Create; if a same-named Job from an earlier budget lingers, delete it and retry (<= ~30s)."""
        for i in range(16):
            try:
                return self.api.create_namespaced_job(namespace=self.s.k8s_namespace, body=body)
            except Exception as e:  # kubernetes.client.ApiException
                if getattr(e, "status", None) != 409 or i == 15:
                    raise
                if i == 0:
                    self.delete(body["metadata"]["name"])
                self.sleep(2)

    def launch(self, spec: JobSpec, secret_env: dict[str, str] | None = None) -> str:
        name = job_name_for(spec.review_id, spec.attempt)
        sec = f"{name}-ai" if secret_env else None
        job = self._create(build_job_manifest(self.s, spec, sec))
        if secret_env:
            uid = getattr(getattr(job, "metadata", None), "uid", None)
            meta = {"name": sec, "namespace": self.s.k8s_namespace,
                    "labels": {SECRET_LABEL: "true", "review-id": spec.review_id}}
            if uid:
                meta["ownerReferences"] = [{"apiVersion": "batch/v1", "kind": "Job", "name": name, "uid": uid,
                                            "blockOwnerDeletion": False}]
            body = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": meta, "stringData": secret_env}
            try:
                self.core.create_namespaced_secret(namespace=self.s.k8s_namespace, body=body)
            except Exception as e:
                if getattr(e, "status", None) != 409:
                    raise
                self.core.replace_namespaced_secret(name=sec, namespace=self.s.k8s_namespace, body=body)
        return name

    def delete(self, job_name: str) -> None:
        """Delete a Job and its pods (watchdog relaunch, pause, cancel). Missing Job is fine."""
        try:
            self.api.delete_namespaced_job(name=job_name, namespace=self.s.k8s_namespace,
                                           propagation_policy="Background")
        except Exception as e:
            if getattr(e, "status", None) != 404:
                raise

    def cleanup(self, review_id: str) -> None:
        """Delete the per-attempt AI Secrets by their known names (RBAC: secrets delete only, no list).
        ownerReferences to the Job are the GC fallback."""
        for n in range(1, MAX_ATTEMPTS + 1):
            try:
                self.core.delete_namespaced_secret(name=f"{job_name_for(review_id, n)}-ai", namespace=self.s.k8s_namespace)
            except Exception as e:  # noqa: BLE001
                if getattr(e, "status", None) != 404:
                    log.warning("could not delete AI secret for %s attempt %d", review_id, n)

    def ready(self) -> None:
        """readyz: the k8s API answers and RBAC allows `get jobs` (a 404 for a probe name is fine)."""
        try:
            self.api.read_namespaced_job(name="readyz-probe", namespace=self.s.k8s_namespace)
        except Exception as e:
            if getattr(e, "status", None) != 404:
                raise

    def status(self, job_name: str) -> str:
        try:
            st = self.api.read_namespaced_job(name=job_name, namespace=self.s.k8s_namespace).status
        except Exception as e:
            if getattr(e, "status", None) == 404:
                return "missing"
            raise
        if getattr(st, "succeeded", None):
            return "succeeded"
        if getattr(st, "failed", None):
            return "failed"  # backoffLimit 0 / activeDeadlineSeconds exceeded
        return "active"


def make_launcher(settings: Settings) -> JobLauncher:
    if settings.job_launcher == "kubernetes":
        return KubernetesJobLauncher(settings)
    if settings.job_launcher == "local":
        return LocalLauncher(settings)
    raise ValueError(f"unknown JOB_LAUNCHER {settings.job_launcher!r}")
