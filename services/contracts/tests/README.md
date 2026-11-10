# Contract tests

```bash
pip install -r services/contracts/tests/requirements-dev.txt
pytest services/contracts/tests
```

- `test_json_schemas.py` – checks report/finding/checkpoint schemas are valid Draft 2020-12, accept `fixtures/valid/*` and reject `fixtures/invalid/*`. Fixture naming: `<schema>__<case>.json`.
- `test_openapi_specs.py` – validates both OpenAPI specs (incl. cross-file `$ref`s), unique operationIds, presence of the Phase 0 endpoints, and that schemathesis can load them.
- `test_agreed_decisions.py` – one test per accepted review decision (closed schemas, uuid/sha, budget_no+attempt, timestamps, error fields, 422 cloud_ack_required, 401/403, ProviderId, PATCH schema, pagination, SSE events, explicit gateway routes, session vs internal-token security, Error.code enum, size_mb, /attempts budget_no filter). Not-yet-landed decisions are `xfail(strict=True)`: when the contract changes the test XPASSes and fails – remove its marker. `fixtures/pending_invalid/` holds fixtures that must be rejected once the decisions land. It is currently empty (all decisions landed), so its parametrized test reports one skip.
- Requires schemathesis 4.x (native OpenAPI 3.1).
- Live/stateful schemathesis tests are **skipped** unless a base URL is set:
  - `REVIEW_API_BASE_URL=http://localhost:8001` (+ `AISCAN_INTERNAL_TOKEN`, `AISCAN_USER_ID`)
  - `GATEWAY_BASE_URL=http://localhost:8000` (+ `AISCAN_SESSION_COOKIE`)

Ad-hoc fuzzing: `st run services/contracts/review-api.openapi.yaml --base-url $REVIEW_API_BASE_URL`.
