"""A face that's one of two people, not known which (twins): "Ana or Ben"."""
import json

import numpy as np
import pytest
from sqlmodel import select

from conftest import api_client, make_image, reset_caches
from shoebox import backup, db, people
from shoebox.db import session_scope
from shoebox.export import run_export
from shoebox.faces import DetectedFace
from shoebox.ingest import run_ingest
from shoebox.matching import load_exemplars
from shoebox.models import Face, Person, Photo
from shoebox.people import merge_detections
from test_faces import tags

rng = np.random.default_rng(3)


def det(x, y=0.1):
    v = rng.normal(size=512)
    return DetectedFace(x, y, 0.2, 0.25, 0.9, (v / np.linalg.norm(v)).astype(np.float32), 30.0)


@pytest.fixture
def faces(env):
    """Three photos with one detected face each; returns the face ids by photo."""
    for i in range(3):
        make_image(env.photos_root / "A1" / f"p{i}.jpg", i)
    run_ingest()
    with session_scope() as s:
        for p in s.exec(select(Photo).order_by(Photo.rel_path)).all():
            merge_detections(s, p, [det(0.1)], "r")
        return [f.id for f in s.exec(select(Face).order_by(Face.photo_id)).all()]


def name_pair(c, face_ids):
    """Ana on face 0 (to have both people), then face_ids as "Ana or Ben"."""
    ana = c.post("/api/faces/assign", json={"face_ids": face_ids, "name": "Ana"}).json()["person"]
    res = c.post("/api/faces/assign", json={"face_ids": face_ids, "person_id": ana["id"], "alt_name": "Ben"})
    assert res.status_code == 200, res.text
    ben = next(p for p in c.get("/api/people").json() if p["name"] == "Ben")
    return ana, ben


def test_a_face_can_be_one_of_two_people_and_shows_under_both(faces):
    with api_client() as c:
        ana, ben = name_pair(c, [faces[0]])
        f = c.get(f"/api/people/{ana['id']}/faces").json()
        assert [(x["id"], x["person_name"], x["alt_name"]) for x in f] == [(faces[0], "Ana", "Ben")]
        assert [x["id"] for x in c.get(f"/api/people/{ben['id']}/faces").json()] == [faces[0]]
        people_by_name = {p["name"]: p for p in c.get("/api/people").json()}
        assert people_by_name["Ben"]["face_count"] == 1 == people_by_name["Ana"]["face_count"]
        # The grid's people filter finds the photo under either name.
        for pid in (ana["id"], ben["id"]):
            assert c.get("/api/photos", params={"people": str(pid)}).json()["total"] == 1
            assert c.get("/api/photos", params={"person_id": pid}).json()["total"] == 1
        # The same person twice, or a second person without a first, is refused.
        assert c.post("/api/faces/assign", json={"face_ids": [faces[1]], "person_id": ana["id"], "alt_person_id": ana["id"]}).status_code == 422
        assert c.post("/api/faces/assign", json={"face_ids": [faces[1]], "alt_person_id": ben["id"]}).status_code == 422


def test_resolving_who_it_is(faces):
    with api_client() as c:
        ana, ben = name_pair(c, faces)
        # "It's Ben": a plain naming clears the second person.
        c.post("/api/faces/assign", json={"face_ids": [faces[0]], "person_id": ben["id"]})
        # "Not Ana" (from Ana's page) leaves Ben, and Ana is never suggested again.
        c.post("/api/faces/reject", json={"face_ids": [faces[1]], "person_id": ana["id"]})
        # "Neither" clears both.
        c.post("/api/faces/reject", json={"face_ids": [faces[2]]})
    with session_scope() as s:
        f0, f1, f2 = (s.get(Face, i) for i in faces)
        assert (f0.person_id, f0.alt_person_id) == (ben["id"], None)
        assert (f1.person_id, f1.alt_person_id, f1.rejected_person_ids) == (ben["id"], None, [ana["id"]])
        assert (f2.person_id, f2.alt_person_id, sorted(f2.rejected_person_ids)) == (None, None, sorted([ana["id"], ben["id"]]))


