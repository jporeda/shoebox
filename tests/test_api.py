from conftest import api_client, make_image

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


def test_photo_detail_shows_the_originals_folder_on_the_mac(album_tree, env, monkeypatch):
    from conftest import api_client
    from shoebox.ingest import run_ingest

    run_ingest()
    with api_client() as c:
        pid = c.get("/api/photos").json()["items"][0]["id"]
        assert c.get(f"/api/photos/{pid}").json()["source_folder"] is None  # PHOTO_ROOT unknown
        monkeypatch.setenv("PHOTO_ROOT", "/Users/you/Pictures/FamilyScans")  # what compose passes in
        d = c.get(f"/api/photos/{pid}").json()
    album_dir = d["rel_path"].rsplit("/", 1)[0]
    assert d["source_folder"] == f"/Users/you/Pictures/FamilyScans/{album_dir}"


def test_photo_detail_offers_the_nearest_dated_photos_before_and_after(album_tree, env):
    from conftest import api_client
    from shoebox.ingest import run_ingest

    run_ingest()
    with api_client() as c:
        items = c.get("/api/photos", params={"order": "album"}).json()["items"]
        album = [p for p in items if p["album_id"] == items[0]["album_id"]]
        first, middle, last = album[0]["id"], album[1]["id"], album[2]["id"]
        c.patch("/api/photos", json={"ids": [first], "date": "1987-06-14"})
        c.patch("/api/photos", json={"ids": [last], "date": "1988"})
        near = c.get(f"/api/photos/{middle}").json()["nearby_dates"]
    assert [(n["photo_id"], n["offset"] < 0, n["label"], n["year"]) for n in near] == [
        (first, True, "1987-06-14", 1987), (last, False, "1988", 1988)]


def test_photo_list_can_be_limited_to_given_ids(album_tree, env):
    from conftest import api_client
    from shoebox.ingest import run_ingest

    run_ingest()
    with api_client() as c:
        all_ids = [p["id"] for p in c.get("/api/photos").json()["items"]]
        pick = all_ids[1:3]
        got = c.get("/api/photos", params={"ids": ",".join(map(str, pick))}).json()
        assert got["total"] == 2 and sorted(p["id"] for p in got["items"]) == sorted(pick)
        assert c.get("/api/photos", params={"ids": "1,x"}).status_code == 422


def test_photo_filters_combine_years_people_and_events(album_tree, env):
    from conftest import api_client
    from sqlmodel import select
    from shoebox.db import session_scope
    from shoebox.ingest import run_ingest
    from shoebox.models import EventKind, Face, Person

    run_ingest()
    with api_client() as c:
        ids = [p["id"] for p in c.get("/api/photos").json()["items"]][:4]
        a, b, d, e = ids
        with session_scope() as s:
            bday = s.exec(select(EventKind).where(EventKind.name == "Birthday")).one().id
        c.patch("/api/photos", json={"ids": [a, b], "date": "1985", "event_kind_id": bday, "event_label": "Party"})
        c.patch("/api/photos", json={"ids": [d], "date": "1986"})
        c.patch("/api/photos", json={"ids": [e], "date": "1990"})
        with session_scope() as s:
            sam, ann = Person(name="Sam"), Person(name="Ann")
            s.add_all([sam, ann])
            s.flush()
            for pid, who in ((a, sam), (a, ann), (b, sam), (d, ann)):
                s.add(Face(photo_id=pid, x=0.1, y=0.1, w=0.1, h=0.1, person_id=who.id, source="manual"))
            sam_id, ann_id = sam.id, ann.id
        party = c.get("/api/event-instances").json()[0]["id"]

        def got(**params):
            return sorted(p["id"] for p in c.get("/api/photos", params=params).json()["items"])

        assert got(years="1985,1986") == sorted([a, b, d])  # any of the years
        assert got(people=f"{sam_id},{ann_id}") == [a]  # both of them, together
        assert got(people=f"{sam_id},{ann_id}", people_mode="any") == sorted([a, b, d])
        assert got(years="1985,1986", people=str(ann_id)) == sorted([a, d])  # filters combine
        assert got(events=str(party)) == sorted([a, b])
        assert got(events=str(party), people=str(ann_id)) == [a]
        assert {y["year"]: y["count"] for y in c.get("/api/years").json()}[1985] >= 2
        assert c.get("/api/photos", params={"years": "19x5"}).status_code == 422


