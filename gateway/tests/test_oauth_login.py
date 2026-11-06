"""GitHub OAuth login: callback derivation, minimal scope, GitHub error params, allow-list."""
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import do_login, make_client, settings


def authorize_params(c):
    r = c.get("/auth/github/login", follow_redirects=False)
    assert r.status_code == 302
    return parse_qs(urlparse(r.headers["location"]).query)


@pytest.mark.parametrize("public_url,expected", [
    ("https://ai-scan.localhost", "https://ai-scan.localhost/auth/github/callback"),
    ("https://ai-scan.localhost/", "https://ai-scan.localhost/auth/github/callback"),
    ("https://ai-scan.127.0.0.1.nip.io", "https://ai-scan.127.0.0.1.nip.io/auth/github/callback"),
    ("http://localhost:8080", "http://localhost:8080/auth/github/callback"),
])
def test_callback_derived_from_public_url(public_url, expected):
    assert settings(public_url=public_url).oauth_callback_url == expected
    c, _ = make_client(public_url=public_url)
    assert authorize_params(c)["redirect_uri"] == [expected]


def test_callback_override():
    s = settings(public_url="https://a.example", github_callback_url="https://b.example/auth/github/callback")
    assert s.oauth_callback_url == "https://b.example/auth/github/callback"


def test_token_exchange_uses_same_redirect_uri():
    c, router = make_client(public_url="https://ai-scan.localhost")
    route = router.post("https://github.example/login/oauth/access_token").respond(200, json={"access_token": "t"})
    router.get("https://api.github.example/user").respond(200, json={"id": 1, "login": "almog975"})
    c.get("/auth/github/login", follow_redirects=False)
    st = c.cookies.get("aiscan_oauth_state")
    assert c.get(f"/auth/github/callback?code=x&state={st}", follow_redirects=False).status_code == 302
    assert "redirect_uri=https%3A%2F%2Fai-scan.localhost%2Fauth%2Fgithub%2Fcallback" in route.calls.last.request.content.decode()


def test_default_scope_is_minimal():
    c, _ = make_client()
    scope = authorize_params(c)["scope"][0].split()
    assert scope == ["read:user"]
    assert "repo" not in scope


def test_scope_configurable():
    c, _ = make_client(github_oauth_scopes="read:user, user:email")
    assert authorize_params(c)["scope"] == ["read:user user:email"]


@pytest.mark.parametrize("err", ["access_denied", "redirect_uri_mismatch", "application_suspended", "other"])
def test_github_error_params_are_400_not_422(err):
    c, _ = make_client(public_url="https://ai-scan.localhost")
    r = c.get(f"/auth/github/callback?error={err}&error_description=Nope+here&state=s")
    assert r.status_code == 400
    body = r.text
    assert err in body and "Nope here" in body
    if err == "redirect_uri_mismatch":
        assert "https://ai-scan.localhost/auth/github/callback" in body


def test_missing_code_is_400_not_422():
    c, _ = make_client()
    assert c.get("/auth/github/callback").status_code == 400
    assert c.get("/auth/github/callback?state=s").status_code == 400


def test_allow_list_denies_others_and_names_login():
    c, router = make_client(allowed_github_logins="almog975")
    r = do_login(c, router, login="someone")
    assert r.status_code == 403 and "someone" in r.text


def test_allow_list_admits_owner_case_insensitive_and_admin():
    c, router = make_client(allowed_github_logins="first, Almog975", admin_github_logins="ALMOG975")
    assert do_login(c, router, login="first", uid=1).status_code == 302   # first user = admin by bootstrap
    c.cookies.clear()
    assert do_login(c, router, login="almog975", uid=2).status_code == 302
    assert c.get("/me").json()["role"] == "admin"   # via ADMIN_GITHUB_LOGINS, not bootstrap


def test_production_requires_https_and_secure_cookie():
    p = settings(app_env="production", public_url="http://localhost:8080", cookie_secure=False,
                 database_url="postgresql://x", internal_api_token="x" * 40).problems()
    assert any("PUBLIC_URL" in x for x in p) and any("COOKIE_SECURE" in x for x in p)


def test_local_app_env_accepts_http_public_url():
    assert settings(app_env="local", public_url="http://localhost:8080", cookie_secure=False).problems() == []
