"""Tests for the decisions AI Scan Planner accepted (review notes 1-17 + gateway follow-ups).

Anything the contracts don't reflect yet is xfail(strict=True): once Coder lands the
change the test XPASSes, the run fails, and the marker must be removed.
Fixtures in fixtures/pending_invalid/ must be rejected once the decision lands.
"""
import json
import re

import pytest

from conftest import FIXTURES_DIR, load_schema, load_spec

PENDING = pytest.mark.xfail(strict=True, reason="agreed decision not yet in contracts")
PUBLIC = {"healthz", "readyz", "metrics"}
METHODS = {"get", "post", "put", "patch", "delete"}
DOCS = {
    "review-api.openapi.yaml": load_spec("review-api.openapi.yaml"),
    "gateway.openapi.yaml": load_spec("gateway.openapi.yaml"),
}
for _n in ("common", "report", "checkpoint", "progress", "finding"):
    DOCS[f"{_n}.schema.json"] = load_schema(f"{_n}.schema.json")
API = DOCS["review-api.openapi.yaml"]
GW = DOCS["gateway.openapi.yaml"]


def _ptr(doc, frag):
    cur = doc
    for part in [p for p in frag.lstrip("/").split("/") if p]:
        cur = cur[part.replace("~1", "/").replace("~0", "~")]
    return cur


def deref(node, base="review-api.openapi.yaml"):
    """Follow $ref chains across contract files. Returns (node, base_file)."""
    while isinstance(node, dict) and "$ref" in node:
        f, _, frag = node["$ref"].partition("#")
        base = f or base
        node = _ptr(DOCS[base], frag)
    return node, base


def flat(node, base="review-api.openapi.yaml"):
    """Dereference and merge oneOf/anyOf branches into one keyword dict (shallow)."""
    node, base = deref(node, base)
    out = dict(node)
    for key in ("oneOf", "anyOf"):
        for br in node.get(key, []):
            out.update({k: v for k, v in flat(br, base).items() if k != "type"})
    return out


def comp(name):
    return deref(API["components"]["schemas"][name])[0]


def prop(schema, name, base="review-api.openapi.yaml"):
    return flat(schema["properties"][name], base)


def ops(spec, base):
    for path, item in spec["paths"].items():
        item, b = deref(item, base)
        for m, op in item.items():
            if m in METHODS and isinstance(op, dict):
                yield path, m, op, item, b


# ---------- JSON-Schema fixtures that must be rejected once decisions land ----------
# Put new not-yet-enforced cases in fixtures/pending_invalid/; move them to invalid/ once they XPASS.
@PENDING
@pytest.mark.parametrize("path", sorted((FIXTURES_DIR / "pending_invalid").glob("*.json")), ids=lambda p: p.stem)
def test_pending_invalid_fixture_rejected(path, validator_for):
    v = validator_for(f"{path.name.split('__')[0]}.schema.json")
    assert not v.is_valid(json.loads(path.read_text()))


# ---------- additionalProperties:false ----------
@pytest.mark.parametrize("name", ["report.schema.json", "checkpoint.schema.json", "progress.schema.json"])
def test_schema_closed(name):
    assert load_schema(name).get("additionalProperties") is False


def test_report_repo_closed():
    assert load_schema("report.schema.json")["properties"]["repo"]["additionalProperties"] is False


@pytest.mark.parametrize("name", ["ReviewCreate", "AIProviderWrite", "AIProviderPatch", "Settings", "ProviderPublic",
                                  "Review", "Error"])
def test_api_schema_closed(name):
    assert comp(name).get("additionalProperties") is False


# ---------- uuid / sha formats ----------
@pytest.mark.parametrize("name", ["report.schema.json", "checkpoint.schema.json", "progress.schema.json"])
def test_review_id_uuid(name):
    assert prop(load_schema(name), "review_id", name).get("format") == "uuid"


@pytest.mark.parametrize("name", ["Review", "AIProvider", "ReviewAttempt", "ProviderPublic"])
def test_api_ids_uuid(name):
    assert prop(comp(name), "id").get("format") == "uuid"


@pytest.mark.parametrize("param", ["ReviewId", "ProviderId"])
def test_path_params_uuid(param):
    assert flat(API["components"]["parameters"][param]["schema"]).get("format") == "uuid"


def _is_sha_pattern(pat):
    return bool(pat) and re.fullmatch(pat, "a" * 40) and not re.fullmatch(pat, "a" * 39) and not re.fullmatch(pat, "g" * 40)


def test_commit_sha_pattern():
    assert _is_sha_pattern(prop(comp("Review"), "commit_sha").get("pattern"))
    assert _is_sha_pattern(flat(load_schema("report.schema.json")["properties"]["repo"]["properties"]["commit_sha"],
                                "report.schema.json").get("pattern"))


# ---------- budget_no + attempt 1-5 ----------
@pytest.mark.parametrize("name", ["checkpoint.schema.json", "progress.schema.json",
                                  "report.schema.json"])
