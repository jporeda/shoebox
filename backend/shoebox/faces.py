"""Face detection and embeddings (pure: image in, faces out; no database).

Boxes are normalized (0..1) in the frame of the image as displayed with a given
clockwise rotation; ``rotate_box`` converts between rotations.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .config import get_settings

DETECTOR_VERSION = "buffalo_l-1"
MIN_FACE_PX = 40
MIN_DET_SCORE = 0.55


@dataclass
class DetectedFace:
    x: float
    y: float
    w: float
    h: float
    det_score: float
    embedding: np.ndarray  # 512 float32, L2-normalized
    age: float | None


@lru_cache
def _app():
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(
        name="buffalo_l",
        root=str(get_settings().model_dir),
        allowed_modules=["detection", "recognition", "genderage"],
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return app


def detect(rgb: np.ndarray) -> list[DetectedFace]:
    """Detect faces in an RGB array; drops tiny and low-confidence detections."""
    h, w = rgb.shape[:2]
    out = []
    for f in _app().get(np.ascontiguousarray(rgb[:, :, ::-1])):  # insightface wants BGR
        x0, y0, x1, y1 = (float(v) for v in f.bbox)
        x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(float(w), x1), min(float(h), y1)
        if min(x1 - x0, y1 - y0) < MIN_FACE_PX or f.det_score < MIN_DET_SCORE:
            continue
        emb = np.asarray(f.normed_embedding, dtype=np.float32)
        out.append(DetectedFace(x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h, float(f.det_score), emb,
                                float(f.age) if getattr(f, "age", None) is not None else None))
    return out


def embed_region(rgb: np.ndarray, box: tuple[float, float, float, float]) -> DetectedFace | None:
    """Embedding for a face a person drew: re-detect inside a padded crop of the box."""
    h, w = rgb.shape[:2]
    x, y, bw, bh = box
    pad = 0.6
    x0, y0 = max(0, int((x - bw * pad) * w)), max(0, int((y - bh * pad) * h))
    x1, y1 = min(w, int((x + bw * (1 + pad)) * w)), min(h, int((y + bh * (1 + pad)) * h))
    crop = rgb[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    scale = 1.0
    if min(crop.shape[:2]) < 160:  # small faces in old prints: upscale so the detector sees them
        from PIL import Image

        scale = 160 / min(crop.shape[:2])
        crop = np.asarray(Image.fromarray(crop).resize((int(crop.shape[1] * scale), int(crop.shape[0] * scale))))
    best = None
    for f in _app().get(np.ascontiguousarray(crop[:, :, ::-1])):
        fx0, fy0, fx1, fy1 = (float(v) / scale for v in f.bbox)
        cand = ((x0 + fx0) / w, (y0 + fy0) / h, (fx1 - fx0) / w, (fy1 - fy0) / h)
        overlap = iou(cand, box)
        if overlap > 0.2 and (best is None or overlap > best[0]):
            best = (overlap, f, cand)
    if best is None:
        return None
    _, f, (bx, by, bw2, bh2) = best
    return DetectedFace(bx, by, bw2, bh2, float(f.det_score), np.asarray(f.normed_embedding, dtype=np.float32),
                        float(f.age) if getattr(f, "age", None) is not None else None)


# --- geometry ---------------------------------------------------------------------


def rotate_box(box: tuple[float, float, float, float], from_rot: int, to_rot: int) -> tuple[float, float, float, float]:
    """Move a normalized box from one clockwise display rotation to another."""
    x, y, w, h = box
    for _ in range(((to_rot - from_rot) % 360) // 90):
        # 90° clockwise: (x, y) -> (1 - y - h, x), width and height swap
        x, y, w, h = 1 - y - h, x, h, w
    return (x, y, w, h)


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix = max(0.0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0.0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def to_blob(emb: np.ndarray) -> bytes:
    return np.asarray(emb, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
