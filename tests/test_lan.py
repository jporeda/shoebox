"""Home-network access (SHOEBOX_BIND): which host names and origins the server answers to, and
Undo staying per device once several devices share it."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from conftest import make_image, require, reset_caches
from shoebox import editlog
from shoebox.db import session_scope
from shoebox.ingest import run_ingest
from shoebox.models import Face, Photo

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


# ---------- Regression tests for bugs found in review of lan-access ----------

MAC, PHONE = "mac-device-1", "phone-device-1"


@pytest.fixture
def two_photos(lan):
    make_image(lan_photos() / "A1" / "IMG_0001.jpg", 1)
    make_image(lan_photos() / "A1" / "IMG_0002.jpg", 2)
    run_ingest()
    with session_scope() as s:
        ids = [p.id for p in s.exec(select(Photo).order_by(Photo.rel_path)).all()]
    require(len(ids) == 2, f"expected 2 ingested photos, got {ids}")
    return ids


def year(pid: int):
    with session_scope() as s:
        return s.get(Photo, pid).t_year


# L1/M1: out-of-order undo used to delete a face another device had named.
def test_undoing_a_drawn_face_another_device_has_named_keeps_the_face_and_reports_a_conflict(two_photos):
    a, _ = two_photos
    with client("http://192.168.1.5:8000", PHONE) as phone, client("http://localhost:8000", MAC) as mac:
        r = phone.post(f"/api/photos/{a}/faces", json={"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2})
        require(r.status_code == 200, f"drawing a face failed: {r.status_code}")
        fid = r.json()["id"]
        r = mac.post("/api/faces/assign", json={"face_ids": [fid], "name": "Test Person"})
        require(r.status_code == 200, f"naming the face failed: {r.status_code}")

        result = phone.post("/api/undo", json={}).json()
        with session_scope() as s:
            face = s.get(Face, fid)
            assert face is not None and face.person_id is not None, "the phone's undo deleted the Mac's named face"
        assert result["conflicts"], f"undo did not report a conflict: {result}"


# L2/BUG-1: undo by explicit batch_id used to ignore which device owns the batch.
def test_undo_by_batch_id_refuses_another_devices_batch(two_photos):
    a, _ = two_photos
    with client("http://localhost:8000", MAC) as mac, client("http://192.168.1.5:8000", PHONE) as phone:
        require(mac.patch("/api/photos", json={"ids": [a], "date": "1985"}).status_code == 200, "Mac edit failed")
        mac_batch = mac.get("/api/edits").json()[0]["id"]
        r = phone.post("/api/undo", json={"batch_id": mac_batch}).json()
        assert r["undone"] is None, "the phone undid the Mac's batch by id"
        assert year(a) == 1985


# L4/BUG-2: skip used to mark the latest batch, not the one that was blocked. The backend half
# (default undo reaches only the device's own batches) makes this pass; the UI also sends batchId.
def test_skip_marks_the_blocked_batch_even_if_a_shared_batch_lands_in_between(two_photos):
    a, b = two_photos
    with client("http://localhost:8000", MAC) as mac, client("http://192.168.1.5:8000", PHONE) as phone:
        require(mac.patch("/api/photos", json={"ids": [a], "date": "1985"}).status_code == 200, "Mac edit failed")
        require(phone.patch("/api/photos", json={"ids": [a], "date": "1990"}).status_code == 200, "phone edit failed")
        blocked = mac.post("/api/undo", json={}).json()
        require(blocked.get("blocked"), f"Mac's undo was not blocked: {blocked}")
        with session_scope() as s:  # a job or CLI edit lands between the two clicks
            editlog.record(s, [(s.get(Photo, b), "t_year", 2001)], "job edit", actor="user")

        r = mac.post("/api/undo", json={"skip": True}).json()
        assert r["batch_id"] == blocked["batch_id"], f"skipped {r.get('description')!r} instead"
        assert year(b) == 2001


# L3/BUG-3: a missing or malformed client id used to undo any device's edits.
@pytest.mark.parametrize("header", [None, "", "short", "x" * 65, "bad:chars!"])
def test_undo_without_a_valid_client_id_does_not_take_back_a_devices_edit(two_photos, header):
    a, _ = two_photos
    with client("http://192.168.1.5:8000", PHONE) as phone:
        require(phone.patch("/api/photos", json={"ids": [a], "date": "1985"}).status_code == 200, "phone edit failed")
    extra = {} if header is None else {"X-Shoebox-Client": header}
    with client("http://localhost:8000") as stale_tab:
        stale_tab.post("/api/undo", json={}, headers=extra)
    assert year(a) == 1985, f"client header {header!r} undid the phone's edit"


# L5: the top-bar undo from a device used to take back shared (job/restore/CLI) batches.
@pytest.mark.parametrize("actor", ["user", "restore"])
def test_default_undo_from_a_device_leaves_shared_batches_alone(two_photos, actor):
    a, _ = two_photos
    with session_scope() as s:
        editlog.record(s, [(s.get(Photo, a), "t_year", 1970)], "shared batch", actor=actor)
    require(year(a) == 1970, "shared batch did not apply")
    with client("http://192.168.1.5:8000", PHONE) as phone:
        r = phone.post("/api/undo", json={}).json()
    assert r["undone"] is None, f"the phone's default undo took back a {actor!r} batch"
    assert year(a) == 1970


# L6: writes from another LAN host's page on our port used to be accepted.
@pytest.mark.parametrize("origin", ["http://192.168.1.99:8000", "http://printer.local:8000"])
def test_writes_from_another_lan_hosts_page_are_refused(lan, origin):
    with client("http://192.168.1.5:8000") as c:
        assert c.post("/api/undo", json={}, headers={"Origin": origin}).status_code == 403


def test_a_device_may_undo_a_shared_batch_by_id_and_lists_it_with_shared(two_photos):
    """The Jobs page's "undo this run": shared batches are listed with ?shared=true and any device
    may undo one by id; the count lets the UI confirm before undoing many changes."""
    a, b = two_photos
    with session_scope() as s:
        job = editlog.record(s, [(s.get(Photo, a), "t_year", 1970), (s.get(Photo, b), "t_year", 1971)], "job")
    with client("http://192.168.1.5:8000", PHONE) as phone:
        require(phone.patch("/api/photos", json={"ids": [a], "caption": "porch"}).status_code == 200, "edit failed")
        assert job not in [e["id"] for e in phone.get("/api/edits").json()]
        listed = phone.get("/api/edits", params={"shared": True}).json()
        assert [(e["id"], e["edits"]) for e in listed] == [(job, 2)]
        r = phone.post("/api/undo", json={"batch_id": job}).json()
        assert r["undone"] == job
        assert [c["id"] for c in r["conflicts"]] == [a]  # the phone edited photo a since: left alone
    assert (year(a), year(b)) == (1970, None)


def test_a_request_without_a_client_id_may_still_undo_shared_batches(two_photos):
    a, _ = two_photos
    with session_scope() as s:
        job = editlog.record(s, [(s.get(Photo, a), "t_year", 1970)], "job")
    with client("http://localhost:8000") as script:
        assert script.post("/api/undo", json={}).json()["undone"] == job


def test_the_cli_still_undoes_any_batch(two_photos):
    a, _ = two_photos
    with client("http://192.168.1.5:8000", PHONE) as phone:
        require(phone.patch("/api/photos", json={"ids": [a], "date": "1985"}).status_code == 200, "edit failed")
    with session_scope() as s:
        assert editlog.undo(s)["undone"] is not None
    assert year(a) is None


@pytest.mark.parametrize("origin", ["http://192.168.1.5:5173", "http://Family-Mac.local:5173"])
def test_the_vite_dev_exception_is_for_this_machine_only(lan, origin):
    with client("http://192.168.1.5:8000") as c:
        assert c.post("/api/undo", json={}, headers={"Origin": origin}).status_code == 403


def test_naming_with_different_case_reuses_the_existing_person(lan):
    """The "New person" hint ignores case, so the server must too: "test person" is Test Person."""
    from shoebox.models import Person
    from shoebox.people import person_for_name

    with session_scope() as s:
        first, created = person_for_name(s, "Test Person")
        require(created, "setup: first naming creates the person")
        again, created_again = person_for_name(s, "  test person ")
        assert again.id == first.id and created_again == []
        assert len(s.exec(select(Person)).all()) == 1
