# Root: delegates to each component (make -C services/<dir> test|build). Contracts stay a root target.
SERVICES := frontend gateway review-api scanner-worker
.PHONY: test build contracts local-k8s local-k8s-down local-k8s-status
# test-%/build-% must NOT be .PHONY: GNU make skips pattern-rule search for phony targets (they would be no-ops).
test: $(addprefix test-,$(SERVICES))
build: $(addprefix build-,$(SERVICES))
test-%:
	$(MAKE) -C services/$* test
build-%:
	$(MAKE) -C services/$* build
contracts:
	openapi-spec-validator services/contracts/gateway.openapi.yaml services/contracts/review-api.openapi.yaml
	python3 -c "import json,jsonschema,glob; [jsonschema.Draft202012Validator.check_schema(json.load(open(f))) for f in glob.glob('services/contracts/*.schema.json')]; print('schemas ok')"

# Local full stack on k3d (Docker Desktop; Windows: run in WSL2). docs/local-k3d.md. ARGS e.g. ARGS=--no-argocd / --yes
local-k8s:
	infra/bootstrap/local/k3d.sh $(ARGS) up
local-k8s-down:
	infra/bootstrap/local/k3d.sh $(ARGS) down
local-k8s-status:
	infra/bootstrap/local/k3d.sh $(ARGS) status
