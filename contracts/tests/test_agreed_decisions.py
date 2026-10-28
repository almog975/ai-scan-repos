"""Tests for the decisions AI Scan Planner accepted (review notes 1-17).

Anything the contracts don't reflect yet is xfail(strict=True): once Coder lands the
change, the test XPASSes, the run fails, and the xfail marker must be removed.
Fixtures in fixtures/pending_invalid/ must be rejected once the decision is in.
"""
import json

import pytest

from conftest import FIXTURES_DIR, load_schema, load_spec

PENDING = pytest.mark.xfail(strict=True, reason="agreed decision not yet in contracts")
PUBLIC = {"healthz", "readyz", "metrics"}
API = load_spec("review-api.openapi.yaml")
GW = load_spec("gateway.openapi.yaml")
METHODS = {"get", "post", "put", "patch", "delete"}


def ops(spec):
    for path, item in spec["paths"].items():
        for m, op in item.items():
            if m in METHODS and isinstance(op, dict):
                yield path, m, op


def resolve(spec, node):
    while isinstance(node, dict) and "$ref" in node and node["$ref"].startswith("#/"):
        cur = spec
        for part in node["$ref"][2:].split("/"):
            cur = cur[part.replace("~1", "/").replace("~0", "~")]
        node = cur
    return node


def schema_of(name):
    return API["components"]["schemas"][name]


# ---------- JSON Schema fixtures that must be rejected once decisions land ----------
@pytest.mark.parametrize("path", sorted((FIXTURES_DIR / "pending_invalid").glob("*.json")), ids=lambda p: p.stem)
def test_pending_invalid_fixture_rejected(path, validator_for):
    v = validator_for(f"{path.name.split('__')[0]}.schema.json")
    assert not v.is_valid(json.loads(path.read_text()))


# ---------- additionalProperties:false ----------
@pytest.mark.parametrize("name", ["report.schema.json", "checkpoint.schema.json", "progress.schema.json"])
def test_schema_closed(name):
    assert load_schema(name).get("additionalProperties") is False


@pytest.mark.parametrize("name", ["ReviewCreate", "AIProviderWrite", "Settings", "Review", "Error"])
def test_api_schema_closed(name):
    assert schema_of(name).get("additionalProperties") is False


# ---------- uuid / sha formats ----------
@pytest.mark.parametrize("name", ["report.schema.json", "checkpoint.schema.json", "progress.schema.json"])
def test_review_id_uuid(name):
    assert load_schema(name)["properties"]["review_id"].get("format") == "uuid"


@pytest.mark.parametrize("name", ["Review", "AIProvider", "ReviewAttempt"])
def test_api_ids_uuid(name):
    assert schema_of(name)["properties"]["id"].get("format") == "uuid"


def test_commit_sha_pattern():
    assert schema_of("Review")["properties"]["commit_sha"].get("pattern") == "^[a-f0-9]{40}$"


# ---------- budget_no + attempt 1-5 ----------
@pytest.mark.parametrize("name", ["checkpoint.schema.json", "progress.schema.json", "report.schema.json"])
def test_budget_no_in_schema(name):
    s = load_schema(name)
    assert "budget_no" in s["properties"] and "budget_no" in s["required"]
    assert s["properties"]["budget_no"].get("minimum") == 1


@pytest.mark.parametrize("name", ["Review", "ReviewAttempt", "Progress"])
def test_budget_no_and_attempt_bounds_in_api(name):
    p = schema_of(name)["properties"]
    assert "budget_no" in p
    att = p.get("attempt") or p.get("n")
    assert att.get("minimum") == 1 and att.get("maximum") == 5


# ---------- required timestamps / error fields ----------
def test_report_timestamps_required():
    assert {"started_at", "finished_at"} <= set(load_schema("report.schema.json")["required"])


def test_review_timestamps():
    s = schema_of("Review")
    assert {"created_at", "updated_at"} <= set(s["required"])


@pytest.mark.parametrize("spec", [API, GW], ids=["review-api", "gateway"])
def test_error_fields_required(spec):
    err = spec["components"]["schemas"]["Error"]
    assert "request_id" in err["required"]
    assert {"code", "message"} <= set(err["properties"]["error"]["required"])


# ---------- ack_cloud -> 422 cloud_ack_required ----------
def test_create_review_documents_422():
    resp = API["paths"]["/reviews"]["post"]["responses"]
    assert "422" in resp
    assert "cloud_ack_required" in json.dumps(resolve(API, resp["422"])).lower()


# ---------- 401 / 403 ----------
def _non_public(spec):
    return [(p, m, o) for p, m, o in ops(spec) if o.get("operationId") not in PUBLIC and o.get("security") != []]


def test_every_protected_route_has_401():
    missing = [f"{m.upper()} {p}" for p, m, o in _non_public(API) if "401" not in o["responses"]]
    assert not missing, missing


def test_admin_routes_have_403():
    missing = [f"{m.upper()} {p}" for p, m, o in ops(API) if p.startswith("/admin") and "403" not in o["responses"]]
    assert not missing, missing


def test_admin_role_documented():
    text = json.dumps({p: i for p, i in API["paths"].items() if p.startswith("/admin")}).lower()
    assert "users.role" in text or "admin" in json.dumps(API["components"].get("securitySchemes", {})).lower()


# ---------- ProviderId param ----------
@pytest.mark.parametrize("path", ["/admin/ai-providers/{id}", "/admin/ai-providers/{id}/test"])
def test_provider_routes_use_provider_id(path):
    refs = [x.get("$ref", "") for x in API["paths"][path].get("parameters", [])]
    assert "#/components/parameters/ProviderId" in refs


# ---------- PATCH schema ----------
def test_patch_provider_uses_patch_schema():
    body = API["paths"]["/admin/ai-providers/{id}"]["patch"]["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"] != "#/components/schemas/AIProviderWrite"
    assert not resolve(API, body).get("required")


# ---------- pagination ----------
def test_list_reviews_page_size_and_total():
    names = {x.get("name") for x in API["paths"]["/reviews"]["get"]["parameters"]}
    assert {"page", "page_size"} <= names or {"page", "limit"} <= names
    assert "total" in schema_of("ReviewList")["required"]


@pytest.mark.parametrize("path", ["/reviews/{id}/files", pytest.param("/reviews/{id}/attempts", marks=pytest.mark.xfail(strict=True, reason="decision: /attempts is NOT paginated (max 5 rows per budget)"))])
def test_subresource_lists_paginated(path):
    names = {resolve(API, x).get("name") for x in API["paths"][path]["get"].get("parameters", [])}
    names |= {resolve(API, x).get("name") for x in API["paths"][path].get("parameters", [])}
    assert "page" in names or "cursor" in names


# ---------- SSE events ----------
def test_sse_events_documented():
    text = json.dumps(API["paths"]["/reviews/{id}/progress"]).lower()
    for ev in ("progress", "state", "done"):
        assert f"event: {ev}" in text or f'"{ev}"' in text, ev
    assert "done" in text and "state" in text