def test_undo_restores_the_pair_and_removes_the_new_person(faces):
    with api_client() as c:
        ana, _ = name_pair(c, [faces[0]])
        c.post("/api/undo", json={})  # undoes "or Ben", and Ben, whom it created
        assert [p["name"] for p in c.get("/api/people").json()] == ["Ana"]
    with session_scope() as s:
        f = s.get(Face, faces[0])
        assert (f.person_id, f.alt_person_id) == (ana["id"], None)


def test_one_of_two_faces_teach_the_matcher_nothing(faces):
    with api_client() as c:
        ana, _ = name_pair(c, [faces[0]])
        c.post("/api/faces/assign", json={"face_ids": [faces[1]], "person_id": ana["id"]})
    with session_scope() as s:
        ex = load_exemplars(list(s.exec(select(Face)).all()), {})
        assert list(ex.person_ids) == [ana["id"]]  # only the face known to be Ana
        assert set(people.person_centroids(s)) == {ana["id"]}


def test_merging_one_twin_into_the_other_leaves_just_one(faces):
    with api_client() as c:
        ana, ben = name_pair(c, [faces[0]])
        c.post(f"/api/people/{ben['id']}/merge", json={"into_id": ana["id"]})
    with session_scope() as s:
        f = s.get(Face, faces[0])
        assert (f.person_id, f.alt_person_id) == (ana["id"], None)


def test_export_names_the_pair_together_and_truth_round_trips(faces, env, tmp_path, monkeypatch):
    with api_client() as c:
        name_pair(c, [faces[0]])
    run_export()
    index = json.loads((env.backup_dir / "export" / "export-index.json").read_text())
    with session_scope() as s:
        photo_id = s.get(Face, faces[0]).photo_id
    out = env.backup_dir / "export" / index[str(photo_id)]["path"]
    t = tags(out, "XMP-mwg-rs:RegionInfo", "XMP-iptcExt:PersonInImage")
    assert t["XMP-mwg-rs:RegionInfo"]["RegionList"][0]["Name"] == "Ana or Ben"
    assert t["XMP-iptcExt:PersonInImage"] in ("Ana or Ben", ["Ana or Ben"])  # never Ana or Ben alone

    backup.run_backup("t")
    truth = json.loads((env.backup_dir / "truth" / "truth.json").read_text())
    monkeypatch.setenv("SHOEBOX_DATA_DIR", str(tmp_path / "fresh"))
    reset_caches()
    db.migrate()
    run_ingest()
    backup.restore_truth(truth)
    with session_scope() as s:
        names = {p.id: p.name for p in s.exec(select(Person)).all()}
        f = s.exec(select(Face)).one()
        assert (names[f.person_id], names[f.alt_person_id]) == ("Ana", "Ben")


def test_people_list_their_known_twins(faces):
    with api_client() as c:
        ana, ben = name_pair(c, [faces[0]])
        cy = c.post("/api/faces/assign", json={"face_ids": [faces[1]], "name": "Cy"}).json()["person"]
        twins = {p["name"]: p["twin_ids"] for p in c.get("/api/people").json()}
        assert twins == {"Ana": [ben["id"]], "Ben": [ana["id"]], "Cy": []}
        # Once nobody is "one of the two" any more, they're no longer offered as a pair.
        c.post("/api/faces/assign", json={"face_ids": [faces[0]], "person_id": ana["id"]})
        assert all(p["twin_ids"] == [] for p in c.get("/api/people").json())
        assert cy["twin_ids"] == []


def test_a_pair_is_always_named_in_one_order(faces, env):
    with api_client() as c:
        ana, ben = name_pair(c, [faces[0]])
        # Named the other way round: still stored, shown and exported as "Ana or Ben".
        c.post("/api/faces/assign", json={"face_ids": [faces[1]], "person_id": ben["id"], "alt_person_id": ana["id"]})
    with session_scope() as s:
        for i in faces[:2]:
            f = s.get(Face, i)
            assert (f.person_id, f.alt_person_id) == (ana["id"], ben["id"])
        f = s.get(Face, faces[1])
        f.person_id, f.alt_person_id = ben["id"], ana["id"]  # stored the other way before this rule
        s.add(f)
    run_export()
    index = json.loads((env.backup_dir / "export" / "export-index.json").read_text())
    with session_scope() as s:
        photo_id = s.get(Face, faces[1]).photo_id
    t = tags(env.backup_dir / "export" / index[str(photo_id)]["path"], "XMP-mwg-rs:RegionInfo")
    assert t["XMP-mwg-rs:RegionInfo"]["RegionList"][0]["Name"] == "Ana or Ben"


