import pytest
from sqlmodel import select

from shoebox.backs import parse_dates, read_back, run_read_backs
from shoebox.db import session_scope
from shoebox.ingest import run_ingest
from shoebox.models import LooseBack, Photo


@pytest.mark.parametrize(
    "text,expected",
    [
        ("PLACE STAMP FOTO DATE: DEG., 1975 HERE", (12, 1975, True)),  # DEC misread as DEG
        ("k&ikxkw OCT. 1978 PLACE", (10, 1978, True)),
        ("Kodak Paper OCT 87 M", (10, 1987, False)),
        ("JULY 91", (7, 1991, False)),
        ("909-467 (No.22) 07/06/02 319", (7, 2002, False)),
        ("MAR 1984 $861 UV", (3, 1984, True)),
    ],
)
def test_parse_dates(text, expected):
    hits = parse_dates(text)
    assert (hits[0].month, hits[0].year, hits[0].four_digit) == expected


def test_parse_ignores_noise():
    assert parse_dates("EL25 PQ02 22+00 ANANN+04AU 6124") == []
    assert parse_dates("Post Card PLACE STAMP HERE") == []


def fake(texts):
    return lambda path: iter(texts)


def test_four_digit_year_accepted_from_one_read(env):
    r = read_back(env.photos_root, fake(["", "OCT., 1978", "", ""]))
    assert r["accepted"]["year"] == 1978 and r["accepted"]["month"] == 10


def test_single_two_digit_read_is_only_a_candidate(env):
    r = read_back(env.photos_root, fake(["AUG 92", "", "", "", "", "", "", ""]))
    assert r["accepted"] is None
    assert r["candidates"][0] == {"month": 8, "year": 1992, "votes": 1, "four_digit": False}


def test_agreeing_two_digit_reads_accepted(env):
    r = read_back(env.photos_root, fake(["FEB 86 K", "FEB 86 K", "", ""]))
    assert (r["accepted"]["year"], r["accepted"]["month"]) == (1986, 2)


def test_out_of_range_years_dropped(env):
    r = read_back(env.photos_root, fake(["JUN 2022", "JUN 2022", "", ""]))
    assert r["candidates"] == [] and r["accepted"] is None


def test_run_read_backs_respects_file_name_years_and_manual_dates(env):
    from conftest import make_image

    root = env.photos_root
    make_image(root / "A3" / "Album3_1984_1986_0001.jpg", 1)
    make_image(root / "A3" / "Album3_1984_1986_0001_b.jpg", 2)
    make_image(root / "A3" / "Album3_1984_1986_0002_b.jpg", 3)  # loose
    make_image(root / "A3" / "Album3_1984_1986_0003_b.jpg", 4)  # loose, will be manual
    run_ingest()
    with session_scope() as s:
        lb = s.exec(select(LooseBack).where(LooseBack.rel_path.endswith("0003_b.jpg"))).one()
        lb.d_year, lb.d_source = 1985, "manual"
        s.add(lb)
    readings = {"0001_b": "OCT 1984", "0002_b": "OCT 1999", "0003_b": "MAY 1986"}

    def reader(path):
        key = path.stem.rsplit("_", 2)[-2] + "_b"
        return read_back(path, fake([readings[key], "", "", ""]))

    result = run_read_backs(reader=reader)
    assert result == {"loose_backs": 2, "paired_backs": 1, "dates_accepted": 1}  # manual 0003 isn't re-read
    with session_scope() as s:
        p = s.exec(select(Photo)).one()
        assert (p.back_year, p.back_month, p.back_source) == (1984, 10, "ocr")
        lbs = {lb.rel_path.rsplit("_", 2)[-2]: lb for lb in s.exec(select(LooseBack)).all()}
        assert lbs["0002"].d_year is None  # 1999 contradicts 1984-1986 in the file name
        assert lbs["0002"].candidates[0]["year"] == 1999  # but is kept for review
        assert (lbs["0003"].d_year, lbs["0003"].d_source) == (1985, "manual")  # never overwritten
