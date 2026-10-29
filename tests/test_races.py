"""Worker jobs spend seconds per photo on OCR or detection; an edit made meanwhile must win."""

import numpy as np
from sqlmodel import select

from conftest import api_client
from shoebox import matching
from shoebox.backs import run_read_backs
from shoebox.db import session_scope
from shoebox.faces import DetectedFace, to_blob
from shoebox.ingest import run_ingest
from shoebox.matching import run_matching
from shoebox.models import Face, LooseBack, Person, Photo
from shoebox.orient import run_orient
from shoebox.people import FACES_VERSION, assign, run_faces

OCR_SAYS_1985 = {"candidates": [{"month": 3, "year": 1985, "votes": 3, "four_digit": True}],
                 "accepted": {"year": 1985, "month": 3, "confidence": 0.9}}


def test_back_dates_typed_during_ocr_win(album_tree, env):
    run_ingest()
    with session_scope() as s:
        loose_id = s.exec(select(LooseBack)).first().id
        paired_id = s.exec(select(Photo).where(Photo.back_sha256 != None)).first().id  # noqa: E711
    with api_client() as client:
        def reader(path):
            if "IMG_0099" in path.name:
                assert client.patch(f"/api/loose-backs/{loose_id}", json={"date": "1978-10"}).status_code == 200
            else:
                assert client.patch(f"/api/paired-backs/{paired_id}", json={"date": "1979"}).status_code == 200
            return OCR_SAYS_1985

        result = run_read_backs(reader=reader)
    assert result["dates_accepted"] < 3  # the two typed dates weren't overwritten
    with session_scope() as s:
        lb, p = s.get(LooseBack, loose_id), s.get(Photo, paired_id)
        assert (lb.d_year, lb.d_month, lb.d_source) == (1978, 10, "manual")
        assert (p.back_year, p.back_source) == (1979, "manual")
        assert lb.candidates[0]["year"] == 1985  # the reading is still offered as a chip


def test_rotation_chosen_during_auto_rotate_wins(album_tree, env):
    run_ingest()
    with session_scope() as s:
        pid = s.exec(select(Photo).order_by(Photo.id)).first().id
    fired = []
    with api_client() as client:
        def scorer(arr):
            if not fired:
                fired.append(1)
                assert client.patch("/api/photos", json={"ids": [pid], "rotate_by": 180}).status_code == 200
            return 1.0 if arr.shape[0] > arr.shape[1] else 0.0  # would pick 90

        run_orient(scorer=scorer)
    with session_scope() as s:
        p = s.get(Photo, pid)
        assert (p.rotation, p.rotation_source) == (180, "manual")


def test_rotation_during_face_detection_gets_detected_again(album_tree, env):
    run_ingest()
    with session_scope() as s:
        pid = s.exec(select(Photo).order_by(Photo.id)).first().id  # detected first
    calls = []
    with api_client() as client:
        def detector(rgb):
            if not calls:
                assert client.patch("/api/photos", json={"ids": [pid], "rotate_by": 90}).status_code == 200
            calls.append(1)
            return [DetectedFace(x=0.1, y=0.1, w=0.2, h=0.2, det_score=0.9, embedding=np.ones(512, np.float32), age=30)]

        run_faces(detector=detector)
    with session_scope() as s:
        assert s.get(Photo, pid).faces_model_ver == 0  # still waiting for detection the new way up
        assert s.exec(select(Face).where(Face.photo_id == pid)).first() is None
        others = s.exec(select(Photo).where(Photo.id != pid)).all()
        assert all(o.faces_model_ver == FACES_VERSION for o in others)


def test_face_named_during_matching_keeps_its_name(album_tree, env, monkeypatch):
    run_ingest()
    vec = np.random.default_rng(3).normal(size=512)
    with session_scope() as s:
        photos = [p.id for p in s.exec(select(Photo).order_by(Photo.id)).all()]
        ann, bob = Person(name="Ann"), Person(name="Bob")
        s.add_all([ann, bob])
        s.flush()
        ids = []
        for pid, person, source in [(photos[0], ann.id, "manual"), (photos[1], ann.id, "manual"), (photos[2], None, "auto")]:
            f = Face(photo_id=pid, x=0.1, y=0.1, w=0.2, h=0.2, person_id=person, source=source, est_age=30,
                     embedding=to_blob((vec / np.linalg.norm(vec)).astype(np.float32)))
            s.add(f)
            s.flush()
            ids.append(f.id)
        bob_id = bob.id
    real = matching.load_exemplars

    def load_then_user_names(*args, **kwargs):
        ex = real(*args, **kwargs)
        with session_scope() as s:  # the user names the face on another connection meanwhile
            assign(s, [ids[2]], bob_id)
        return ex

    monkeypatch.setattr(matching, "load_exemplars", load_then_user_names)
    run_matching()
    with session_scope() as s:
        f = s.get(Face, ids[2])
        assert (f.person_id, f.source) == (bob_id, "manual")
