COMPONENT := frontend
include ../mk/common.mk
NPM ?= npm

.PHONY: deps test dist build
deps:
	@command -v $(NPM) >/dev/null 2>&1 || { echo "[frontend] npm not found (node >= 20)" >&2; exit 2; }
	@[ -d node_modules ] || CI=true $(NPM) ci --no-audit --no-fund

# vitest on the agent (never in the Dockerfile); JUnit for Jenkins' junit step.
test: deps
	CI=true npx vitest run --reporter=default --reporter=junit --outputFile.junit=$(REPORTS)/junit.xml

# Static bundle only (tsc + vite); the footer shows VITE_APP_VERSION.
dist: deps
	CI=true VITE_APP_VERSION=$(GIT_SHA) $(NPM) run build

# nginx-unprivileged image tagged with the git sha; the same sha is baked into the footer badge.
build: require-docker
	$(DOCKER) build --pull -t $(IMAGE) --build-arg VITE_APP_VERSION=$(GIT_SHA) .
