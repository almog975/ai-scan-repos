FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd -g 10001 app && useradd -u 10001 -g app -M -s /usr/sbin/nologin app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
RUN mkdir -p /reports && chown -R 10001:10001 /reports
USER 10001
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=2)" || exit 1
# Run migrations, then serve. (In K8s, migrations can move to an init container.)
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn --factory app.main:get_app --host 0.0.0.0 --port 8001"]
