COMPONENT := gateway
include ../mk/common.mk
PY ?= python3

.PHONY: test build
# Unit tests on the agent (never in the Dockerfile). Exit code = pytest's; JUnit for Jenkins' junit step.
test:
	@$(PY) -c "import pytest" 2>/dev/null || { echo "[$(COMPONENT)] pytest not installed for $(PY) (pip install -r requirements-dev.txt)" >&2; exit 2; }
	@mkdir -p $(REPORTS)
	$(PY) -m pytest -q -o addopts= -p no:cacheprovider --junitxml=$(REPORTS)/junit.xml tests

# Image tagged with the git sha (never latest).
build: require-docker
	$(DOCKER) build --pull -t $(IMAGE) --label org.opencontainers.image.revision=$(GIT_SHA) .
