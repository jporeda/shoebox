import numpy as np
import pytest
from sqlmodel import select

from conftest import api_client, make_image
from shoebox import services
from shoebox.db import session_scope
from shoebox.estimate import Evidence, combine, run_estimates
from shoebox.evaluate import run_evaluate
from shoebox.faces import DetectedFace, to_blob
from shoebox.ingest import run_ingest
from shoebox.matching import decide, run_matching
from shoebox.models import Face, Person, Photo
from shoebox.people import accept, assign, reject, revert_run

rng = np.random.default_rng(1)


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


@pytest.fixture
def album(env):
    root = env.photos_root
    for i in range(10):
        make_image(root / "A1" / f"IMG_{i:04d}.jpg", i)
    run_ingest()
    with session_scope() as s:
        return [p.id for p in s.exec(select(Photo).order_by(Photo.list_index)).all()]


def p(pid):
    with session_scope() as s:
        return s.get(Photo, pid)


def test_combine_agreement_raises_confidence_and_conflict_is_reported():
    a = Evidence("own_back", 1978, 10, 1978, 1978, 0.9, "back says 1978-10")
    b = Evidence("neighbours", 1978, None, 1977, 1979, 0.6, "between 1977 and 1979")
    c = Evidence("file_name", 1990, None, 1990, 1990, 0.35, "file name says 1990")
    one = combine([a])
    both = combine([a, b])
    conflict = combine([a, b, c])
    assert (one.year, one.month, one.confidence) == (1978, 10, 0.9)
    assert both.confidence == 0.95 and both.precision == "month"
    assert conflict.year == 1978 and conflict.confidence < both.confidence
    assert "but file name says 1990" in conflict.source


def test_album_neighbours_interpolate_between_trusted_dates(album):
    with session_scope() as s:
        services.bulk_update(s, [album[0]], date="1980")
        services.bulk_update(s, [album[4]], date="1984")
        services.bulk_update(s, [album[7]], date="1984")
    run_estimates()
    assert (p(album[2]).i_year, p(album[2]).i_min, p(album[2]).i_max) == (1982, 1980, 1984)
    assert "between 1980 and 1984" in p(album[2]).i_source
    six = p(album[6])  # between two 1984s: same year, high confidence
    assert six.i_year == 1984 and six.i_confidence >= 0.7 and six.i_precision == "year"
    nine = p(album[9])  # past the last anchor: weaker
    assert nine.i_year == 1984 and nine.i_confidence < six.i_confidence
    assert p(album[0]).t_year == 1980  # trusted values untouched


def test_estimates_are_never_anchors(album):
    with session_scope() as s:
        services.bulk_update(s, [album[0]], date="1980")
    run_estimates()
    first = p(album[9]).i_year
    for _ in range(3):  # re-running must not drift (estimates don't feed back in)
        run_estimates()
    assert p(album[9]).i_year == first == 1980


def test_age_anchor_from_named_person(album):
    with session_scope() as s:
        sam = Person(name="Sam", birth_year=1972)
        s.add(sam)
        s.flush()
        s.add(Face(photo_id=album[5], x=0.1, y=0.1, w=0.2, h=0.2, person_id=sam.id, source="manual", est_age=6.4,
                   embedding=to_blob(unit(rng.normal(size=512)))))
        s.add(Face(photo_id=album[8], x=0.1, y=0.1, w=0.2, h=0.2, person_id=sam.id, source="auto", est_age=30,
                   embedding=to_blob(unit(rng.normal(size=512)))))
    run_estimates()
    five = p(album[5])
    assert (five.i_year, five.i_min, five.i_max) == (1978, 1976, 1980) and "Sam looks about 6" in five.i_source
    assert p(album[8]).i_year is None  # automatic names don't date photos


def faces_for(album, people_vecs):
    """people_vecs: list of (photo index, base vector, person or None, source, est_age)."""
    with session_scope() as s:
        ids = []
        for idx, base, person_id, source, age in people_vecs:
            f = Face(photo_id=album[idx], x=0.1, y=0.1, w=0.2, h=0.2, person_id=person_id, source=source,
                     est_age=age, embedding=to_blob(unit(base + rng.normal(size=512) * 0.35)))
            s.add(f)
            s.flush()
            ids.append(f.id)
        return ids


def test_matching_auto_names_suggests_young_and_never_learns_from_itself(album):
    A, B = rng.normal(size=512), rng.normal(size=512)
    with session_scope() as s:
        a, b = Person(name="Ann"), Person(name="Bob")
        s.add_all([a, b])
        s.flush()
        aid, bid = a.id, b.id
    named = faces_for(album, [(0, A, aid, "manual", 30), (1, A, aid, "manual", 31), (2, B, bid, "manual", 30), (3, B, bid, "manual", 33)])
    unknown = faces_for(album, [(4, A, None, "auto", 32), (5, B, None, "auto", 4), (6, A, None, "drawn", 30)])
    result = run_matching()
    assert result["auto"] == 1 and result["suggested"] == 2 and result["exemplars"] == 4
    with session_scope() as s:
        f4, f5, f6 = (s.get(Face, i) for i in unknown)
        assert (f4.person_id, f4.source) == (aid, "auto")
        assert (f5.person_id, f5.suggested_person_id) == (None, bid)  # looks 4: suggestion only
        assert (f6.person_id, f6.suggested_person_id) == (None, aid)  # drawn: a person names it
    assert run_matching()["exemplars"] == 4  # the auto name did not become an exemplar
    # reject: Ann is never suggested again for that face; accept: becomes an exemplar
    with session_scope() as s:
        reject(s, [unknown[0]])
        accept(s, [unknown[1]])
    run_matching()
    with session_scope() as s:
        f4, f5 = s.get(Face, unknown[0]), s.get(Face, unknown[1])
        assert f4.person_id is None and f4.rejected_person_ids == [aid] and f4.suggested_person_id != aid
        assert (f5.person_id, f5.source) == (bid, "accepted")
    assert run_matching()["exemplars"] == 5


