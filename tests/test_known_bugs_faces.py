"""Known face and naming bugs, reproduced. Each test states the behaviour we want and is a strict
xfail until its fix lands: the fix removes the marker (and may move the test next to
test_faces.py or test_infer.py).

IDs are from the team review (J = Jordan, M = Morgan); "plan" is the item in the team plan.
"""

import numpy as np
import pytest
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import select

from conftest import api_client, make_image, require
from shoebox import faces, jobs, people, services
from shoebox.db import session_scope
from shoebox.faces import DetectedFace, to_blob
from shoebox.ingest import run_ingest
from shoebox.matching import run_matching
from shoebox.models import Face, Job, Person, Photo


@pytest.fixture
def rng():
    return np.random.default_rng(1)


@pytest.fixture
def album(env):
    for i in range(10):
        make_image(env.photos_root / "A1" / f"IMG_{i:04d}.jpg", i)
    run_ingest()
    with session_scope() as s:
        return list(s.exec(select(Photo.id).order_by(Photo.list_index)).all())


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def add_faces(album, rng, rows):
    """rows: (photo index, base vector, person id or None, source); returns the new face ids."""
    with session_scope() as s:
        ids = []
        for idx, base, person_id, source in rows:
            f = Face(photo_id=album[idx], x=0.1, y=0.1, w=0.2, h=0.2, person_id=person_id, source=source,
                     est_age=30, embedding=to_blob(unit(base + rng.normal(size=512) * 0.35)))
            s.add(f)
            s.flush()
            ids.append(f.id)
        return ids


def add_people(*names):
    with session_scope() as s:
        ps = [Person(name=n) for n in names]
        s.add_all(ps)
        s.flush()
        return [p.id for p in ps]


def auto_named(face_ids):
    with session_scope() as s:
        return sum(1 for i in face_ids if (f := s.get(Face, i)).person_id is not None and f.source == "auto")


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B7 / plan 4.1: Merging people loses 'not them' answers")
def test_not_them_survives_merging_duplicate_people(album, rng):
    A = rng.normal(size=512)
    sam, sammy = add_people("Sam", "Sammy")
    add_faces(album, rng, [(0, A, sam, "manual"), (1, A, sam, "manual")])
    (face_id,) = add_faces(album, rng, [(2, A, None, "auto")])
    with session_scope() as s:
        people.assign(s, [face_id], sammy)
        people.reject(s, [face_id])  # "this isn't Sammy"
    with session_scope() as s:
        people.merge_people(s, sammy, sam)  # Sammy was a duplicate of Sam
    run_matching()

    with session_scope() as s:
        f = s.get(Face, face_id)
        assert sam not in (f.person_id, f.suggested_person_id), "a face rejected as Sammy is now offered as Sam"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B7 / plan 4.1: Merging people leaves the empty merged-away person behind")
def test_merging_people_removes_the_merged_away_person(album, rng):
    A = rng.normal(size=512)
    sam, sammy = add_people("Sam", "Sammy")
    add_faces(album, rng, [(0, A, sam, "manual"), (1, A, sammy, "manual")])
    with api_client() as client:
        require(client.post(f"/api/people/{sammy}/merge", json={"into_id": sam}).status_code == 200, "setup: merged")
        names = [p["name"] for p in client.get("/api/people").json()]
    assert names == ["Sam"]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B10 / plan 4.1: 'Revert this run's names' does nothing on any run after the first")
def test_reverting_a_run_clears_every_name_it_reports_as_applied(album, rng):
    # The Jobs page shows "N names applied" and offers the revert button whenever N > 0.
    A = rng.normal(size=512)
    (ann,) = add_people("Ann")
    add_faces(album, rng, [(0, A, ann, "manual"), (1, A, ann, "manual")])
    auto_ids = add_faces(album, rng, [(2, A, None, "auto")])
    first = run_matching()
    require(first["auto"] == 1 and auto_named(auto_ids) == 1, f"setup: the first run names the face: {first}")
    second = run_matching()  # almost any later edit triggers another run
    with session_scope() as s:
        people.revert_run(s, second["run_id"])

    cleared = 1 - auto_named(auto_ids)
    assert cleared == second["auto"], f"run reported {second['auto']} name(s) applied; revert cleared {cleared}"


@pytest.mark.xfail(strict=True, raises=StaleDataError,
                   reason="J-B11 / plan 4.1: Faces job fails if a drawn box is deleted while it is being embedded")
def test_deleting_a_drawn_face_while_it_is_embedded_does_not_fail_the_job(album):
    with api_client() as client:
        face_id = client.post(f"/api/photos/{album[0]}/faces", json={"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}).json()["id"]

        def embedder(rgb, box):  # the worker is computing the embedding when the user deletes the box
            require(client.delete(f"/api/faces/{face_id}").json() == {"deleted": True}, "setup: drawn face deleted")
            return DetectedFace(0.1, 0.1, 0.2, 0.2, 0.9, np.ones(512, np.float32), 30)

        people.run_embed_missing(embedder=embedder)
    with session_scope() as s:
        assert s.get(Face, face_id) is None, "the deleted face came back"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="M-S4 / plan 4.1: Re-detection deletes faces the user answered 'not that person' for")
def test_redetection_keeps_a_face_answered_not_that_person(env):
    make_image(env.photos_root / "A1" / "p0.jpg", 0)
    run_ingest()
    with session_scope() as s:
        photo_id = s.exec(select(Photo.id)).one()
        bob = Person(name="Bob")
        s.add(bob)
        s.flush()
        f = Face(photo_id=photo_id, x=0.1, y=0.1, w=0.2, h=0.2, source="auto", suggested_person_id=bob.id,
                 embedding=b"\0" * 2048)
        s.add(f)
        s.flush()
        bob_id, face_id = bob.id, f.id
    with session_scope() as s:
        people.reject(s, [face_id])
    with session_scope() as s:
        require(s.get(Face, face_id).rejected_person_ids == [bob_id], "setup: 'not Bob' recorded")
        services.bulk_update(s, [photo_id], rotate_by=90)  # queues re-detection
    people.run_faces(detector=lambda rgb: [])  # the detector no longer finds this face

    with session_scope() as s:
        f = s.get(Face, face_id)
        assert f is not None, "re-detection deleted the face and its 'not Bob' answer"
        assert f.rejected_person_ids == [bob_id]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="M-S5 / plan 4.1: One missing photo file fails every faces job")
def test_faces_job_survives_a_missing_photo_file(env, monkeypatch):
    root = env.photos_root
    make_image(root / "A1" / "p0.jpg", 0)
    make_image(root / "A1" / "p1.jpg", 1)
    run_ingest()
    with session_scope() as s:
        p = s.exec(select(Photo).order_by(Photo.rel_path)).first()
        s.add(Face(photo_id=p.id, x=0.1, y=0.1, w=0.2, h=0.2, source="drawn"))  # drawn, not embedded yet
        rel = p.rel_path
    (root / rel).unlink()
    run_ingest()  # marks it missing
    monkeypatch.setattr(faces, "detect", lambda rgb: [])  # no model needed
    monkeypatch.setattr(faces, "embed_region", lambda rgb, box: None)
    job = jobs.enqueue("faces")
    require(jobs._claim() == job.id, "setup: the faces job is next")
    jobs.run_job(job.id)

    with session_scope() as s:
        j = s.get(Job, job.id)
        assert j.state == "done", (j.error or "").strip().splitlines()[-1:]
        queued = {x.type for x in s.exec(select(Job).where(Job.state == "queued")).all()}
    assert {"cluster", "estimate"} <= queued  # what a finished faces job queues next
