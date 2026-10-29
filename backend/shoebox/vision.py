"""Read back scans with Claude (Message Batches API).

Only back images are sent; fronts and faces never leave the machine. Each back
gets a full transcription plus the date the photo was most likely taken.
Batches are recorded before submission and resumed after a restart, so a crash
never pays for the same back twice.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, Optional

from PIL import Image, ImageOps
from sqlmodel import Session, select

from .backs import _consistent
from .config import get_settings
from .db import session_scope
from .jobs_ctx import NULL_CTX, JobContext
from .models import BackText, ClaudeBatch, LooseBack, Photo, utcnow

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
VISION_VERSION = 1
MAX_EDGE = 1568  # larger images are downscaled by the API anyway
# Batch pricing is half the standard $4 / $20 per million tokens for this model.
BATCH_INPUT_PER_MTOK = 2.0
BATCH_OUTPUT_PER_MTOK = 10.0
PROMPT_TOKENS = 900
OUTPUT_TOKENS = (250, 900)  # low/high guess incl. thinking at low effort
POLL_SECONDS = 30

Scope = Literal["unread", "all"]

PROMPT = """This is a scan of the BACK of a printed family photograph (1950s-2000s).
Read everything on it and work out when the photo was most likely taken.

What you may see:
- Printed lab dates, often upside down, mirrored, faint, or repeated: "FOTO DATE: DEC., 1975",
  "OCT., 1978", Kodak paper codes like "FEB 86 K" or "OCT 84 N" (= Feb 1986 / Oct 1984),
  dot-matrix lab lines with mm/dd/yy like "07/06/02". These are the print/processing date,
  usually within weeks of the photo being taken.
- Handwriting: dates ("Aug. '90", "Christmas 1983"), names, places, events. A handwritten
  date usually describes when the photo was taken; prefer it over a lab date when both exist.
- Noise to ignore for dating: patent numbers, "Post Card / Place stamp here", paper brand
  names (Kodak, AGFA, Fuji), order numbers, prices.

Rules:
- Use 0 for any unknown year, month, or day.
- best_year/best_month is your single best estimate of when the photo was taken; 0 if the back
  gives no date evidence at all. confidence is 0-1 for that estimate.
- transcription: all legible text in reading order (normalize upside-down/mirrored text).
- Do not guess names or dates that aren't on the back."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "transcription": {"type": "string"},
        "handwritten_text": {"type": "string"},
        "dates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "year": {"type": "integer"},
                    "month": {"type": "integer"},
                    "day": {"type": "integer"},
                    "kind": {"type": "string", "enum": ["printed_lab_date", "handwritten", "stamp", "other"]},
                },
                "required": ["text", "year", "month", "day", "kind"],
                "additionalProperties": False,
            },
        },
        "best_year": {"type": "integer"},
        "best_month": {"type": "integer"},
        "confidence": {"type": "number"},
        "names": {"type": "array", "items": {"type": "string"}},
        "places": {"type": "array", "items": {"type": "string"}},
        "event_hint": {"type": "string"},
    },
    "required": [
        "transcription", "handwritten_text", "dates", "best_year", "best_month",
        "confidence", "names", "places", "event_hint",
    ],
    "additionalProperties": False,
}


class VisionError(RuntimeError):
    pass


def make_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise VisionError("ANTHROPIC_API_KEY is not set. Add it to .env and restart: docker compose up -d")
    import anthropic

    return anthropic.Anthropic()


# --- choosing work ----------------------------------------------------------------


def _targets(s: Session, scope: Scope) -> list[tuple[str, str]]:
    """(sha256, rel_path) of backs to send: skip manual dates and ones already read."""
    out: list[tuple[str, str]] = []
    for lb in s.exec(select(LooseBack).where(LooseBack.missing == False, LooseBack.claude_ver < VISION_VERSION)).all():  # noqa: E712
        if lb.d_source == "manual" or (scope == "unread" and lb.d_year is not None):
            continue
        out.append((lb.sha256, lb.rel_path))
    for p in s.exec(
        select(Photo).where(
            Photo.missing == False, Photo.back_sha256 != None, Photo.back_claude_ver < VISION_VERSION  # noqa: E711,E712
        )
    ).all():
        if p.back_source == "manual" or (scope == "unread" and p.back_year is not None):
            continue
        out.append((p.back_sha256, p.back_rel_path))
    return out


