# Shared by every component Makefile. Non-interactive, no silent skips: a missing tool fails with a clear message.
# Image tag = git sha (Jenkins passes GIT_SHA; locally it is read from git).
GIT_SHA  ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
REGISTRY ?= ai-scan
IMAGE    ?= $(REGISTRY)/$(COMPONENT):$(GIT_SHA)
DOCKER   ?= docker
REPORTS  ?= reports

.PHONY: help require-docker
help:
	@echo "$(COMPONENT): make test (JUnit -> $(REPORTS)/junit.xml) | make build (image $(IMAGE))"

require-docker:
	@command -v $(DOCKER) >/dev/null 2>&1 || { echo "[$(COMPONENT)] '$(DOCKER)' not found: make build needs docker" >&2; exit 2; }
