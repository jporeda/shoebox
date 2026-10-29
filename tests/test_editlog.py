from sqlmodel import select

from shoebox import editlog, services
from shoebox.db import session_scope
from shoebox.ingest import run_ingest
from shoebox.models import EventInstance, EventKind, Photo


def ids(s):
    return [p.id for p in s.exec(select(Photo).order_by(Photo.id)).all()]


def test_bulk_date_and_undo(album_tree):
    run_ingest()
    with session_scope() as s:
        all_ids = ids(s)
        batch = services.bulk_update(s, all_ids[:3], date="1980s")
    with session_scope() as s:
        p = s.get(Photo, all_ids[0])
        assert (p.t_year, p.t_min, p.t_max, p.t_precision, p.t_source) == (1980, 1980, 1989, "decade", "manual")
        result = editlog.undo(s)
        assert result["undone"] == batch and result["reverted"] == 3 * 5 and not result["conflicts"]
    with session_scope() as s:
        assert all(s.get(Photo, i).t_year is None for i in all_ids[:3])


def test_undo_keeps_later_changes(album_tree):
    run_ingest()
    with session_scope() as s:
        pid = ids(s)[0]
        first = services.bulk_update(s, [pid], date="1981")
        services.bulk_update(s, [pid], date="1982")
    with session_scope() as s:
        result = editlog.undo(s, first)
        assert result["reverted"] == 0 and any(c["field"] == "t_year" for c in result["conflicts"])
        assert s.get(Photo, pid).t_year == 1982


def test_event_creates_instance_and_dates_photo(album_tree):
    run_ingest()
    with session_scope() as s:
        pid = ids(s)[0]
        xmas = s.exec(select(EventKind).where(EventKind.name == "Christmas")).one()
        services.bulk_update(s, [pid], date="1985", event_kind_id=xmas.id)
        p = s.get(Photo, pid)
        inst = s.get(EventInstance, p.event_instance_id)
        assert inst.label == "Christmas 1985" and (inst.month, inst.day) == (12, 25)
        # A second photo tagged with that instance and no date of its own inherits it.
        other = ids(s)[1]
        services.bulk_update(s, [other], event_instance_id=inst.id)
        o = s.get(Photo, other)
        assert (o.t_year, o.t_month, o.t_day, o.t_source) == (1985, 12, 25, "event")


def test_tags_shared_across_duplicate_group_but_rotation_is_not(album_tree):
    run_ingest()
    with session_scope() as s:
        a, b = ids(s)[:2]
        for pid in (a, b):
            p = s.get(Photo, pid)
            p.dup_group_id = a
            s.add(p)
    with session_scope() as s:
        services.bulk_update(s, [a], date="1990", rotate_by=90)
    with session_scope() as s:
        pa, pb = s.get(Photo, a), s.get(Photo, b)
        assert pa.t_year == pb.t_year == 1990
        assert (pa.rotation, pb.rotation) == (90, 0)


def test_year_plus_fixed_holiday_becomes_that_day_and_undoes_cleanly(album_tree):
    run_ingest()
    with session_scope() as s:
        pid = ids(s)[0]
        xmas = s.exec(select(EventKind).where(EventKind.name == "Christmas")).one()
        services.bulk_update(s, [pid], date="1985", event_kind_id=xmas.id)
        p = s.get(Photo, pid)
        assert (p.t_year, p.t_month, p.t_day, p.t_precision, p.t_source) == (1985, 12, 25, "day", "manual")
        result = editlog.undo(s)
        assert not result["conflicts"]
        assert s.get(Photo, pid).t_year is None and s.get(Photo, pid).event_instance_id is None


def test_rotation_still_undoes_after_the_faces_job_ran(album_tree):
    from shoebox.people import FACES_VERSION

    run_ingest()
    with session_scope() as s:
        pid = ids(s)[0]
        p = s.get(Photo, pid)
        p.faces_model_ver = FACES_VERSION  # faces were found after import
        s.add(p)
    with session_scope() as s:
        services.bulk_update(s, [pid], rotate_by=90)
    with session_scope() as s:
        p = s.get(Photo, pid)
        assert p.faces_model_ver == 0
        p.faces_model_ver = FACES_VERSION  # the faces job ran seconds later
        s.add(p)
    with session_scope() as s:
        result = editlog.undo(s)
        assert result["reverted"] == 2 and not result["conflicts"]
        assert s.get(Photo, pid).rotation == 0


def test_undo_that_reverts_nothing_stays_until_skipped(album_tree):
    run_ingest()
    with session_scope() as s:
        a, b = ids(s)[:2]
        older = services.bulk_update(s, [a], date="1970")
        newer = services.bulk_update(s, [b], caption="by hand")
    with session_scope() as s:
        p = s.get(Photo, b)
        p.caption = "changed outside the edit log"
        s.add(p)
    with session_scope() as s:
        result = editlog.undo(s)
        assert result["blocked"] and result["batch_id"] == newer and result["undone"] is None
    with session_scope() as s:
        assert editlog.undo(s)["batch_id"] == newer  # not silently used up
        assert editlog.undo(s, skip=True)["skipped"]
    with session_scope() as s:
        assert editlog.undo(s)["undone"] == older
        assert s.get(Photo, a).t_year is None


def test_rotation_must_be_quarter_turns(album_tree):
    import pytest

    run_ingest()
    with session_scope() as s, pytest.raises(ValueError):
        services.bulk_update(s, ids(s)[:1], rotate_by=45)
