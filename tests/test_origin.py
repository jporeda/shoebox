"""Second layer behind X-Shoebox: changes are refused when the browser says they come from
another page (Origin header), and allowed with no Origin at all (CLI, tests, curl)."""

from __future__ import annotations

import pytest
from conftest import api_client
from fastapi.testclient import TestClient

REFUSED = {"detail": "cross-origin request refused"}


def ui_on(base_url: str) -> TestClient:
    """The UI as served at base_url, which sets the Host header the browser would send."""
    from shoebox.app import app

    return TestClient(app, base_url=base_url, headers={"X-Shoebox": "1"})


def undo(client: TestClient, origin: str | None = None):
    """Nothing to undo in a fresh DB, so an allowed request is a harmless 200."""
    return client.post("/api/undo", json={}, headers={} if origin is None else {"Origin": origin})


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost",  # api_client is http://localhost, i.e. port 80
        "http://127.0.0.1",
        "http://localhost:5173",  # `npm run dev`: Vite proxies /api and keeps the page's Origin
        "http://127.0.0.1:5173",
    ],
)
def test_own_pages_may_make_changes(env, origin):
    with api_client() as ui:
        assert undo(ui, origin).status_code == 200


def test_requests_without_origin_are_allowed(env):
    with api_client() as cli:
        assert undo(cli).status_code == 200


def test_origin_port_must_be_the_one_the_browser_used(env):
    with ui_on("http://localhost:8000") as ui:
        assert undo(ui, "http://localhost:8000").status_code == 200
        assert undo(ui, "http://127.0.0.1:8000").status_code == 200
        assert undo(ui, "https://localhost:8000").status_code == 200
        assert undo(ui, "http://localhost").json() == REFUSED  # port 80
        assert undo(ui, "http://localhost:8001").json() == REFUSED
    # Docker publishing 127.0.0.1:9000 -> 8000: the browser's Host says 9000, and so does Origin.
    with ui_on("http://localhost:9000") as ui:
        assert undo(ui, "http://localhost:9000").status_code == 200
        assert undo(ui, "http://localhost:8000").json() == REFUSED


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://localhost.evil.example",
        "http://evil.example:80",
        "http://localhost:3000",  # some other local dev server
        "http://127.0.0.1:8080",
        "null",  # sandboxed iframe, file:// page, data: URL
        "",
        "file://",
        "ftp://localhost",
        "http://[::1]",  # not a host name the server answers to
        "http://localhost:0",
        "http://localhost:99999",
        "http://localhost:abc",
        "http://[::1",
        "http://user@localhost",
        "http://localhost/",
        "http://localhost, https://evil.example",
    ],
)
def test_other_origins_are_refused(env, origin):
    with api_client() as ui:
        r = undo(ui, origin)
        assert r.status_code == 403
        assert r.json() == REFUSED


def test_every_changing_method_is_checked(env):
    with api_client() as ui:
        r = ui.patch("/api/photos", json={"ids": [], "caption": "x"}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403 and r.json() == REFUSED


def test_reads_are_not_checked(env):
    # Without CORS the other page can't read the response anyway.
    with api_client() as ui:
        assert ui.get("/api/stats", headers={"Origin": "https://evil.example"}).status_code == 200


def test_own_origin_still_needs_the_app_header(env):
    from shoebox.app import app

    with TestClient(app, base_url="http://localhost") as browser:
        r = browser.post("/api/undo", json={}, headers={"Origin": "http://localhost"})
        assert r.status_code == 403
        assert "X-Shoebox" in r.json()["detail"]
