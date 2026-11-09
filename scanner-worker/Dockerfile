# gitleaks is built from source with a current Go toolchain: every released binary (up to 8.30.1) is linked
# against a Go stdlib with a fixable CRITICAL (CVE-2025-68121, crypto/tls), which fails the CI trivy gate.
# Integrity: `go install module@version` checks go.sum against the Go checksum database (sum.golang.org).
FROM golang:1.27.3-bookworm AS gitleaks
ARG GITLEAKS_VERSION=8.30.1
RUN CGO_ENABLED=0 GOBIN=/out go install -trimpath \
      -ldflags "-s -w -X github.com/zricethezav/gitleaks/v8/version.Version=${GITLEAKS_VERSION}" \
      "github.com/zricethezav/gitleaks/v8@v${GITLEAKS_VERSION}" \
 && /out/gitleaks version

# Debian 12 base pinned so the apt versions below resolve (hadolint DL3008).
FROM python:3.12-slim-bookworm
ARG TRIVY_VERSION=0.76.1
# sha256 from trivy_${TRIVY_VERSION}_checksums.txt (official release asset)
ARG TRIVY_SHA256=c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOME=/tmp
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git=1:2.39.5-0+deb12u3 ca-certificates=20250419~deb12u2 curl=7.88.1-10+deb12u15 \
 && curl -fsSL -o /tmp/tv.tgz "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" \
 && echo "${TRIVY_SHA256}  /tmp/tv.tgz" | sha256sum -c - \
 && tar -xzf /tmp/tv.tgz -C /usr/local/bin trivy \
 && rm -f /tmp/*.tgz && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY --from=gitleaks /out/gitleaks /usr/local/bin/gitleaks
WORKDIR /opt/scanner
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scanner ./scanner
RUN groupadd -g 10001 scanner && useradd -u 10001 -g scanner -M -s /usr/sbin/nologin scanner \
 && mkdir -p /work /reports && chown 10001:10001 /work /reports
USER 10001
# No source code is baked in: repos are cloned at runtime into /work (emptyDir) and deleted.
EXPOSE 8002
ENTRYPOINT ["python", "-m", "scanner.run"]
