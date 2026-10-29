"""Auto-orientation: pick the rotation where the face detector is most confident.

Only touches photos whose rotation was never set by a person.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np
from sqlmodel import select

from .config import get_settings
from .db import session_scope
from .images import open_display
from .jobs_ctx import NULL_CTX, JobContext
from .models import Photo

log = logging.getLogger(__name__)

ORIENT_VERSION = 1
ROTATIONS = (0, 90, 180, 270)


@lru_cache
def detector():
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(
        name="buffalo_l",
        root=str(get_settings().model_dir),
        allowed_modules=["detection"],
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return app


def face_score(rgb: np.ndarray) -> float:
    """Sum of confident detection scores (BGR input expected by insightface)."""
    faces = detector().get(rgb[:, :, ::-1].copy())
    return float(sum(f.det_score for f in faces if f.det_score >= get_settings().orient_min_score))


def best_rotation(scores: dict[int, float], margin: float) -> int:
    """Rotation to apply, or 0 if no rotation clearly beats upright."""
    best = max(scores, key=scores.get)
    if best != 0 and scores[best] >= scores.get(0, 0.0) + margin:
        return best
    return 0


def run_orient(ctx: JobContext = NULL_CTX, scorer=face_score) -> dict:
    settings = get_settings()
    with session_scope() as s:
        ids = s.exec(
            select(Photo.id).where(Photo.missing == False, Photo.orient_ver < ORIENT_VERSION)  # noqa: E712
        ).all()
    rotated = 0
    for i, pid in enumerate(ids):
        ctx.check_cancel()
        if i % 10 == 0:
            ctx.progress(i, len(ids), "checking orientation")
        with session_scope() as s:
            p = s.get(Photo, pid)
            if p.rotation_source != "manual":
                try:
                    base = open_display(settings.photos_root / p.rel_path, 0, max_size=640).convert("RGB")
                    scores = {r: scorer(np.asarray(base.rotate(-r, expand=True))) for r in ROTATIONS}
                    rot = best_rotation(scores, settings.orient_margin)
                    if rot != p.rotation:
                        p.rotation = rot
                        p.rotation_source = "auto" if rot else "none"
                        rotated += bool(rot)
                except Exception as exc:
                    log.warning("orientation failed for %s: %s", p.rel_path, exc)
            p.orient_ver = ORIENT_VERSION
            s.add(p)
    return {"checked": len(ids), "rotated": rotated}