def test_budget_no_required_in_schema(name):
    s = load_schema(name)
    assert "budget_no" in s["required"]
    assert prop(s, "budget_no", name).get("minimum") == 1
    att = prop(s, "attempt", name)
    assert (att.get("minimum"), att.get("maximum")) == (1, 5)


@pytest.mark.parametrize("name,field", [("Review", "attempt"), ("ReviewAttempt", "n")])
def test_budget_no_and_attempt_bounds_in_api(name, field):
    s = comp(name)
    assert {"budget_no", field} <= set(s["required"])
    att = prop(s, field)
    assert (att.get("minimum"), att.get("maximum")) == (1, 5)


# ---------- required timestamps / error fields ----------
def test_report_timestamps_required():
    assert {"started_at", "finished_at"} <= set(load_schema("report.schema.json")["required"])


def test_review_timestamps():
    assert {"created_at", "updated_at"} <= set(comp("Review")["required"])


@pytest.mark.parametrize("spec", ["review-api.openapi.yaml", "gateway.openapi.yaml"])
def test_error_fields_required(spec):
    err, base = deref(DOCS[spec]["components"]["schemas"]["Error"], spec)
    inner = flat(err["properties"]["error"], base)
    assert {"code", "message"} <= set(inner["required"])
    assert "request_id" in set(inner["required"]) | set(err["required"]), "request_id must be required"


# ---------- ack_cloud -> 422 cloud_ack_required ----------
def test_create_review_documents_422():
    resp = API["paths"]["/reviews"]["post"]["responses"]
    assert "422" in resp
    assert "cloud_ack_required" in json.dumps(deref(resp["422"])[0]).lower()


# ---------- 401 / 403 ----------
def test_every_protected_route_has_401():
    missing = [f"{m.upper()} {p}" for p, m, o, *_ in ops(API, "review-api.openapi.yaml")
               if o.get("operationId") not in PUBLIC and o.get("security") != [] and "401" not in o["responses"]]
    assert not missing, missing


def test_admin_routes_have_403():
    missing = [f"{m.upper()} {p}" for p, m, o, *_ in ops(API, "review-api.openapi.yaml")
               if p.startswith("/admin") and "403" not in o["responses"]]
    assert not missing, missing


def test_admin_role_documented():
    assert "users.role" in GW["info"]["description"]
    assert "403" in API["info"]["description"] and "/admin" in API["info"]["description"]


# ---------- ProviderId param ----------
@pytest.mark.parametrize("path", ["/admin/ai-providers/{id}", "/admin/ai-providers/{id}/test"])
def test_provider_routes_use_provider_id(path):
    refs = [x.get("$ref", "") for x in API["paths"][path].get("parameters", [])]
    assert "#/components/parameters/ProviderId" in refs
    assert "#/components/parameters/ReviewId" not in refs


# ---------- PATCH schema ----------
def test_patch_provider_uses_patch_schema():
    body = API["paths"]["/admin/ai-providers/{id}"]["patch"]["requestBody"]["content"]["application/json"]["schema"]
    s = deref(body)[0]
    assert body["$ref"].endswith("/AIProviderPatch")
    assert not s.get("required") and s.get("minProperties") == 1


# ---------- pagination ----------
def _param_names(path, method="get"):
    item = API["paths"][path]
    params = item.get("parameters", []) + item[method].get("parameters", [])
    return {deref(x)[0].get("name") for x in params}


