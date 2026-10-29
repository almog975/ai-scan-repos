# Root: delegates to each component (make -C <dir> test|build). Contracts stay a root target.
SERVICES := frontend gateway review-api scanner-worker
.PHONY: test build contracts $(addprefix test-,$(SERVICES)) $(addprefix build-,$(SERVICES))
test: $(addprefix test-,$(SERVICES))
build: $(addprefix build-,$(SERVICES))
test-%:
	$(MAKE) -C $* test
build-%:
	$(MAKE) -C $* build
contracts:
	openapi-spec-validator contracts/gateway.openapi.yaml contracts/review-api.openapi.yaml
	python3 -c "import json,jsonschema,glob; [jsonschema.Draft202012Validator.check_schema(json.load(open(f))) for f in glob.glob('contracts/*.schema.json')]; print('schemas ok')"
