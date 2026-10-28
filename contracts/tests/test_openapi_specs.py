"""Static validation of both OpenAPI specs + schemathesis wiring.

Live API tests run only when REVIEW_API_BASE_URL / GATEWAY_BASE_URL is set
(optionally with AISCAN_INTERNAL_TOKEN, AISCAN_USER_ID, AISCAN_SESSION_COOKIE).
"""
import os

import pytest
import schemathesis
from openapi_spec_validator import validate
from openapi_spec_validator.readers import read_from_filename

from conftest import BASE_URL_ENV, CONTRACTS_DIR, SPEC_FILES, load_spec

NEW_ENDPOINTS = {
    "/ai-providers": ["get"],
    "/repos/check": ["get"],
    "/reviews/{id}/files": ["get"],
    "/reviews/{id}/retry": ["post"],
    "/reviews/{id}/attempts": ["get"],
    "/admin/settings": ["get"],
}


@pytest.mark.parametrize("name", SPEC_FILES)
def test_spec_is_valid_openapi(name):
    spec_dict, base_uri = read_from_filename(str(CONTRACTS_DIR / name))
    validate(spec_dict, base_uri=base_uri)  # resolves cross-file $refs


@pytest.mark.parametrize("name", SPEC_FILES)
def test_operation_ids_unique(name):
    ids = [op["operationId"] for p in load_spec(name)["paths"].values() if isinstance(p, dict)
           for m, op in p.items() if isinstance(op, dict) and "operationId" in op]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("path,methods", NEW_ENDPOINTS.items())
def test_new_endpoints_present(path, methods):
    review = load_spec("review-api.openapi.yaml")["paths"]
    gateway = load_spec("gateway.openapi.yaml")["paths"]
    assert path in review
    for m in methods:
        assert "200" in review[path][m]["responses"] or "202" in review[path][m]["responses"]
    assert "/api" + path in gateway, "gateway must expose /api" + path


@pytest.mark.parametrize("name", SPEC_FILES)
def test_schemathesis_loads(name):
    schema = schemathesis.openapi.from_path(str(CONTRACTS_DIR / name))
    assert len(list(schema.get_all_operations())) > 0


def _live_schema(name):
    url = os.environ.get(BASE_URL_ENV[name])
    if not url:
        return None
    schema = schemathesis.openapi.from_path(str(CONTRACTS_DIR / name))
    schema.config.update(base_url=url, headers=_headers())
    return schema


def _headers():
    h = {}
    if os.environ.get("AISCAN_INTERNAL_TOKEN"):
        h["X-Internal-Token"] = os.environ["AISCAN_INTERNAL_TOKEN"]
    if os.environ.get("AISCAN_USER_ID"):
        h["X-User-Id"] = os.environ["AISCAN_USER_ID"]
    if os.environ.get("AISCAN_SESSION_COOKIE"):
        h["Cookie"] = f"aiscan_session={os.environ['AISCAN_SESSION_COOKIE']}"
    return h


# ---- Live schemathesis tests (generated only when the base URL env var is set) ----
_LIVE = {name: _live_schema(name) for name in SPEC_FILES}


def _make_live(name):
    schema = _LIVE[name]
    if schema is None:
        @pytest.mark.parametrize("_", [name])
        def _skipped(_):
            pytest.skip(f"{BASE_URL_ENV[name]} not set")
        return _skipped, _skipped

    @schema.parametrize()
    def _conformance(case):
        case.call_and_validate()

    return _conformance, schema.as_state_machine().TestCase


test_live_review_api, TestLiveReviewApiStateful = _make_live("review-api.openapi.yaml")
test_live_gateway, TestLiveGatewayStateful = _make_live("gateway.openapi.yaml")
if _LIVE["review-api.openapi.yaml"] is None:
    del TestLiveReviewApiStateful
    test_live_review_api_stateful = test_live_review_api
if _LIVE["gateway.openapi.yaml"] is None:
    del TestLiveGatewayStateful
    test_live_gateway_stateful = test_live_gateway