def test_albums_are_a_filter_that_combines_with_the_others(album_tree, env):
    from conftest import api_client
    from shoebox.ingest import run_ingest

    run_ingest()
    with api_client() as c:
        albums = {a["name"]: a["id"] for a in c.get("/api/albums").json()}
        items = c.get("/api/photos").json()["items"]
        both = ",".join(str(i) for i in albums.values())
        assert c.get("/api/photos", params={"albums": both}).json()["total"] == len(items)
        one = albums["Album 1980s"]
        in_one = [p["id"] for p in items if p["album_id"] == one]
        assert sorted(p["id"] for p in c.get("/api/photos", params={"albums": str(one)}).json()["items"]) == sorted(in_one)
        c.patch("/api/photos", json={"ids": [in_one[0]], "date": "1985"})
        got = c.get("/api/photos", params={"albums": both, "years": "1985"}).json()["items"]
        assert [p["id"] for p in got] == [in_one[0]]


def test_duplicates_can_keep_both_and_go_back_to_one(album_tree, env):
    make_image(album_tree / "Album 1990s" / "rescan.jpg", 2, size=(640, 480), quality=60)  # a rescan of IMG_0002
    run_ingest()
    with api_client() as client:
        dups = client.get("/api/photos", params={"duplicates": True}).json()["items"]
        a, b = [p["id"] for p in dups]
        detail = client.get(f"/api/photos/{a}").json()
        assert detail["dup_members"] == [a, b] and detail["file_size"] > 0

        kept = lambda: [client.get(f"/api/photos/{i}").json()["is_dup_keeper"] for i in (a, b)]
        client.post(f"/api/photos/{a}/keep-all")
        assert kept() == [True, True]
        client.post("/api/undo", json={})
        assert kept() == [False, False]

        client.post(f"/api/photos/{a}/keep-all")
        client.post(f"/api/photos/{b}/keeper")  # "Keep only this one"
        assert kept() == [False, True]
        assert client.post("/api/photos/999999/keep-all").status_code == 404


def test_not_duplicates_splits_the_group_and_import_keeps_it_split(album_tree, env):
    make_image(album_tree / "Album 1990s" / "rescan.jpg", 2, size=(640, 480), quality=60)
    make_image(album_tree / "Album 1990s" / "rescan 2.jpg", 2, size=(400, 300), quality=50)
    run_ingest()
    with api_client() as client:
        group = lambda: {p["rel_path"]: p["dup_group_id"] for p in client.get("/api/photos", params={"duplicates": True}).json()["items"]}
        assert len(group()) == 3 and len(set(group().values())) == 1
        ids = {p["rel_path"]: p["id"] for p in client.get("/api/photos", params={"duplicates": True}).json()["items"]}

        # Just one photo out of the group: the other two stay duplicates.
        client.post(f"/api/photos/{ids['Album 1990s/rescan 2.jpg']}/not-duplicates", json={"only_this": True})
        assert set(group()) == {"Album 1980s/IMG_0002.jpg", "Album 1990s/rescan.jpg"}
        run_ingest()
        assert set(group()) == {"Album 1980s/IMG_0002.jpg", "Album 1990s/rescan.jpg"}

        # The whole group: none left, still after Import; Undo brings the pair back.
        client.post(f"/api/photos/{ids['Album 1990s/rescan.jpg']}/not-duplicates", json={})
        assert group() == {}
        run_ingest()
        assert group() == {}
        client.post("/api/undo", json={})
        assert set(group()) == {"Album 1980s/IMG_0002.jpg", "Album 1990s/rescan.jpg"}


def test_json_is_gzipped_but_images_are_not(album_tree, env):
    run_ingest()
    with api_client() as client:
        gz = {"Accept-Encoding": "gzip"}
        listing = client.get("/api/photos", headers=gz)
        assert listing.headers["content-encoding"] == "gzip"
        assert listing.json()["total"] > 0  # decodes back to the same list
        p = listing.json()["items"][0]
        img = client.get(f"/api/img/{p['sha256']}/thumb/0.jpg", headers=gz)
        assert "content-encoding" not in img.headers and "immutable" in img.headers["cache-control"]


def test_job_states_is_the_slim_poll(env):
    with api_client() as client:
        job = client.post("/api/jobs", json={"type": "backup"}).json()
        assert client.get("/api/jobs/states").json()[0] == {"id": job["id"], "state": "queued"}
