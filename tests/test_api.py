from conftest import api_client

from shoebox.ingest import run_ingest


def test_grid_edit_undo_flow(album_tree, env):
    run_ingest()
    with api_client() as client:
        albums = client.get("/api/albums").json()
        assert [a["name"] for a in albums] == ["Album 1980s", "Album 1990s"]
        page = client.get("/api/photos", params={"album_id": albums[0]["id"]}).json()
        assert page["total"] == 3
        ids = [p["id"] for p in page["items"][:2]]

        assert client.get("/api/parse-date", params={"text": "1980s"}).json() == {"ok": True, "label": "1980s", "error": ""}
        bad = client.patch("/api/photos", json={"ids": ids, "date": "summer"})
        assert bad.status_code == 422

        res = client.patch("/api/photos", json={"ids": ids, "date": "1983-07"}).json()
        assert {p["date_label"] for p in res["photos"]} == {"1983-07"}
        assert client.get("/api/stats").json()["reviewed"] == 2
        chrono = client.get("/api/photos", params={"order": "chrono"}).json()["items"]
        assert [p["id"] for p in chrono[:2]] == ids

        assert client.get("/api/photos", params={"status": "untagged"}).json()["total"] == 4
        undo = client.post("/api/undo", json={}).json()
        assert undo["reverted"] == 2 * 6
        assert client.get("/api/stats").json()["reviewed"] == 0

        p = page["items"][0]
        img = client.get(f"/api/img/{p['sha256']}/thumb/90.jpg")
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
        assert "immutable" in img.headers["cache-control"]
        back = client.get(f"/api/img/{p['back_sha256']}/preview/0.jpg")
        assert back.status_code == 200

        job = client.post("/api/jobs", json={"type": "backup"}).json()
        assert job["state"] == "queued"


def test_other_sites_cannot_make_changes(album_tree, env):
    from fastapi.testclient import TestClient

    from shoebox.app import app

    with TestClient(app, base_url="http://localhost") as browser:
        # A page elsewhere can send a "simple" POST, but can't add the app's header.
        r = browser.post("/api/jobs", content='{"type": "ingest"}')
        assert r.status_code == 403
        assert browser.get("/api/stats").status_code == 200  # reading is fine
    with TestClient(app, base_url="http://evil.example") as rebound:
        # DNS rebinding: a hostile name pointed at 127.0.0.1
        assert rebound.get("/api/stats").status_code == 400
    with api_client() as ui:
        assert ui.post("/api/jobs", json={"type": "backup"}).status_code == 200
