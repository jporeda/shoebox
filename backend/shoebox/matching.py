"""Suggest (and, when very sure, apply) names for unnamed faces.

Exemplars are only faces a person named or accepted; automatic names are outputs and
never exemplars, so a wrong guess can't recruit more wrong guesses. Each exemplar is
weighted by how close its photo's year is to this photo's year — a 1985 toddler is
compared mostly with that child's mid-80s faces, not the 2005 adult. Faces that look
under 6 are never named automatically, only suggested.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Optional

import numpy as np
from sqlmodel import select

from .db import same, session_scope, update_if
from .estimate import TRUSTED_FACE_SOURCES
from .faces import from_blob
from .jobs_ctx import NULL_CTX, JobContext
from .models import Face, Photo

AUTO_SIMILARITY = 0.55
AUTO_MARGIN = 0.10
SUGGEST_SIMILARITY = 0.35
MIN_AUTO_AGE = 6
TOP_K = 3


@dataclass
class Exemplars:
    vectors: np.ndarray  # (n, 512) normalized
    person_ids: np.ndarray  # (n,)
    years: np.ndarray  # (n,) float, nan if unknown
    child: np.ndarray  # (n,) bool: exemplar face looked under 14


@dataclass
class Match:
    person_id: int
    score: float
    margin: float


def photo_year(p: Photo) -> Optional[int]:
    return p.t_year if p.t_year is not None else p.i_year


def load_exemplars(faces: list[Face], years: dict[int, Optional[int]], exclude: set[int] = frozenset()) -> Optional[Exemplars]:
    rows = [f for f in faces if f.person_id and f.source in TRUSTED_FACE_SOURCES and not f.ignored
            and f.embedding and f.id not in exclude]
    if not rows:
        return None
    v = np.stack([from_blob(f.embedding) for f in rows])
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
    return Exemplars(
        vectors=v,
        person_ids=np.array([f.person_id for f in rows]),
        years=np.array([years.get(f.photo_id) if years.get(f.photo_id) is not None else math.nan for f in rows], float),
        child=np.array([(f.est_age or 30) < 14 for f in rows]),
    )


def match(emb: np.ndarray, year: Optional[int], ex: Exemplars, rejected: set[int] = frozenset()) -> Optional[Match]:
    e = emb / (np.linalg.norm(emb) + 1e-9)
    sims = ex.vectors @ e
    if year is None or math.isnan(year):
        era = np.full_like(sims, 0.6)
    else:
        tau = np.where(ex.child, 3.0, 10.0)  # children change fast, adults slowly
        dy = np.abs(ex.years - year)
        era = np.where(np.isnan(dy), 0.6, np.exp(-np.nan_to_num(dy) / tau))
    adjusted = sims * (0.6 + 0.4 * era)
    scores = []
    for pid in np.unique(ex.person_ids):
        if int(pid) in rejected:
            continue
        mine = np.sort(adjusted[ex.person_ids == pid])[::-1][:TOP_K]
        scores.append((float(mine.mean()), int(pid)))
    if not scores:
        return None
    scores.sort(reverse=True)
    best, pid = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    return Match(pid, round(best, 3), round(best - second, 3))


def decide(m: Optional[Match], est_age: Optional[float]) -> str:
    """'auto' (apply), 'suggest', or 'none'."""
    if m is None or m.score < SUGGEST_SIMILARITY:
        return "none"
    young = est_age is not None and est_age < MIN_AUTO_AGE
    if m.score >= AUTO_SIMILARITY and m.margin >= AUTO_MARGIN and not young:
        return "auto"
    return "suggest"


def run_matching(ctx: JobContext = NULL_CTX) -> dict:
    run_id = f"names-{uuid.uuid4().hex[:8]}"
    counts = {"auto": 0, "suggested": 0, "unnamed": 0, "exemplars": 0, "run_id": run_id}
    with session_scope() as s:
        photos = {p.id: p for p in s.exec(select(Photo)).all()}
        years = {pid: photo_year(p) for pid, p in photos.items()}
        faces = list(s.exec(select(Face).where(Face.ignored == False)).all())  # noqa: E712
        ex = load_exemplars(faces, years)
        counts["exemplars"] = 0 if ex is None else len(ex.person_ids)
        # Unnamed faces of any kind, plus earlier automatic names (recomputed each run).
        targets = [f for f in faces if (f.person_id is None or f.source == "auto") and f.embedding]
        ctx.progress(0, len(targets), "suggesting names")
        for f in targets:
            m = match(from_blob(f.embedding), years.get(f.photo_id), ex, set(f.rejected_person_ids or [])) if ex else None
            verdict = decide(m, f.est_age)
            if verdict == "auto" and f.source != "auto":
                verdict = "suggest"  # drawn/once-named faces are trusted; only a person names them
            new = (m.person_id if verdict == "auto" else None, m.person_id if verdict == "suggest" else None)
            counts["auto" if verdict == "auto" else "suggested" if verdict == "suggest" else "unnamed"] += 1
            if (f.person_id, f.suggested_person_id) == new:
                continue
            values = {"person_id": new[0], "suggested_person_id": new[1],
                      "confidence": m.score if m and verdict != "none" else None}
            if verdict == "auto":
                values.update(run_id=run_id, cluster_id=None)
            # Skipped if someone named, ignored or answered this face since it was read.
            update_if(s, Face, f.id, same(Face.person_id, f.person_id), same(Face.suggested_person_id, f.suggested_person_id),
                      same(Face.source, f.source), Face.ignored == False, **values)  # noqa: E712
    return counts
