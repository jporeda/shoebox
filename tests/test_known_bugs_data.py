"""Known bugs in import, dates and concurrent edits, reproduced. Each test states the behaviour we
want and is a strict xfail until its fix lands: the fix removes the marker (and may move the
test next to its area's tests).

IDs are from the team review (J = Jordan, M = Morgan); "plan" is the item in the team plan.
"""

import threading

import pytest
from sqlmodel import select

from conftest import api_client, make_image, require
from shoebox import editlog
from shoebox.dates import MAX_YEAR, MIN_YEAR, DateParseError, parse_partial_date
from shoebox.db import session_scope
from shoebox.ingest import run_ingest
from shoebox.models import LooseBack, Photo

# --- typed loose-back dates ------------------------------------------------------------


def set_loose_back_date(rel, text):
    with session_scope() as s:
        loose_id = s.exec(select(LooseBack.id).where(LooseBack.rel_path == rel)).one()
    with api_client() as client:
        require(client.patch(f"/api/loose-backs/{loose_id}", json={"date": text}).status_code == 200,
                f"setup: date typed for {rel}")


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B8 / plan 4.4: Typed loose-back dates follow the file name, not the scan")
def test_swapped_loose_backs_keep_their_typed_dates(env):
    root = env.photos_root
    make_image(root / "A" / "IMG_0001.jpg", 1)
    make_image(root / "A" / "IMG_0005_back.jpg", 105)
    make_image(root / "A" / "IMG_0009_back.jpg", 109, size=(330, 250))
    run_ingest()
    set_loose_back_date("A/IMG_0005_back.jpg", "1971")
    set_loose_back_date("A/IMG_0009_back.jpg", "1979")
    with session_scope() as s:
        sha = {lb.rel_path: lb.sha256 for lb in s.exec(select(LooseBack)).all()}
    a, b, tmp = root / "A/IMG_0005_back.jpg", root / "A/IMG_0009_back.jpg", root / "swap.tmp"
    a.rename(tmp)
    b.rename(a)
    tmp.rename(b)
    run_ingest()

    with session_scope() as s:
        year_by_scan = {lb.sha256: lb.d_year for lb in s.exec(select(LooseBack).where(LooseBack.missing == False)).all()}  # noqa: E712
    # Photos follow their content on a swap (test_swapped_file_names_keep_their_tags); backs should too.
    assert year_by_scan.get(sha["A/IMG_0005_back.jpg"]) == 1971, year_by_scan
    assert year_by_scan.get(sha["A/IMG_0009_back.jpg"]) == 1979, year_by_scan


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B8 / plan 4.4: A typed loose-back date is lost when its front is scanned later")
def test_typed_loose_back_date_survives_scanning_its_front_later(env):
    root = env.photos_root
    make_image(root / "A" / "IMG_0001.jpg", 1)
    make_image(root / "A" / "IMG_0002_back.jpg", 102)  # front not scanned yet: a loose back
    run_ingest()
    set_loose_back_date("A/IMG_0002_back.jpg", "1978-10")
    make_image(root / "A" / "IMG_0002.jpg", 2)  # the front is scanned later; the back is now paired
    run_ingest()

    with session_scope() as s:
        p = s.exec(select(Photo).where(Photo.rel_path == "A/IMG_0002.jpg")).one()
        require(p.back_rel_path == "A/IMG_0002_back.jpg", f"setup: back paired with its front, got {p.back_rel_path}")
        assert (p.back_year, p.back_month, p.back_source) == (1978, 10, "manual"), "typed back date lost"


# --- dates -----------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="J-B12 / plan 4.3: Circa and decade dates reach outside 1850-2100")
@pytest.mark.parametrize("text", ["~1850", "c 2100", "2100s"])
def test_parsed_ranges_stay_inside_supported_years(text):
    try:
        pd = parse_partial_date(text)
    except DateParseError:
        return  # refusing the input is fine too
    assert MIN_YEAR <= pd.min and pd.max <= MAX_YEAR, (pd.min, pd.max)


# --- concurrent edits ------------------------------------------------------------------


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="M-S3 / plan 1.5: Concurrent rotations lose an update")
def test_two_concurrent_quarter_turns_make_a_half_turn(env, monkeypatch):
    make_image(env.photos_root / "A1" / "p0.jpg", 0)
    run_ingest()
    with session_scope() as s:
        photo_id = s.exec(select(Photo.id)).one()
    real_record = editlog.record
    both_read = threading.Barrier(2)

    def record_after_both_read(session, changes, description, actor="user"):
        changes = list(changes)  # the new rotation is already computed from the value read
        try:
            # Hold each request here until both have read the rotation. Once writers are
            # serialized, the second can't read until the first commits: the wait times out,
            # breaks the barrier, and both carry on one after the other.
            both_read.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        return real_record(session, changes, description, actor)

    monkeypatch.setattr("shoebox.services.editlog.record", record_after_both_read)
    client = api_client()
    statuses = []

    def press_r():
        statuses.append(client.patch("/api/photos", json={"ids": [photo_id], "rotate_by": 90}).status_code)

    threads = [threading.Thread(target=press_r) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    require(statuses == [200, 200], f"setup: both requests answered, got {statuses}")

    with session_scope() as s:
        assert s.get(Photo, photo_id).rotation == 180, "two quarter turns made only one"
