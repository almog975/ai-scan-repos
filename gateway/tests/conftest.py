import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

GH = "https://github.example"
API = "https://api.github.example"
RA = "http://review-api.test"


def settings(**kw):
    base = dict(database_url="sqlite:///:memory:", review_api_url=RA, internal_api_token="itok",
                github_client_id="cid", github_client_secret="csecret", github_oauth_base=GH, github_api_base=API,
                public_url="http://app.test", github_webhook_secret="whsecret", cookie_secure=False)
    base.update(kw)
    return Settings(**base)


def make_client(base_url="http://app.test", **kw):
    router = respx.MockRouter(assert_all_called=False)
    app = create_app(settings(**kw), transport=httpx.MockTransport(router.async_handler))
    return TestClient(app, base_url=base_url), router


def do_login(c, router, login="almog", uid=42):
    router.post(f"{GH}/login/oauth/access_token").respond(200, json={"access_token": "ghtok"})
    router.get(f"{API}/user").respond(200, json={"id": uid, "login": login})
    r = c.get("/auth/login", follow_redirects=False)
    state = c.cookies.get("aisr_oauth_state")
    assert f"state={state}" in r.headers["location"]
    return c.get(f"/auth/callback?code=x&state={state}", follow_redirects=False)


@pytest.fixture
def logged_in():
    c, router = make_client()
    assert do_login(c, router).status_code == 302
    return c, router