def _image_tokens(path: Path) -> int:
    with Image.open(path) as im:
        w, h = im.size
    scale = min(1.0, MAX_EDGE / max(w, h))
    return int((w * scale) * (h * scale) / 750) + 1


def cost_estimate(scope: Scope = "unread") -> dict:
    root = get_settings().photos_root
    with session_scope() as s:
        targets = _targets(s, scope)
    image_tokens = sum(_image_tokens(root / rel) for _, rel in targets)
    inp = image_tokens + PROMPT_TOKENS * len(targets)
    lo = inp * BATCH_INPUT_PER_MTOK / 1e6 + OUTPUT_TOKENS[0] * len(targets) * BATCH_OUTPUT_PER_MTOK / 1e6
    hi = inp * BATCH_INPUT_PER_MTOK / 1e6 + OUTPUT_TOKENS[1] * len(targets) * BATCH_OUTPUT_PER_MTOK / 1e6
    return {
        "scope": scope,
        "backs": len(targets),
        "model": MODEL,
        "input_tokens": inp,
        "cost_low_usd": round(lo, 2),
        "cost_high_usd": round(hi, 2),
        "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


# --- requests ------------------------------------------------------------------------


def _encode(path: Path) -> str:
    with Image.open(path) as raw:
        img = ImageOps.exif_transpose(raw).convert("RGB")
    img.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return base64.standard_b64encode(buf.getvalue()).decode()


def build_request(sha: str, path: Path) -> dict:
    return {
        "custom_id": sha,  # 64 hex chars: fits the 64-char custom_id limit
        "params": {
            "model": MODEL,
            "max_tokens": 16000,
            "output_config": {
                "effort": "low",
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _encode(path)}},
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        },
    }


# --- applying results ---------------------------------------------------------------


def _accepted(data: dict) -> Optional[dict]:
    s = get_settings()
    y, m = data.get("best_year") or 0, data.get("best_month") or 0
    if not y or not s.ocr_min_year <= y <= s.ocr_max_year:
        return None
    return {"year": y, "month": m if 1 <= m <= 12 else None, "confidence": max(0.0, min(float(data.get("confidence") or 0), 0.95))}


def _candidates(existing: list, data: dict) -> list:
    kept = [c for c in existing or [] if c.get("source") != "claude"]
    for d in data.get("dates", []):
        if d.get("year"):
            kept.insert(0, {"year": d["year"], "month": d.get("month") or None, "votes": 1,
                            "four_digit": True, "source": "claude", "kind": d.get("kind"), "text": d.get("text")})
    return kept


def apply_result(s: Session, sha: str, data: dict) -> bool:
    """Store one back's reading. Returns True if it produced an accepted date."""
    acc = _accepted(data)
    lb = s.exec(select(LooseBack).where(LooseBack.sha256 == sha)).first()
    if lb is not None:
        lo_hi = s.exec(
            select(Photo.name_year_min, Photo.name_year_max).where(
                Photo.album_id == lb.album_id, Photo.name_year_min != None  # noqa: E711
            )
        ).first()
        acc = _consistent(acc, *(lo_hi or (None, None)))
        lb.transcription, lb.claude_raw, lb.claude_ver = data.get("transcription"), data, VISION_VERSION
        lb.candidates = _candidates(lb.candidates, data)
        if lb.d_source != "manual" and acc:
            lb.d_year, lb.d_month, lb.d_source, lb.d_confidence = acc["year"], acc["month"], "claude", acc["confidence"]
        s.add(lb)
        return acc is not None
    for p in s.exec(select(Photo).where(Photo.back_sha256 == sha)).all():
        acc_p = _consistent(acc, p.name_year_min, p.name_year_max)
        bt = s.exec(select(BackText).where(BackText.photo_id == p.id)).first() or BackText(photo_id=p.id)
        bt.transcription, bt.dates, bt.raw = data.get("transcription"), data.get("dates", []), data
        s.add(bt)
        p.back_candidates = _candidates(p.back_candidates, data)
        p.back_claude_ver = VISION_VERSION
        if p.back_source != "manual" and acc_p:
            p.back_year, p.back_month, p.back_source, p.back_confidence = (
                acc_p["year"], acc_p["month"], "claude", acc_p["confidence"])
        s.add(p)
        return acc_p is not None
    return False


