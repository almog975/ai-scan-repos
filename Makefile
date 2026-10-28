SERVICES := frontend gateway review-api scanner-worker
.PHONY: test contracts $(SERVICES)
test: $(SERVICES)
$(SERVICES):
	$(MAKE) -C $@ test
contracts:
	openapi-spec-validator contracts/gateway.openapi.yaml contracts/review-api.openapi.yaml
	python3 -c "import json,jsonschema,glob; [jsonschema.Draft202012Validator.check_schema(json.load(open(f))) for f in glob.glob('contracts/*.schema.json')]; print('schemas ok')"
