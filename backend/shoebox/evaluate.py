"""Measure inference accuracy by hiding some confirmed answers and predicting them.

Nothing is written: dates are re-estimated in memory with the hidden photos removed as
anchors, and faces are matched with the hidden faces removed as exemplars.
"""

from __future__ import annotations

import random
import statistics
from typing import Optional

from sqlmodel import select

from .config import get_settings
from .db import session_scope
from .estimate import TRUSTED_FACE_SOURCES, estimate_for, load_world
from .faces import from_blob
from .jobs_ctx import NULL_CTX, JobContext
from .matching import decide, load_exemplars, match, photo_year
from .models import Face, Photo

MIN_DATES = 5
MIN_FACES = 10


def _bucket(conf: float) -> str:
    return "high (≥0.7)" if conf >= 0.7 else "medium (0.4–0.7)" if conf >= 0.4 else "low (<0.4)"


def _summary(errors: list[int]) -> dict:
    if not errors:
        return {"n": 0}
    return {
        "n": len(errors),
        "median_abs_error_years": statistics.median(errors),
        "exact_year": round(sum(e == 0 for e in errors) / len(errors), 3),
        "within_1_year": round(sum(e <= 1 for e in errors) / len(errors), 3),
        "within_2_years": round(sum(e <= 2 for e in errors) / len(errors), 3),
    }


def evaluate_dates(fraction: float, seed: int) -> dict:
    with session_scope() as s:
        world = load_world(s)
        trusted = [p for p in world.photos if p.t_year is not None and p.t_precision in ("day", "month", "year")
                   and p.t_source != "event"]
        if len(trusted) < MIN_DATES:
            return {"status": f"need at least {MIN_DATES} confirmed single-year dates (have {len(trusted)})"}
        hidden = random.Random(seed).sample(trusted, max(1, round(len(trusted) * fraction)))
        held = load_world(s, exclude={p.id for p in hidden})
        window = get_settings().loose_back_window
        errors, by_kind, by_conf, missing = [], {}, {}, 0
        for p in hidden:
            est = estimate_for(p, held, window)
            if est is None:
                missing += 1
                continue
            err = abs(est.year - p.t_year)
            errors.append(err)
            by_kind.setdefault(est.kind, []).append(err)
            by_conf.setdefault(_bucket(est.confidence), []).append(err)
    return {
        "hidden": len(hidden),
        "no_estimate": missing,
        "overall": _summary(errors),
        "by_main_evidence": {k: _summary(v) for k, v in sorted(by_kind.items())},
        "by_confidence": {k: _summary(v) for k, v in sorted(by_conf.items())},
    }


def _rates(rows: list[tuple[str, bool]], total: int) -> dict:
    auto = [ok for verdict, ok in rows if verdict == "auto"]
    sugg = [ok for verdict, ok in rows if verdict == "suggest"]
    return {
        "faces": total,
        "auto_named": len(auto),
        "auto_precision": round(sum(auto) / len(auto), 3) if auto else None,
        "auto_recall": round(sum(auto) / total, 3) if total else None,
        "suggested": len(sugg),
        "suggestion_precision": round(sum(sugg) / len(sugg), 3) if sugg else None,
    }


def evaluate_faces(fraction: float, seed: int) -> dict:
    with session_scope() as s:
        photos = {p.id: p for p in s.exec(select(Photo)).all()}
        years: dict[int, Optional[int]] = {pid: photo_year(p) for pid, p in photos.items()}
        faces = list(s.exec(select(Face).where(Face.ignored == False)).all())  # noqa: E712
    named = [f for f in faces if f.person_id and f.source in TRUSTED_FACE_SOURCES and f.embedding]
    if len(named) < MIN_FACES:
        return {"status": f"need at least {MIN_FACES} named faces (have {len(named)})"}
    hidden = random.Random(seed).sample(named, max(1, round(len(named) * fraction)))
    ex = load_exemplars(faces, years, exclude={f.id for f in hidden})
    rows_all, rows_young = [], []
    for f in hidden:
        m = match(from_blob(f.embedding), years.get(f.photo_id), ex) if ex else None
        verdict = decide(m, f.est_age)
        row = (verdict, bool(m and m.person_id == f.person_id))
        rows_all.append(row)
        if f.est_age is not None and f.est_age < 6:
            rows_young.append(row)
    return {"overall": _rates(rows_all, len(hidden)), "under_6": _rates(rows_young, len(rows_young))}


def run_evaluate(ctx: JobContext = NULL_CTX, fraction: float = 0.2, seed: int = 7) -> dict:
    ctx.progress(0, 2, "dates")
    dates = evaluate_dates(fraction, seed)
    ctx.progress(1, 2, "faces")
    return {"held_out_fraction": fraction, "dates": dates, "faces": evaluate_faces(fraction, seed)}
