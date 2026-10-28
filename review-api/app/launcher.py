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
    def launch(self, review_id: str, attempt: int) -> str: ...


def job_name_for(review_id: str, attempt: int) -> str:
    return f"scan-{review_id[:12]}-a{attempt}-{uuid.uuid4().hex[:5]}"


class LocalLauncher:
    """Dev/test launcher: runs the worker as a subprocess."""

    def __init__(self, settings: Settings, popen=subprocess.Popen):
        self.s = settings
        self._popen = popen
        self.launched: list[tuple[str, int]] = []

    def launch(self, review_id: str, attempt: int) -> str:
        name = job_name_for(review_id, attempt)
        self.launched.append((review_id, attempt))
        cmd = shlex.split(self.s.local_worker_cmd) + ["--review-id", review_id]
        env = {**os.environ, "REVIEW_ID": review_id, "ATTEMPT": str(attempt),
               "REPORTS_DIR": self.s.reports_dir, "REVIEW_API_URL": os.environ.get("REVIEW_API_URL", "http://localhost:8000")}
        try:
            self._popen(cmd, cwd=self.s.local_worker_cwd or None, env=env)
        except FileNotFoundError:
            log.warning("local worker command not found: %s", cmd)
        return name


def build_job_manifest(s: Settings, review_id: str, attempt: int, name: str) -> dict:
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
                        "envFrom": [{"secretRef": {"name": s.scanner_secret_name}}],
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


class KubernetesJobLauncher:
    def __init__(self, settings: Settings, batch_api=None):
        self.s = settings
        if batch_api is None:
            from kubernetes import client, config
            config.load_incluster_config()
            batch_api = client.BatchV1Api()
        self.api = batch_api

    def launch(self, review_id: str, attempt: int) -> str:
        name = job_name_for(review_id, attempt)
        self.api.create_namespaced_job(namespace=self.s.k8s_namespace,
                                       body=build_job_manifest(self.s, review_id, attempt, name))
        return name


def make_launcher(settings: Settings) -> JobLauncher:
    if settings.job_launcher == "kubernetes":
        return KubernetesJobLauncher(settings)
    if settings.job_launcher == "local":
        return LocalLauncher(settings)
    raise ValueError(f"unknown JOB_LAUNCHER {settings.job_launcher!r}")