def test_person_counts_photos_not_faces_and_points_out_a_name_used_twice(faces):
    with session_scope() as s:  # a second face on the first photo
        f0 = s.get(Face, faces[0])
        s.add(Face(photo_id=f0.photo_id, x=0.6, y=0.1, w=0.2, h=0.25, source="drawn"))
        s.flush()
        extra = s.exec(select(Face).where(Face.photo_id == f0.photo_id, Face.id != f0.id)).one().id
    with api_client() as c:
        sam = c.post("/api/faces/assign", json={"face_ids": [faces[0], extra, faces[1]], "name": "Sam"}).json()["person"]
        assert (sam["face_count"], sam["photo_count"]) == (3, 2)
        with session_scope() as s:
            photo_id = s.get(Face, faces[0]).photo_id
        assert sam["double_photo_ids"] == [photo_id]
        assert c.get("/api/photos", params={"person_id": sam["id"]}).json()["total"] == sam["photo_count"]
        # A photo gone from the folder drops out of the person page, as it does from the grid.
        with session_scope() as s:
            p = s.get(Photo, s.get(Face, faces[1]).photo_id)
            p.missing = True
            s.add(p)
        sam = next(p for p in c.get("/api/people").json() if p["name"] == "Sam")
        assert sam["photo_count"] == 1 == c.get("/api/photos", params={"person_id": sam["id"]}).json()["total"]
        assert len(c.get(f"/api/people/{sam['id']}/faces").json()) == 2


# --- matching can't tell twins apart; one twin confirmed in a photo means the other -----------------

from shoebox.faces import to_blob  # noqa: E402
from shoebox.matching import Match, run_matching, twin_guess  # noqa: E402


def test_twin_guess():
    twins = {1: {2}, 2: {1}}
    close, clear = Match(1, 0.6, 0.03, runner_up=2), Match(1, 0.6, 0.3, runner_up=2)
    assert twin_guess(close, twins, set()) == (1, 2)  # too close to call: "Ana or Ben?"
    assert twin_guess(clear, twins, set()) == (1, None)  # clearly Ana
    assert twin_guess(close, twins, {1}) == (2, None)  # Ana's already in the photo: Ben
    assert twin_guess(clear, twins, {1}) == (2, None)
    assert twin_guess(close, twins, {1, 2}) is None  # both there already
    assert twin_guess(Match(3, 0.6, 0.3, runner_up=1), twins, {3}) == (3, None)  # no twin: as before


def unit(seed):
    v = np.random.default_rng(seed).normal(size=512)
    return v / np.linalg.norm(v)


def test_matching_suggests_the_pair_when_twins_look_alike(env):
    for i in range(4):
        make_image(env.photos_root / "A1" / f"p{i}.jpg", i)
    run_ingest()
    look = unit(1)  # the twins look the same to the face model
    with session_scope() as s:
        p0, p1, p2, p3 = s.exec(select(Photo).order_by(Photo.rel_path)).all()
        ana, ben = Person(name="Ana"), Person(name="Ben")
        s.add_all([ana, ben])
        s.flush()
        def face(p, x, **kw):
            noise = np.random.default_rng(int(x * 100) + p.id).normal(size=512) * 0.02
            f = Face(photo_id=p.id, x=x, y=0.1, w=0.2, h=0.25, embedding=to_blob((look + noise).astype(np.float32)), **kw)
            s.add(f)
            return f
        face(p0, 0.1, person_id=ana.id, source="manual")
        face(p0, 0.5, person_id=ben.id, source="manual")
        face(p1, 0.1, person_id=ana.id, alt_person_id=ben.id, source="manual")  # what makes them twins
        alone = face(p2, 0.1)  # nobody else in the photo
        with_ana = face(p3, 0.5)  # Ana is confirmed on the other face
        face(p3, 0.1, person_id=ana.id, source="manual")
        s.flush()
        alone_id, with_ana_id, ana_id, ben_id = alone.id, with_ana.id, ana.id, ben.id
    run_matching()
    with session_scope() as s:
        a, w = s.get(Face, alone_id), s.get(Face, with_ana_id)
        assert (a.person_id, a.suggested_person_id, a.suggested_alt_person_id) == (None, ana_id, ben_id)
        assert (w.person_id, w.suggested_person_id, w.suggested_alt_person_id) == (None, ben_id, None)
    with api_client() as c:
        shown = {f["id"]: f for f in c.get(f"/api/photos/{a.photo_id}/faces").json()}
        assert (shown[alone_id]["suggested_name"], shown[alone_id]["suggested_alt_name"]) == ("Ana", "Ben")
        c.post("/api/faces/accept", json={"face_ids": [alone_id]})  # yes: one of the two
    with session_scope() as s:
        a = s.get(Face, alone_id)
        assert (a.person_id, a.alt_person_id, a.suggested_person_id, a.suggested_alt_person_id) == (ana_id, ben_id, None, None)