def _message_json(message) -> dict:
    if message.stop_reason == "refusal":
        raise VisionError("refused")
    text = next((b.text for b in message.content if b.type == "text"), "")
    return json.loads(text)


# --- the job ------------------------------------------------------------------------


def _adopt_orphan(client, row: ClaudeBatch) -> Optional[str]:
    """A crash between create() and saving its id: find the batch we created."""
    since = row.created_at - timedelta(minutes=2)
    for b in client.messages.batches.list(limit=20):
        created = b.created_at if b.created_at.tzinfo else b.created_at.replace(tzinfo=since.tzinfo)
        rc = b.request_counts
        total = rc.processing + rc.succeeded + rc.errored + rc.canceled + rc.expired
        if created >= since and total == len(row.custom_ids):
            return b.id
    return None


def run_claude_backs(ctx: JobContext = NULL_CTX, scope: Scope = "unread", client=None) -> dict:
    client = client or make_client()
    root = get_settings().photos_root

    with session_scope() as s:
        row = s.exec(select(ClaudeBatch).where(ClaudeBatch.state != "processed").order_by(ClaudeBatch.id)).first()
        if row is None:
            targets = _targets(s, scope)
            if not targets:
                return {"sent": 0, "accepted": 0, "message": "no backs need reading"}
            row = ClaudeBatch(job_id=ctx.job_id, custom_ids=[sha for sha, _ in targets], state="pending")
            s.add(row)
            s.flush()
            rels = dict(targets)
        else:
            rels = {}
        row_id, custom_ids, batch_id, state = row.id, list(row.custom_ids), row.batch_id, row.state

    if state == "pending" and batch_id is None:
        resumed = not rels
        batch_id = _adopt_orphan(client, row) if resumed else None
        if batch_id is None:
            if resumed:  # crashed before submitting: rebuild the request list
                with session_scope() as s:
                    wanted = set(custom_ids)
                    rels = {sha: rel for sha, rel in _targets(s, "all") if sha in wanted}
            ctx.progress(0, len(custom_ids), "preparing images")
            requests = []
            for i, sha in enumerate(custom_ids):
                ctx.check_cancel()
                if sha in rels:
                    requests.append(build_request(sha, root / rels[sha]))
                if i % 25 == 0:
                    ctx.progress(i, len(custom_ids), "preparing images")
            batch = client.messages.batches.create(requests=requests)
            batch_id = batch.id
        with session_scope() as s:
            r = s.get(ClaudeBatch, row_id)
            r.batch_id, r.state = batch_id, "submitted"
            s.add(r)

    while True:
        ctx.check_cancel()
        b = client.messages.batches.retrieve(batch_id)
        rc = b.request_counts
        done = (rc.succeeded or 0) + (rc.errored or 0) + (rc.canceled or 0) + (rc.expired or 0)
        ctx.progress(done, len(custom_ids), f"Claude batch {b.processing_status}")
        if b.processing_status == "ended":
            break
        time.sleep(POLL_SECONDS)

    accepted = succeeded = failed = 0
    errors: list[str] = []
    for result in client.messages.batches.results(batch_id):
        sha = result.custom_id
        try:
            if result.result.type != "succeeded":
                raise VisionError(result.result.type)
            data = _message_json(result.result.message)
            with session_scope() as s:
                accepted += apply_result(s, sha, data)
            succeeded += 1
        except Exception as exc:  # leave it unread; a later run retries it
            failed += 1
            if len(errors) < 20:
                errors.append(f"{sha[:12]}: {exc}")
    with session_scope() as s:
        r = s.get(ClaudeBatch, row_id)
        r.state = "processed"
        s.add(r)

    from .estimate import run_estimates

    est = run_estimates(ctx)
    return {
        "batch_id": batch_id,
        "sent": len(custom_ids),
        "succeeded": succeeded,
        "failed": failed,
        "dates_accepted": accepted,
        "errors": errors,
        "estimate": {k: v for k, v in est.items() if k != "run_id"},
        "finished_at": utcnow().isoformat(),
    }
