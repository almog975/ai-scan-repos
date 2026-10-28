FROM python:3.12-slim
ARG GITLEAKS_VERSION=8.21.2
ARG TRIVY_VERSION=0.58.1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOME=/tmp
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl \
 && curl -fsSL -o /tmp/gl.tgz https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz \
 && tar -xzf /tmp/gl.tgz -C /usr/local/bin gitleaks \
 && curl -fsSL -o /tmp/tv.tgz https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz \
 && tar -xzf /tmp/tv.tgz -C /usr/local/bin trivy \
 && rm -f /tmp/*.tgz && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/scanner
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY scanner ./scanner
RUN groupadd -g 10001 scanner && useradd -u 10001 -g scanner -M -s /usr/sbin/nologin scanner \
 && mkdir -p /work /reports && chown 10001:10001 /work /reports
USER 10001
# No source code is baked in: repos are cloned at runtime into /work (emptyDir) and deleted.
EXPOSE 8002
ENTRYPOINT ["python", "-m", "scanner.run"]