def test_decide_thresholds():
    from shoebox.matching import Match

    assert decide(Match(1, 0.7, 0.2), 30) == "auto"
    assert decide(Match(1, 0.7, 0.05), 30) == "suggest"  # too close to someone else
    assert decide(Match(1, 0.7, 0.2), 3) == "suggest"  # young child
    assert decide(Match(1, 0.2, 0.2), 30) == "none"


def test_revert_run_clears_that_runs_names(album):
    A = rng.normal(size=512)
    with session_scope() as s:
        a = Person(name="Ann")
        s.add(a)
        s.flush()
        aid = a.id
    faces_for(album, [(0, A, aid, "manual", 30), (1, A, aid, "manual", 30), (2, A, None, "auto", 30)])
    run_id = run_matching()["run_id"]
    with session_scope() as s:
        auto = s.exec(select(Face).where(Face.source == "auto", Face.person_id == aid)).first()
        assert auto is not None
        revert_run(s, run_id)
    with session_scope() as s:
        assert s.exec(select(Face).where(Face.source == "auto", Face.person_id == aid)).first() is None
    run_matching()  # the next run (almost any edit triggers one) only suggests for that face
    with session_scope() as s:
        f = s.get(Face, auto.id)
        assert (f.person_id, f.suggested_person_id, f.source) == (None, aid, "reverted")
        accept(s, [auto.id])
    with session_scope() as s:
        assert (s.get(Face, auto.id).person_id, s.get(Face, auto.id).source) == (aid, "accepted")
        with pytest.raises(ValueError):
            revert_run(s, run_estimates()["run_id"])


def test_evaluate_reports_holdout_accuracy(album):
    with session_scope() as s:
        for i, y in zip(range(10), [1980, 1980, 1981, 1981, 1982, 1982, 1983, 1983, 1984, 1984]):
            services.bulk_update(s, [album[i]], date=str(y))
    result = run_evaluate(fraction=0.3, seed=1)
    d = result["dates"]
    assert d["hidden"] == 3 and d["overall"]["n"] == 3 and d["overall"]["within_1_year"] == 1.0
    assert "need at least" in result["faces"]["status"]


def test_review_api_and_confirmed_only_export(album, env):
    A = rng.normal(size=512)
    with session_scope() as s:
        a = Person(name="Ann")
        s.add(a)
        s.flush()
        aid = a.id
    faces_for(album, [(0, A, aid, "manual", 30), (1, A, aid, "manual", 30)])
    auto_id = faces_for(album, [(2, A, None, "auto", 30)])[0]
    run_matching()
    with api_client() as client:
        groups = client.get("/api/faces/review").json()
        assert [(g["name"], [f["id"] for f in g["faces"]]) for g in groups] == [("Ann", [auto_id])]
        assert client.get("/api/faces/stats").json()["to_review"] == 1
        from shoebox.export import build_plan

        with session_scope() as s:
            services.bulk_update(s, [album[2]], date="1990")
            plan = {it.photo_id: it for it in build_plan(s)}
        assert plan[album[2]].people == []  # automatic name stays in the app
        client.post("/api/faces/accept", json={"face_ids": [auto_id]})
        assert client.get("/api/faces/review").json() == []
        with session_scope() as s:
            plan = {it.photo_id: it for it in build_plan(s)}
        assert plan[album[2]].people == ["Ann"]
        page = client.get("/api/photos", params={"order": "confidence", "status": "inferred"}).json()
        confs = [x["confidence"] for x in page["items"]]
        assert confs == sorted(confs)


def test_ocr_back_dates_are_weaker_anchors_and_not_double_counted(album):
    with session_scope() as s:
        for idx, y in ((0, 1975), (8, 1975)):
            ph = s.get(Photo, album[idx])
            ph.back_year, ph.back_source = y, "ocr"
            s.add(ph)
        services.bulk_update(s, [album[9]], date="1976")
    run_estimates()
    four = p(album[4])
    assert four.i_year == 1975 and "between 1975 (ocr) and 1975 (ocr)" in four.i_source
    with session_scope() as s:  # same gap but confirmed anchors -> more confident
        services.bulk_update(s, [album[0], album[8]], date="1975")
    run_estimates()
    assert p(album[4]).i_confidence > four.i_confidence


def test_naming_a_face_by_hand_clears_its_suggestion(album):
    with session_scope() as s:
        ann = Person(name="Ann")
        s.add(ann)
        s.flush()
        f = Face(photo_id=album[0], x=0.1, y=0.1, w=0.2, h=0.2, source="auto", suggested_person_id=ann.id)
        s.add(f)
        s.flush()
        assign(s, [f.id], ann.id)
    with api_client() as client:
        assert client.get("/api/faces/review").json() == []  # nothing left to answer


def test_evaluation_does_not_read_the_answer_off_the_hidden_photo(album):
    from shoebox.evaluate import evaluate_dates

    with session_scope() as s:
        for i, pid in enumerate(album[:6]):
            photo = s.get(Photo, pid)
            photo.back_year, photo.back_source = 1980 + i, "manual"
            s.add(photo)
            services.bulk_update(s, [pid], date=str(1980 + i), source="back")  # "Use as date"
    result = evaluate_dates(fraction=1.0, seed=0)
    assert "own_back" not in result["by_main_evidence"]
