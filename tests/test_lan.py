"""Home-network access (SHOEBOX_BIND): which host names and origins the server answers to, and
Undo staying per device once several devices share it."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from conftest import make_image, reset_caches
from shoebox.db import session_scope
from shoebox.ingest import run_ingest
from shoebox.models import Photo

LAN_HOSTS = ["http://Family-Mac.local:8000", "http://192.168.1.5:8000", "http://10.0.0.7:8000", "http://172.20.1.2:8000"]
FOREIGN_HOSTS = ["http://evil.example:8000", "http://8.8.8.8:8000", "http://172.32.0.1:8000", "http://local:8000"]


def client(base_url: str, device: str | None = None) -> TestClient:
    from shoebox.app import app

    headers = {"X-Shoebox": "1"}
    if device:
        headers["X-Shoebox-Client"] = device
    return TestClient(app, base_url=base_url, headers=headers)


@pytest.fixture
def lan(env, monkeypatch):
    monkeypatch.setenv("SHOEBOX_BIND", "0.0.0.0")
    monkeypatch.setenv("SHOEBOX_HOSTS", "shoebox.home.arpa")
    reset_caches()
    yield
    monkeypatch.delenv("SHOEBOX_BIND")
    reset_caches()


@pytest.mark.parametrize("base", LAN_HOSTS)
def test_network_names_are_refused_unless_network_access_is_on(env, base):
    with client(base) as c:
        assert c.get("/api/stats").status_code == 400


@pytest.mark.parametrize("base", [*LAN_HOSTS, "http://shoebox.home.arpa:8000", "http://localhost:8000"])
def test_network_names_answer_when_network_access_is_on(lan, base):
    with client(base) as c:
        assert c.get("/api/stats").status_code == 200
        # A write from the app's own page, as the browser on that device sends it.
        assert c.post("/api/undo", json={}, headers={"Origin": base}).status_code == 200


@pytest.mark.parametrize("base", FOREIGN_HOSTS)
def test_other_names_are_still_refused_with_network_access_on(lan, base):
    """A DNS-rebinding page arrives under its own public name, so it gets nothing."""
    with client(base) as c:
        assert c.get("/api/stats").status_code == 400


@pytest.mark.parametrize("origin", ["http://evil.example", "http://8.8.8.8:8000", "null", "http://192.168.1.5:9999"])
def test_writes_from_other_pages_are_refused_with_network_access_on(lan, origin):
    with client("http://192.168.1.5:8000") as c:
        assert c.post("/api/undo", json={}, headers={"Origin": origin}).status_code == 403


def test_undo_takes_back_this_devices_edits_not_anothers(lan):
    make_image(lan_photos() / "A1" / "IMG_0001.jpg", 1)
    make_image(lan_photos() / "A1" / "IMG_0002.jpg", 2)
    run_ingest()
    with session_scope() as s:
        a, b = [p.id for p in s.exec(select(Photo).order_by(Photo.rel_path)).all()]

    mac, phone = client("http://localhost:8000", "mac-device-1"), client("http://192.168.1.5:8000", "phone-device-1")
    with mac, phone:
        assert mac.patch("/api/photos", json={"ids": [a], "date": "1985"}).status_code == 200
        assert phone.patch("/api/photos", json={"ids": [b], "date": "1990"}).status_code == 200
        assert mac.patch("/api/photos", json={"ids": [a], "caption": "porch"}).status_code == 200  # Mac's latest

        # The phone's Undo takes back the phone's edit, though the Mac edited since.
        assert "1 photo" in phone.post("/api/undo", json={}).json()["description"]
        assert [e["description"] for e in phone.get("/api/edits").json() if not e["undone"]] == []
        with session_scope() as s:
            assert (s.get(Photo, a).t_year, s.get(Photo, a).caption, s.get(Photo, b).t_year) == (1985, "porch", None)

        # The Mac's Undo is untouched by that and takes back its own caption.
        assert len([e for e in mac.get("/api/edits").json() if not e["undone"]]) == 2
        mac.post("/api/undo", json={})
        with session_scope() as s:
            assert (s.get(Photo, a).t_year, s.get(Photo, a).caption) == (1985, None)


def lan_photos():
    from shoebox.config import get_settings

    return get_settings().photos_root