def test_confirming_one_twin_in_a_photo_makes_the_other_face_the_other_twin(env):
    for i in range(2):
        make_image(env.photos_root / "A1" / f"p{i}.jpg", i)
    run_ingest()
    with session_scope() as s:
        p0, p1 = s.exec(select(Photo).order_by(Photo.rel_path)).all()
        ana, ben = Person(name="Ana"), Person(name="Ben")
        s.add_all([ana, ben])
        s.flush()
        left = Face(photo_id=p0.id, x=0.1, y=0.1, w=0.2, h=0.2, person_id=ana.id, alt_person_id=ben.id, source="manual")
        right = Face(photo_id=p0.id, x=0.6, y=0.1, w=0.2, h=0.2, source="drawn")
        guess = Face(photo_id=p1.id, x=0.1, y=0.1, w=0.2, h=0.2, suggested_person_id=ana.id, suggested_alt_person_id=ben.id)
        other = Face(photo_id=p1.id, x=0.6, y=0.1, w=0.2, h=0.2, source="drawn")
        s.add_all([left, right, guess, other])
        s.flush()
        ids = (left.id, right.id, guess.id, other.id, ana.id, ben.id)
    left_id, right_id, guess_id, other_id, ana_id, ben_id = ids
    with api_client() as c:
        c.post("/api/faces/assign", json={"face_ids": [right_id], "person_id": ana_id})
        c.post("/api/faces/assign", json={"face_ids": [other_id], "person_id": ben_id})
    with session_scope() as s:
        l, g = s.get(Face, left_id), s.get(Face, guess_id)
        assert (l.person_id, l.alt_person_id, l.source) == (ben_id, None, "manual")  # the other twin
        assert (g.person_id, g.suggested_person_id, g.suggested_alt_person_id) == (None, ana_id, None)  # "Ana?"
    with api_client() as c:
        c.post("/api/undo", json={})  # Ben on the other face, and what it implied
        c.post("/api/undo", json={})  # Ana on the right face, and what it implied
    with session_scope() as s:
        l, g = s.get(Face, left_id), s.get(Face, guess_id)
        assert (l.person_id, l.alt_person_id) == (ana_id, ben_id)
        assert (g.suggested_person_id, g.suggested_alt_person_id) == (ana_id, ben_id)
    with api_client() as c:
        c.post("/api/faces/assign", json={"face_ids": [other_id], "person_id": ana_id})
    with session_scope() as s:
        g = s.get(Face, guess_id)
        assert (g.suggested_person_id, g.suggested_alt_person_id) == (ben_id, None)  # Ana's here: "Ben?"
    with api_client() as c:
        # Marking a face "Ana or Ben" where Ana is already confirmed makes it Ben straight away.
        c.post("/api/faces/assign", json={"face_ids": [guess_id], "person_id": ana_id, "alt_person_id": ben_id})
    with session_scope() as s:
        g = s.get(Face, guess_id)
        assert (g.person_id, g.alt_person_id) == (ben_id, None)
        g.person_id, g.source, g.suggested_person_id, g.suggested_alt_person_id = None, "auto", ana_id, ben_id
        s.add(g)
    with api_client() as c:
        c.post("/api/faces/reject", json={"face_ids": [guess_id]})  # "Ana or Ben?" -> no: neither
    with session_scope() as s:
        g = s.get(Face, guess_id)
        assert (g.person_id, g.suggested_person_id, sorted(g.rejected_person_ids)) == (None, None, sorted([ana_id, ben_id]))


