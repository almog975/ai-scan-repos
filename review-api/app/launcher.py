"""Job launchers: start a scanner-worker run for a review.

Secrets are NEVER put in args: the K8s Job gets them via envFrom a Secret,
the local launcher via the inherited process environment.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import uuid
from typing import Protocol

from .config import Settings

log = logging.getLogger(__name__)


class JobLauncher(Protocol):
    def launch(self, review_id: str, attempt: int, secret_env: dict[str, str] | None = None) -> str: ...

    def cleanup(self, review_id: str) -> None: ...


def job_name_for(review_id: str, attempt: int) -> str:
    return f"scan-{review_id[:12]}-a{attempt}-{uuid.uuid4().hex[:5]}"


class LocalLauncher:
    """Dev/test launcher: runs the worker as a subprocess."""

    def __init__(self, settings: Settings, popen=subprocess.Popen):
        self.s = settings
        self._popen = popen
        self.launched: list[tuple[str, int]] = []

    def launch(self, review_id: str, attempt: int, secret_env: dict[str, str] | None = None) -> str:
        name = job_name_for(review_id, attempt)
        self.launched.append((review_id, attempt))
        cmd = shlex.split(self.s.local_worker_cmd) + ["--review-id", review_id]
        env = {**os.environ, "REVIEW_ID": review_id, "ATTEMPT": str(attempt),
               "REPORTS_DIR": self.s.reports_dir, "REVIEW_API_URL": os.environ.get("REVIEW_API_URL", "http://localhost:8001"),
               **(secret_env or {})}  # per-review AI key: env only, never argv
        try:
            self._popen(cmd, cwd=self.s.local_worker_cwd or None, env=env)
        except FileNotFoundError:
            log.warning("local worker command not found: %s", cmd)
        return name

    def cleanup(self, review_id: str) -> None:
        pass  # env dies with the subprocess


def ai_secret_name(job_name: str) -> str:
    return f"{job_name}-ai"


def build_job_manifest(s: Settings, review_id: str, attempt: int, name: str, ai_secret: str | None = None) -> dict:
    """batch/v1 Job manifest (plain dict) for one scanner attempt."""
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": s.k8s_namespace,
                     "labels": {"app": "scanner-worker", "review-id": review_id[:63], "attempt": str(attempt)}},
        "spec": {
            "backoffLimit": 0,  # retries are owned by the watchdog, not by K8s
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "metadata": {"labels": {"app": "scanner-worker", "review-id": review_id[:63]}},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": s.scanner_service_account,
                    "automountServiceAccountToken": False,
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                                        "fsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [{
                        "name": "scanner",
                        "image": s.scanner_image,
                        "args": ["--review-id", review_id],
                        "env": [
                            {"name": "REVIEW_ID", "value": review_id},
                            {"name": "ATTEMPT", "value": str(attempt)},
                            {"name": "REPORTS_DIR", "value": "/reports"},
                            {"name": "WORK_DIR", "value": "/work"},
                            {"name": "REVIEW_API_URL", "value": s.review_api_internal_url},
                            {"name": "HOME", "value": "/tmp"},
                        ],
                        # INTERNAL_API_TOKEN + AI provider keys come from a Secret
                        # Later entries win: the per-review AI secret overrides defaults.
                        "envFrom": [{"secretRef": {"name": s.scanner_secret_name}}]
                                   + ([{"secretRef": {"name": ai_secret}}] if ai_secret else []),
                        "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                            "runAsNonRoot": True, "runAsUser": 10001,
                                            "capabilities": {"drop": ["ALL"]}},
                        "resources": {"requests": {"cpu": "250m", "memory": "512Mi"},
                                      "limits": {"cpu": "2", "memory": "2Gi"}},
                        "volumeMounts": [
                            {"name": "work", "mountPath": "/work"},
                            {"name": "tmp", "mountPath": "/tmp"},
                            {"name": "reports", "mountPath": "/reports"},
                        ],
                    }],
                    "volumes": [
                        {"name": "work", "emptyDir": {"sizeLimit": "2Gi"}},
                        {"name": "tmp", "emptyDir": {"sizeLimit": "1Gi"}},
                        {"name": "reports", "persistentVolumeClaim": {"claimName": s.reports_pvc}},
                    ],
                },
            },
        },
    }


SECRET_LABEL = "ai-scan-repos/per-review-secret"


class KubernetesJobLauncher:
    """Creates the Job, then (if a user AI key exists) a per-review Secret owned by the Job.

    The Secret is garbage-collected with the Job (ownerReference + ttlSecondsAfterFinished)
    and also deleted explicitly by cleanup() when the review completes/fails.
    The pod waits in CreateContainerConfigError until the Secret exists (seconds).
    """

    def __init__(self, settings: Settings, batch_api=None, core_api=None):
        self.s = settings
        if batch_api is None or core_api is None:
            from kubernetes import client, config
            config.load_incluster_config()
            batch_api = batch_api or client.BatchV1Api()
            core_api = core_api or client.CoreV1Api()
        self.api, self.core = batch_api, core_api

    def launch(self, review_id: str, attempt: int, secret_env: dict[str, str] | None = None) -> str:
        name = job_name_for(review_id, attempt)
        sec = ai_secret_name(name) if secret_env else None
        job = self.api.create_namespaced_job(namespace=self.s.k8s_namespace,
                                             body=build_job_manifest(self.s, review_id, attempt, name, sec))
        if secret_env:
            uid = getattr(getattr(job, "metadata", None), "uid", None)
            meta = {"name": sec, "namespace": self.s.k8s_namespace,
                    "labels": {SECRET_LABEL: "true", "review-id": review_id[:63]}}
            if uid:
                meta["ownerReferences"] = [{"apiVersion": "batch/v1", "kind": "Job", "name": name, "uid": uid,
                                            "blockOwnerDeletion": False}]
            self.core.create_namespaced_secret(namespace=self.s.k8s_namespace, body={
                "apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": meta, "stringData": secret_env})
        return name

    def cleanup(self, review_id: str) -> None:
        try:
            self.core.delete_collection_namespaced_secret(
                namespace=self.s.k8s_namespace, label_selector=f"{SECRET_LABEL}=true,review-id={review_id[:63]}")
        except Exception:  # noqa: BLE001 - GC via ownerReference is the fallback
            log.warning("could not delete per-review secrets for %s", review_id)


def make_launcher(settings: Settings) -> JobLauncher:
    if settings.job_launcher == "kubernetes":
        return KubernetesJobLauncher(settings)
    if settings.job_launcher == "local":
        return LocalLauncher(settings)
    raise ValueError(f"unknown JOB_LAUNCHER {settings.job_launcher!r}")
