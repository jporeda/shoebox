import json

from fastapi.testclient import TestClient
from sqlmodel import select

from conftest import make_image
from shoebox import backup, services
from shoebox.app import app
from shoebox.db import session_scope
from shoebox.estimate import run_estimates
from shoebox.export import run_export
from shoebox.ingest import run_ingest
from shoebox.models import LooseBack, Photo


def build(root):
    """A7-like album: fronts and loose backs interleaved, plus a dated album."""
    for name, seed in [
        ("Album7_0001", 1), ("Album7_0003", 2), ("Album7_0003_b", 3), ("Album7_0004_b", 4),
        ("Album7_0006", 5), ("Album7_0007", 6), ("Album7_0008", 7), ("Album7_0009", 8),
        ("Album7_0010", 9), ("Album7_0011", 10),
    ]:
        make_image(root / "A7" / f"{name}.jpg", seed)
    make_image(root / "A10" / "Album10_1981_0001.jpg", 20)
    make_image(root / "A15" / "Album15_1978_1981_0001.jpg", 21)
    run_ingest()


def photos():
    with session_scope() as s:
        return {p.rel_path.split("/")[-1][:-4]: p for p in s.exec(select(Photo)).all()}


def set_loose(name, year, month, source="ocr"):
    with session_scope() as s:
        lb = s.exec(select(LooseBack).where(LooseBack.rel_path.endswith(name + ".jpg"))).one()
        lb.d_year, lb.d_month, lb.d_source, lb.d_confidence = year, month, source, 0.8
        s.add(lb)


def test_loose_back_dates_fronts_within_window(env):
    build(env.photos_root)
    set_loose("Album7_0004_b", 1978, 10)
    with session_scope() as s:  # 0003 has its own back reading, which should win for it
        p = s.exec(select(Photo).where(Photo.rel_path.endswith("Album7_0003.jpg"))).one()
        p.back_year, p.back_month, p.back_source = 1978, 9, "ocr"
        s.add(p)
    result = run_estimates()
    ps = photos()
    # list positions: 0001=0 0003=1 0003_b=2 0004_b=3 0006=4 0007=5 0008=6 0009=7 0010=8
    assert (ps["Album7_0003"].i_year, ps["Album7_0003"].i_month) == (1978, 9)
    assert "back says 1978-09" in ps["Album7_0003"].i_source
    six = ps["Album7_0006"]
    assert (six.i_year, six.i_month) == (1978, 10)
    assert six.i_confidence == 0.75  # loose back 1 file away, plus the OCR-dated 0003 as a neighbour anchor
    assert ps["Album7_0008"].i_confidence >= 0.5 and "3 files away" in ps["Album7_0008"].i_source
    assert ps["Album7_0001"].i_year == 1978  # 3 files away (position 0)
    # 4 files away: out of the loose back's reach, but the OCR-dated 0003 still anchors it
    nine = ps["Album7_0009"]
    assert nine.i_year == 1978 and "loose back" not in nine.i_source and "in this album" in nine.i_source
    assert result["own_back"] == 1 and result["loose_back"] >= 3


def test_file_name_years_are_a_weak_fallback(env):
    build(env.photos_root)
    run_estimates()
    ps = photos()
    a10, a15 = ps["Album10_1981_0001"], ps["Album15_1978_1981_0001"]
    assert (a10.i_year, a10.i_precision, a10.i_confidence) == (1981, "year", 0.35)
    assert (a15.i_year, a15.i_min, a15.i_max, a15.i_precision) == (1979, 1978, 1981, "range")


def test_estimates_never_touch_trusted_dates_and_clear_when_evidence_goes(env):
    build(env.photos_root)
    set_loose("Album7_0004_b", 1978, 10)
    with session_scope() as s:
        pid = photos()["Album7_0006"].id
        services.bulk_update(s, [pid], date="1979")
    run_estimates()
    p = photos()["Album7_0006"]
    assert (p.t_year, p.t_source) == (1979, "manual") and p.i_year == 1978
    set_loose("Album7_0004_b", None, None, None)
    run_estimates()
    # With the loose back gone, the confirmed 1979 next door is the evidence now.
    p7 = photos()["Album7_0007"]
    assert p7.i_year == 1979 and "after 1979 in this album" in p7.i_source


def test_manual_loose_back_date_via_api_updates_neighbours_and_undoes(env):
    build(env.photos_root)
    with TestClient(app) as client:
        lb = [b for b in client.get("/api/loose-backs").json() if b["rel_path"].endswith("0004_b.jpg")][0]
        assert [n["distance"] for n in lb["neighbours"]] == [-3, -2, 1, 2, 3]
        res = client.patch(f"/api/loose-backs/{lb['id']}", json={"date": "1978-10"}).json()
        assert res["source"] == "manual" and res["date_label"] == "1978-10"
        assert {n["date_label"] for n in res["neighbours"]} == {"≈1978-10"}
        assert client.get("/api/loose-backs", params={"undated": True}).json() == []
        # "No date" is a decision too: it leaves the to-do list
        other = [b for b in client.get("/api/loose-backs").json() if b["id"] != lb["id"]]
        for b in other:
            client.patch(f"/api/loose-backs/{b['id']}", json={"date": None})
        assert client.get("/api/loose-backs", params={"undated": True}).json() == []
        bad = client.patch(f"/api/loose-backs/{lb['id']}", json={"date": "1970s"})
        assert bad.status_code == 422
        client.post("/api/undo", json={})
        assert photos()["Album7_0006"].i_year is None


def test_estimated_month_exported_and_loose_back_in_truth(env):
    build(env.photos_root)
    with TestClient(app) as client:
        lb = [b for b in client.get("/api/loose-backs").json() if b["rel_path"].endswith("0004_b.jpg")][0]
        client.patch(f"/api/loose-backs/{lb['id']}", json={"date": "1978-10"})
    result = run_export()
    assert not result["failed"]
    index = json.loads((env.backup_dir / "export" / "export-index.json").read_text())
    names = sorted(v["path"] for v in index.values())
    assert "1978/1978-10-01_0000_A7_Album7-0001.jpg" in names
    truth = backup.build_truth()
    assert truth["loose_backs"][0]["date"] == {"year": 1978, "month": 10}


def test_paired_back_review_dates_its_front(env):
    build(env.photos_root)
    with TestClient(app) as client:
        backs = client.get("/api/paired-backs").json()
        assert [b["rel_path"] for b in backs] == ["A7/Album7_0003_b.jpg"]
        b = backs[0]
        assert b["kind"] == "paired" and b["neighbours"][0]["distance"] == 0
        res = client.patch(f"/api/paired-backs/{b['id']}", json={"date": "1978-09"}).json()
        assert res["date_label"] == "1978-09" and res["neighbours"][0]["date_label"] == "≈1978-09"
        assert client.get("/api/paired-backs", params={"undated": True}).json() == []
        album = [a for a in client.get("/api/albums").json() if a["name"] == "A7"][0]
        assert (album["paired_backs"], album["paired_backs_undated"]) == (1, 0)
        client.patch(f"/api/paired-backs/{b['id']}", json={"date": ""})  # "no date on this back"
        assert client.get("/api/paired-backs", params={"undated": True}).json() == []
        assert photos()["Album7_0003"].i_year is None