def test_setting_one_of_two_detected_twins_to_the_pair_sets_the_other_too(env):
    make_image(env.photos_root / "A1" / "p0.jpg", 0)
    run_ingest()
    with session_scope() as s:
        p = s.exec(select(Photo)).one()
        ana, ben, cy = Person(name="Ana"), Person(name="Ben"), Person(name="Cy")
        s.add_all([ana, ben, cy])
        s.flush()
        # Some earlier face made Ana and Ben known twins.
        s.add(Face(photo_id=p.id, x=0.8, y=0.8, w=0.1, h=0.1, person_id=ana.id, alt_person_id=ben.id, source="manual", ignored=True))
        left = Face(photo_id=p.id, x=0.1, y=0.1, w=0.2, h=0.2, person_id=ana.id, source="auto", confidence=0.7)  # app: Ana
        right = Face(photo_id=p.id, x=0.4, y=0.1, w=0.2, h=0.2, suggested_person_id=ben.id, source="auto")  # app: Ben?
        third = Face(photo_id=p.id, x=0.7, y=0.1, w=0.2, h=0.2, suggested_person_id=cy.id, source="auto")  # someone else
        s.add_all([left, right, third])
        s.flush()
        left_id, right_id, third_id, ana_id, ben_id, cy_id = left.id, right.id, third.id, ana.id, ben.id, cy.id
    with api_client() as c:
        c.post("/api/faces/assign", json={"face_ids": [left_id], "person_id": ana_id, "alt_person_id": ben_id})
        assert "and the twin on 1 other face" in c.get("/api/edits").json()[0]["description"]
    with session_scope() as s:
        r, t = s.get(Face, right_id), s.get(Face, third_id)
        assert (r.person_id, r.alt_person_id, r.suggested_person_id, r.source) == (ana_id, ben_id, None, "accepted")
        assert (t.person_id, t.suggested_person_id) == (None, cy_id)  # not a twin: left alone
    with api_client() as c:
        c.post("/api/undo", json={})
    with session_scope() as s:
        r = s.get(Face, right_id)
        assert (r.person_id, r.alt_person_id, r.suggested_person_id, r.source) == (None, None, ben_id, "auto")
    with api_client() as c:
        c.post("/api/faces/assign", json={"face_ids": [left_id], "person_id": ana_id, "alt_person_id": ben_id})
        # Then it turns out the right one is Ben: so the left one is Ana.
        c.post("/api/faces/assign", json={"face_ids": [right_id], "person_id": ben_id})
    with session_scope() as s:
        l, r = s.get(Face, left_id), s.get(Face, right_id)
        assert (l.person_id, l.alt_person_id) == (ana_id, None)
        assert (r.person_id, r.alt_person_id) == (ben_id, None)


def test_two_twins():
    from shoebox.matching import two_twins
    twins = {1: {2}, 2: {1}}
    clear_a, clear_b = Match(1, 0.7, 0.3, runner_up=3), Match(2, 0.7, 0.3, runner_up=3)
    close_a = Match(1, 0.6, 0.02, runner_up=2)
    assert two_twins(clear_a, clear_a, twins, [set(), set()]) == (1, 2)  # both look like Ana: one is Ben
    assert two_twins(close_a, clear_b, twins, [set(), set()]) == (1, 2)  # one too close to call
    assert two_twins(clear_a, clear_b, twins, [set(), set()]) is None  # clearly one each
    assert two_twins(clear_a, None, twins, [set(), set()]) is None  # the other face matches no one
    assert two_twins(Match(3, 0.7, 0.3), Match(3, 0.7, 0.3), twins, [set(), set()]) is None  # not twins
    assert two_twins(clear_a, clear_a, twins, [{2}, set()]) is None  # told it isn't Ben