def test_list_reviews_page_size_and_total():
    assert {"page", "page_size"} <= _param_names("/reviews")
    resp = API["paths"]["/reviews"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert {"items", "page", "page_size", "total"} <= set(deref(resp)[0]["required"])


def test_files_paginated():
    assert {"page", "page_size"} <= _param_names("/reviews/{id}/files")


def test_attempts_unpaginated_with_budget_filter():
    path = "/reviews/{id}/attempts"
    assert not {"page", "page_size"} & _param_names(path)
    item = API["paths"][path]
    params = [deref(x)[0] for x in item.get("parameters", []) + item["get"].get("parameters", [])]
    bn = [x for x in params if x.get("name") == "budget_no"]
    assert len(bn) == 1 and bn[0]["in"] == "query" and not bn[0].get("required", False)
    sch = flat(bn[0]["schema"])
    assert sch.get("type") == "integer" and sch.get("minimum") == 1
    resp = deref(item["get"]["responses"]["200"]["content"]["application/json"]["schema"])[0]
    assert resp.get("type") == "array"
    assert resp.get("maxItems") == 5 or resp["items"].get("$ref", "").endswith("/ReviewAttempt")


# ---------- SSE events ----------
def test_sse_events_documented():
    op = API["paths"]["/reviews/{id}/progress"]["get"]
    assert "text/event-stream" in op["responses"]["200"]["content"]
    names = set(re.findall(r"`(?:event:\s*)?(\w+)`\s*[–-]", op.get("description", "")))
    assert {"progress", "state", "done"} <= names, names


# ---------- gateway follow-ups ----------
def test_gateway_has_no_catch_all():
    templ = [p for p in GW["paths"] if re.search(r"\{path\}|\{.*\*.*\}|\{rest\}", p)]
    assert not templ, templ


def test_gateway_api_routes_are_explicit_and_match_review_api():
    expected = {"/api" + p for p, *_ in ((p, None) for p in API["paths"]) if p not in {"/healthz", "/readyz", "/metrics"}}
    actual = {p for p in GW["paths"] if p.startswith("/api/")}
    assert actual == expected, {"missing": expected - actual, "extra": actual - expected}


def _effective_security(op, doc):
    return op.get("security", doc.get("security", []))


def _scheme_names(reqs):
    return {name for r in reqs for name in r}


def test_review_api_uses_internal_token():
    assert API["components"]["securitySchemes"]["internal"] == {"type": "apiKey", "in": "header", "name": "X-Internal-Token"}
    for p, m, o, *_ in ops(API, "review-api.openapi.yaml"):
        sec = _scheme_names(_effective_security(o, API))
        if o.get("security") == []:
            assert o["operationId"] in PUBLIC, f"{m} {p} is unauthenticated"
        else:
            assert "internal" in sec, f"{m.upper()} {p}"


def test_gateway_browser_routes_use_session_cookie():
    sch = GW["components"]["securitySchemes"]
    assert sch["session"] == {"type": "apiKey", "in": "cookie", "name": "aiscan_session"}
    assert "internal" not in sch, "gateway must not expose the internal token scheme"
    for p, m, o, *_ in ops(GW, "gateway.openapi.yaml"):
        sec = _scheme_names(_effective_security(o, GW))
        if o.get("security") == []:
            continue  # public: health, OAuth, HMAC webhook
        assert sec == {"session"}, f"{m.upper()} {p}: {sec}"


def test_proxied_routes_do_not_inherit_internal_token():
    """Proxied path items are $refs into review-api: they must not carry op-level security
    (which would leak `internal` into the gateway contract)."""
    leaks = [f"{m.upper()} {p}" for p, m, o, item, base in ops(GW, "gateway.openapi.yaml")
             if p.startswith("/api/") and "internal" in _scheme_names(o.get("security", []))]
    assert not leaks, leaks


@pytest.mark.parametrize("spec", ["review-api.openapi.yaml", "gateway.openapi.yaml"])
def test_error_codes_enumerated(spec):
    err, base = deref(DOCS[spec]["components"]["schemas"]["Error"], spec)
    code = flat(flat(err["properties"]["error"], base)["properties"]["code"], base)
    assert {"INVALID_SIGNATURE", "CLOUD_ACK_REQUIRED", "CONFLICT"} <= set(code.get("enum", []))


def test_repo_size_in_mb_everywhere():
    text = "\n".join((p.parent / p.name).read_text() for p in FIXTURES_DIR.parent.parent.glob("*.*") if p.suffix in {".yaml", ".json"})
    assert "size_kb" not in text
    rc = comp("RepoCheck")
    assert "size_mb" in rc["required"] and "size_mb" in rc["properties"]
    assert "max_repo_size_mb" in comp("Settings")["properties"]


# ---- Phase 4: admin AI providers ----
def test_p4_provider_cloud_ack():
    s = API["components"]["schemas"]
    assert "cloud_ack" in s["AIProvider"]["required"]
    assert set(s["CloudAck"]["required"]) == {"acknowledged", "at", "by"}
    assert {"kind", "cloud_ack"} <= set(s["AIProviderPatch"]["properties"])
    assert "cloud_ack" in s["AIProviderWrite"]["properties"]
    for op in (API["paths"]["/admin/ai-providers"]["post"], API["paths"]["/admin/ai-providers/{id}"]["patch"]):
        assert "422" in op["responses"]


def test_p4_provider_test_result_and_codes():
    s = API["components"]["schemas"]
    assert set(s["ProviderTestResult"]["required"]) == {"ok", "latency_ms", "model"}
    codes = s["Error"]["properties"]["error"]["properties"]["code"]["enum"]
    assert {"AI_PROVIDER_UNREACHABLE", "AI_PROVIDER_AUTH_FAILED", "AI_PROVIDER_BAD_RESPONSE", "PROVIDER_IN_USE"} <= set(codes)
    assert codes == GW["components"]["schemas"]["Error"]["properties"]["error"]["properties"]["code"]["enum"]
    for p in ("/admin/ai-providers/{id}/test", "/admin/ai-providers/test"):
        r = API["paths"][p]["post"]["responses"]
        assert "200" in r and "502" in r
    assert "/api/admin/ai-providers/test" in GW["paths"]
    assert "409" in API["paths"]["/admin/ai-providers/{id}"]["delete"]["responses"]
