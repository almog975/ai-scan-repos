FROM python:3.12-slim
ARG KUBECTL_VERSION=v1.31.2
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client curl ca-certificates \
 && curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl \
 && chmod +x /usr/local/bin/kubectl && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/installer
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app
COPY scripts ./scripts
COPY terraform ./terraform
RUN useradd -u 10001 -M -s /usr/sbin/nologin installer && chmod +x scripts/*.sh
USER 10001
EXPOSE 8088
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
