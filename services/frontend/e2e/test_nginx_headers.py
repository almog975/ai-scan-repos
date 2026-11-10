"""Static checks of frontend/nginx.conf security headers (no nginx/docker on the test box).
Strict xfail = finding, not landed."""
import os, re
import pytest

CONF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nginx.conf")
TEXT = open(CONF).read()


def headers():
    out = {}
    for m in re.finditer(r'add_header\s+([\w-]+)\s+("([^"]*)"|(\S+))\s*(always)?\s*;', TEXT):
        out[m.group(1).lower()] = dict(value=m.group(3) or m.group(4), always=bool(m.group(5)))
    return out


def csp():
    return {d.split()[0]: d.split()[1:] for d in headers()["content-security-policy"]["value"].split(";") if d.strip()}


def test_server_tokens_off():
    assert re.search(r"^\s*server_tokens\s+off;", TEXT, re.M)


def test_nosniff():
    assert headers()["x-content-type-options"]["value"] == "nosniff"


def test_framing_denied():
    assert headers()["x-frame-options"]["value"] == "DENY"
    assert csp()["frame-ancestors"] == ["'none'"]


def test_referrer_policy():
    assert headers()["referrer-policy"]["value"] in {"same-origin", "no-referrer", "strict-origin-when-cross-origin", "strict-origin"}


def test_headers_always_and_not_shadowed():
    assert all(h["always"] for h in headers().values()), "add_header without 'always' is dropped on 4xx/5xx"
    # nginx: any add_header inside a location drops all server-level add_header
    for loc in re.finditer(r"location[^{]*\{([^}]*)\}", TEXT):
        assert "add_header" not in loc.group(1)


def test_csp_no_unsafe_script():
    c = csp()
    script = c.get("script-src", c["default-src"])
    assert "'unsafe-inline'" not in script and "'unsafe-eval'" not in script
    assert c["default-src"] == ["'self'"]


def test_csp_no_unsafe_inline_style():
    assert "'unsafe-inline'" not in csp().get("style-src", [])


def test_csp_hardening_directives():
    c = csp()
    assert c.get("base-uri") in (["'self'"], ["'none'"])
    assert c.get("form-action") in (["'self'"], ["'none'"])
    assert c.get("object-src") == ["'none'"]


def test_permissions_policy():
    assert "permissions-policy" in headers() or "permissionsPolicy" in open(os.path.join(os.path.dirname(CONF), "..", "deploy", "platform", "base", "ingress.yaml")).read()


INGRESS = os.path.join(os.path.dirname(CONF), "..", "deploy", "platform", "base", "ingress.yaml")
ING = open(INGRESS).read()


def test_hsts_at_traefik_on_all_routes():
    # nginx listens plain 8080; TLS + HSTS are terminated at Traefik (websecure)
    m = re.search(r"stsSeconds:\s*(\d+)", ING)
    assert m and int(m.group(1)) >= 15552000
    routes = re.findall(r"middlewares:\s*\[([^\]]*)\]", ING)
    assert routes and all("security-headers" in r for r in routes)
    assert "websecure" in ING


def test_no_legacy_xss_filter():
    assert not re.search(r"browserXssFilter:\s*true", ING)


def test_spa_needs_no_inline_styles():
    src = os.path.join(os.path.dirname(CONF), "src")
    for root, _, files in os.walk(src):
        for f in files:
            if f.endswith(".tsx") and "/test" not in root:
                assert "style={" not in open(os.path.join(root, f)).read(), f
