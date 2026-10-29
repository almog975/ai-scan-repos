# Jenkins agent "tools" container for the ai-scan-agent pod template (deploy/jenkins/values.yaml).
# node 20 + python 3 (venv/pip) + git/make + trivy + kustomize + crane. Image builds happen in the separate
# kaniko container, so no Docker daemon is needed here. Build once and push to the registry, e.g.:
#   docker build -t registry.registry:5000/ai-scan/jenkins-agent:<sha> ci/agent
FROM node:20.18-bookworm-slim

ARG TRIVY_VERSION=0.76.1
ARG KUSTOMIZE_VERSION=5.4.3
ARG CRANE_VERSION=0.20.2
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git make python3 python3-venv python3-pip \
 && rm -rf /var/lib/apt/lists/*

# postgresql-client-16 (psql, pg_dump >= server major) for ci/db-parity.sh against the postgres sidecar. PGDG repo.
# hadolint ignore=DL3008
RUN install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
 && rm -rf /var/lib/apt/lists/*

# Pinned releases, verified against the upstream checksum files.
WORKDIR /tmp/dl
RUN curl -fsSLO "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" \
 && curl -fsSL "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_checksums.txt" | grep " trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz$" | sha256sum -c - \
 && tar -xzf "trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" -C /usr/local/bin trivy \
 && curl -fsSLO "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" \
 && curl -fsSL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/checksums.txt" | grep " kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz$" | sha256sum -c - \
 && tar -xzf "kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" -C /usr/local/bin kustomize \
 && curl -fsSLO "https://github.com/google/go-containerregistry/releases/download/v${CRANE_VERSION}/go-containerregistry_Linux_x86_64.tar.gz" \
 && curl -fsSL "https://github.com/google/go-containerregistry/releases/download/v${CRANE_VERSION}/checksums.txt" | grep " go-containerregistry_Linux_x86_64.tar.gz$" | sha256sum -c - \
 && tar -xzf go-containerregistry_Linux_x86_64.tar.gz -C /usr/local/bin crane \
 && rm -rf /tmp/dl

# Jenkins kubernetes plugin runs agent containers as uid 1000 (the node image's "node" user).
WORKDIR /home/node
USER 1000