def test_a_photo_of_just_the_two_twins_suggests_the_pair_on_both_faces(env):
    for i in range(4):
        make_image(env.photos_root / "A1" / f"p{i}.jpg", i)
    run_ingest()
    ana_look, ben_look = unit(11), unit(12)  # the face model can tell them apart, usually
    with session_scope() as s:
        p0, p1, p2, p3 = s.exec(select(Photo).order_by(Photo.rel_path)).all()
        ana, ben = Person(name="Ana"), Person(name="Ben")
        s.add_all([ana, ben])
        s.flush()
        n = iter(range(1000))
        def face(p, x, look, **kw):
            noise = np.random.default_rng(next(n)).normal(size=512) * 0.01
            f = Face(photo_id=p.id, x=x, y=0.1, w=0.1, h=0.1, embedding=to_blob((look + noise).astype(np.float32)), **kw)
            s.add(f)
            return f
        face(p0, 0.1, ana_look, person_id=ana.id, source="manual")
        face(p0, 0.5, ben_look, person_id=ben.id, source="manual")
        face(p1, 0.1, ana_look, person_id=ana.id, alt_person_id=ben.id, source="manual")  # makes them twins
        both = [face(p2, 0.1, ana_look), face(p2, 0.5, ana_look)]  # just the two of them, both look like Ana
        crowd = [face(p3, 0.1, ana_look), face(p3, 0.4, ana_look), face(p3, 0.7, unit(13))]  # three people
        s.flush()
        both_ids, crowd_ids, ana_id, ben_id = [f.id for f in both], [f.id for f in crowd], ana.id, ben.id
    run_matching()
    with session_scope() as s:
        got = lambda i: (s.get(Face, i).person_id, s.get(Face, i).suggested_person_id, s.get(Face, i).suggested_alt_person_id)
        assert [got(i) for i in both_ids] == [(None, ana_id, ben_id)] * 2
        assert got(crowd_ids[0])[2] is None and got(crowd_ids[1])[2] is None  # not two faces: as before


def test_twin_margin_is_a_setting_that_defaults_to_the_built_in_one(env):
    from shoebox.matching import TWIN_MARGIN
    for i in range(3):
        make_image(env.photos_root / "A1" / f"p{i}.jpg", i)
    run_ingest()
    look = unit(21)  # the twins look the same to the face model
    with session_scope() as s:
        p0, p1, p2 = s.exec(select(Photo).order_by(Photo.rel_path)).all()
        ana, ben = Person(name="Ana"), Person(name="Ben")
        s.add_all([ana, ben])
        s.flush()
        n = iter(range(1000))
        def face(p, x, **kw):
            noise = np.random.default_rng(next(n)).normal(size=512) * 0.02
            f = Face(photo_id=p.id, x=x, y=0.1, w=0.1, h=0.1, embedding=to_blob((look + noise).astype(np.float32)), **kw)
            s.add(f)
            return f
        face(p0, 0.1, person_id=ana.id, source="manual")
        face(p0, 0.5, person_id=ben.id, source="manual")
        face(p1, 0.1, person_id=ana.id, alt_person_id=ben.id, source="manual")
        alone = face(p2, 0.1)
        s.flush()
        alone_id, ben_id = alone.id, ben.id
    with api_client() as c:
        assert c.get("/api/settings").json() == {"twin_margin_percent": round(TWIN_MARGIN * 100), "twin_margin_default": round(TWIN_MARGIN * 100)}
        assert c.put("/api/settings", json={"twin_margin_percent": 51}).status_code == 422
        assert c.put("/api/settings", json={"twin_margin_percent": 0}).json()["twin_margin_percent"] == 0
        assert any(j["type"] == "estimate" for j in c.get("/api/jobs").json())  # suggestions are redone
    run_matching()
    with session_scope() as s:
        assert s.get(Face, alone_id).suggested_alt_person_id is None  # 0: never "Ana or Ben?" for one face
    with api_client() as c:
        c.put("/api/settings", json={"twin_margin_percent": 30})
    run_matching()
    with session_scope() as s:
        assert s.get(Face, alone_id).suggested_alt_person_id is not None
