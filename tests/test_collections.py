"""Separate photo collections: each has its own photos, database, people, edits, jobs and backups,
and nothing carries over between them."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from conftest import make_image, reset_caches
from shoebox import config, db, jobs
from shoebox.config import use_collection
from shoebox.db import session_scope
from shoebox.ingest import run_ingest
from shoebox.models import Job, Person, Photo


@pytest.fixture
def two(env, tmp_path, monkeypatch):
    """The usual main collection plus collection 2 ("Grandma's albums") in its own folders."""
    root = tmp_path / "collections"
    (root / "2" / "photos").mkdir(parents=True)
    monkeypatch.setenv("SHOEBOX_COLLECTIONS_ROOT", str(root))
    monkeypatch.setenv("COLLECTION_2_NAME", "Grandma's albums")
    monkeypatch.setenv("COLLECTION_2_PHOTOS", "/host/path/only/used/by/compose")
    monkeypatch.setenv("COLLECTION_2_BACKUP", "/host/path/only/used/by/compose")
    reset_caches()
    db.migrate_all()
    for i in range(3):
        make_image(env.photos_root / "Album A" / f"IMG_{i}.jpg", i)
    for i in range(2):  # nested subfolders become albums of their own
        make_image(root / "2" / "photos" / "Box 1" / "Envelope 3" / f"scan_{i}.jpg", 100 + i)
    run_ingest()
    with use_collection("c2"):
        run_ingest()
    return root


def ui(collection: str | None = None) -> TestClient:
    from shoebox.app import app

    c = TestClient(app, base_url="http://localhost", headers={"X-Shoebox": "1", "X-Shoebox-Client": "device-one-1"})
    if collection:
        c.cookies.set("shoebox_collection", collection)
    return c


def test_collections_are_listed_and_each_has_its_own_folders(two):
    assert [(c.id, c.name) for c in config.collections()] == [("main", "Photos"), ("c2", "Grandma's albums")]
    main = config.get_settings()
    with use_collection("c2"):
        c2 = config.get_settings()
    assert c2.photos_root == two / "2" / "photos" and c2.backup_dir == two / "2" / "backup"
    assert c2.db_path != main.db_path and c2.db_path.parent == main.data_dir / "collections" / "c2"


def test_nothing_carries_over_between_collections(two):
    with ui() as main, ui("c2") as grandma:
        assert [c["current"] for c in grandma.get("/api/collections").json()] == [False, True]
        mp = main.get("/api/photos").json()["items"]
        gp = grandma.get("/api/photos").json()["items"]
        assert (len(mp), len(gp)) == (3, 2)
        assert [a["rel_path"] for a in grandma.get("/api/albums").json()] == ["Box 1/Envelope 3"]

        # Dates, people and undo stay in their own collection.
        main.patch("/api/photos", json={"ids": [mp[0]["id"]], "date": "1985"})
        assert all(p["year"] is None for p in grandma.get("/api/photos").json()["items"])
        assert grandma.post("/api/undo", json={}).json()["undone"] is None  # nothing of hers to undo
        assert "date 1985" in main.post("/api/undo", json={}).json()["description"]
        main.post("/api/people", json={"name": "Sam"})
        assert grandma.get("/api/people").json() == []

        # A job queued in one collection runs against that collection only.
        grandma.post("/api/jobs", json={"type": "backup"})
    with use_collection("c2"):
        with session_scope() as s:
            assert [j.type for j in s.exec(select(Job)).all()].count("backup") == 1
            assert s.exec(select(Person)).all() == []
    with session_scope() as s:
        assert [p.name for p in s.exec(select(Person)).all()] == ["Sam"]
        assert "backup" not in [j.type for j in s.exec(select(Job)).all()]


def test_the_worker_runs_each_collections_jobs_with_its_own_folders(two):
    main_truth = config.get_settings().backup_dir / "truth" / "truth.json"
    before = main_truth.read_bytes() if main_truth.exists() else None
    with use_collection("c2"):
        job = jobs.enqueue("backup")
        assert jobs._claim() == job.id
        jobs.run_job(job.id)
    assert (two / "2" / "backup" / "truth" / "truth.json").is_file()  # her backup folder
    assert (main_truth.read_bytes() if main_truth.exists() else None) == before  # main's untouched


def test_an_unknown_collection_falls_back_to_main(two):
    with ui("c9") as c:
        assert [x["current"] for x in c.get("/api/collections").json()] == [True, False]
        assert len(c.get("/api/photos").json()["items"]) == 3
