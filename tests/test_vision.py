import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from sqlmodel import select

from conftest import make_image
from shoebox import vision
from shoebox.db import session_scope
from shoebox.ingest import run_ingest
from shoebox.models import BackText, ClaudeBatch, LooseBack, Photo


class FakeBatches:
    """Stands in for client.messages.batches; answers from a dict keyed by image file stem."""

    def __init__(self, answers_by_sha, fail=()):
        self.answers, self.fail = answers_by_sha, set(fail)
        self.created: list = []
        self.polls = 0

    def create(self, requests):
        self.created.append(requests)
        return NS(id=f"msgbatch_{len(self.created)}")

    def retrieve(self, batch_id):
        self.polls += 1
        n = len(self.created[-1]) if self.created else len(self.answers)
        return NS(processing_status="ended", request_counts=NS(processing=0, succeeded=n, errored=0, canceled=0, expired=0))

    def results(self, batch_id):
        for sha, data in self.answers.items():
            if sha in self.fail:
                yield NS(custom_id=sha, result=NS(type="errored"))
            else:
                msg = NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(data))])
                yield NS(custom_id=sha, result=NS(type="succeeded", message=msg))

    def list(self, limit=20):
        return []


def client_for(batches):
    return NS(messages=NS(batches=batches))


def reading(year, month, text="", kind="handwritten", conf=0.9):
    return {
        "transcription": text, "handwritten_text": text,
        "dates": [{"text": text, "year": year, "month": month, "day": 0, "kind": kind}] if year else [],
        "best_year": year, "best_month": month, "confidence": conf,
        "names": ["Grandma Rose"] if text else [], "places": [], "event_hint": "",
    }


@pytest.fixture
def album(env):
    root = env.photos_root
    make_image(root / "A7" / "Album7_0001.jpg", 1)
    make_image(root / "A7" / "Album7_0001_b.jpg", 2)
    make_image(root / "A7" / "Album7_0002_b.jpg", 3)  # loose
    make_image(root / "A7" / "Album7_0003.jpg", 4)
    make_image(root / "A7" / "Album7_0004_b.jpg", 5)  # loose, already dated by OCR
    run_ingest()
    with session_scope() as s:
        lb = s.exec(select(LooseBack).where(LooseBack.rel_path.endswith("0004_b.jpg"))).one()
        lb.d_year, lb.d_month, lb.d_source = 1978, 10, "ocr"
        s.add(lb)
    return root


def shas():
    with session_scope() as s:
        p = s.exec(select(Photo).where(Photo.rel_path.endswith("0001.jpg"))).one()
        lbs = {lb.rel_path.split("_")[-2]: lb.sha256 for lb in s.exec(select(LooseBack)).all()}
        return p.back_sha256, lbs["0002"], lbs["0004"]


def test_cost_estimate_counts_only_unread(album, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    est = vision.cost_estimate("unread")
    assert est["backs"] == 2 and not est["has_api_key"]  # 0004_b already has an OCR date
    assert 0 < est["cost_low_usd"] <= est["cost_high_usd"] < 0.1
    assert vision.cost_estimate("all")["backs"] == 3



def test_batch_run_applies_readings_and_estimates(album):
    paired, loose, _ = shas()
    fake = FakeBatches({paired: reading(1983, 12, "Christmas 1983 at Grandma Rose's"), loose: reading(1984, 0, "Summer '84")})
    result = vision.run_claude_backs(scope="unread", client=client_for(fake))
    assert result["sent"] == 2 and result["succeeded"] == 2 and result["dates_accepted"] == 2
    req = fake.created[0][0]
    assert req["params"]["model"] == "claude-opus-5-5"
    assert req["params"]["output_config"]["format"]["type"] == "json_schema"
    assert req["params"]["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"
    assert {r["custom_id"] for r in fake.created[0]} == {paired, loose}
    with session_scope() as s:
        p = s.exec(select(Photo).where(Photo.rel_path.endswith("0001.jpg"))).one()
        assert (p.back_year, p.back_month, p.back_source) == (1983, 12, "claude")
        assert s.exec(select(BackText).where(BackText.photo_id == p.id)).one().transcription.startswith("Christmas 1983")
        assert p.i_year == 1983 and "claude" in p.i_source
        lb = s.exec(select(LooseBack).where(LooseBack.sha256 == loose)).one()
        assert (lb.d_year, lb.d_month, lb.d_source, lb.transcription) == (1984, None, "claude", "Summer '84")
        assert lb.candidates[0]["source"] == "claude"
        assert s.exec(select(ClaudeBatch)).one().state == "processed"
    # a second run has nothing left to send
    assert vision.run_claude_backs(client=client_for(FakeBatches({})))["sent"] == 0


def test_resume_after_crash_polls_instead_of_resubmitting(album):
    paired, loose, _ = shas()
    with session_scope() as s:
        s.add(ClaudeBatch(batch_id="msgbatch_existing", custom_ids=[paired, loose], state="submitted"))
    fake = FakeBatches({paired: reading(1983, 12, "x"), loose: reading(0, 0)})
    result = vision.run_claude_backs(client=client_for(fake))
    assert fake.created == []  # never resubmitted
    assert result["batch_id"] == "msgbatch_existing" and result["dates_accepted"] == 1


def test_failures_and_contradictions_stay_unread_and_manual_wins(album):
    paired, loose, _ = shas()
    with session_scope() as s:
        p = s.exec(select(Photo).where(Photo.back_sha256 == paired)).one()
        p.back_year, p.back_source = 1990, "manual"
        s.add(p)
    fake = FakeBatches({loose: reading(1984, 5)}, fail={loose})
    result = vision.run_claude_backs(scope="all", client=client_for(fake))
    assert result["failed"] == 1 and result["succeeded"] == 0
    with session_scope() as s:
        lb = s.exec(select(LooseBack).where(LooseBack.sha256 == loose)).one()
        assert lb.claude_ver == 0 and lb.d_year is None  # retried next run
        p = s.exec(select(Photo).where(Photo.back_sha256 == paired)).one()
        assert (p.back_year, p.back_source) == (1990, "manual")
    # the manual one wasn't even sent
    assert paired not in {r["custom_id"] for r in fake.created[0]}


def test_missing_api_key_fails_clearly(album, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(vision.VisionError, match="ANTHROPIC_API_KEY"):
        vision.run_claude_backs()
