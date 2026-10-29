"""Faces in the database: detection job, clustering, naming, crops.

Detection merges with what's already there: a new detection overlapping (IoU >= 0.5)
an existing face updates that face's box/embedding but keeps its person, ignore
flag and source, so re-running never loses a name.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
from PIL import Image
from sqlmodel import Session, select

from . import editlog
from .config import get_settings
from .db import session_scope, update_if
from .faces import DETECTOR_VERSION, DetectedFace, from_blob, iou, rotate_box, to_blob
from .images import open_display, save_jpeg
from .jobs_ctx import NULL_CTX, JobContext
from .models import Face, Person, Photo

log = logging.getLogger(__name__)

FACES_VERSION = 1
# Source of a face whose automatic name the user reverted: it only gets suggestions from now on.
REVERTED = "reverted"
MATCH_IOU = 0.5
# Complete linkage: every pair of faces in a group is within this cosine distance.
# Measured on a real ~1,900-photo collection: 0.6 keeps young siblings mostly apart (largest group
# ~40) at the cost of more, smaller groups; the same person across eras ends up in
# several groups, which naming them the same person merges.
CLUSTER_METHOD = "complete"
CLUSTER_DISTANCE = 0.6
SUGGEST_MIN_SIMILARITY = 0.35
DETECT_EDGE = 1600


def _display_rgb(p: Photo) -> np.ndarray:
    img = open_display(get_settings().photos_root / p.rel_path, p.rotation, max_size=DETECT_EDGE)
    return np.asarray(img.convert("RGB"))


def merge_detections(s: Session, p: Photo, found: list[DetectedFace], run_id: str) -> dict:
    """Reconcile fresh detections with the photo's existing faces."""
    existing = list(s.exec(select(Face).where(Face.photo_id == p.id)).all())
    used: set[int] = set()
    added = updated = 0
    for d in found:
        box = (d.x, d.y, d.w, d.h)
        best, best_iou = None, MATCH_IOU
        for f in existing:
            if f.id in used:
                continue
            o = iou(rotate_box((f.x, f.y, f.w, f.h), f.frame_rotation, p.rotation), box)
            if o >= best_iou:
                best, best_iou = f, o
        if best is not None:
            used.add(best.id)
            best.x, best.y, best.w, best.h, best.frame_rotation = *box, p.rotation
            best.embedding, best.det_score, best.est_age, best.detector_ver = to_blob(d.embedding), d.det_score, d.age, DETECTOR_VERSION
            s.add(best)
            updated += 1
        else:
            s.add(Face(photo_id=p.id, x=d.x, y=d.y, w=d.w, h=d.h, frame_rotation=p.rotation,
                       embedding=to_blob(d.embedding), det_score=d.det_score, est_age=d.age,
                       detector_ver=DETECTOR_VERSION, source="auto", run_id=run_id))
            added += 1
    # Unnamed auto faces the detector no longer finds are dropped; anything a person touched stays.
    removed = 0
    for f in existing:
        if f.id not in used and f.source == "auto" and f.person_id is None and not f.ignored:
            s.delete(f)
            removed += 1
    return {"added": added, "updated": updated, "removed": removed}


def run_faces(ctx: JobContext = NULL_CTX, detector=None) -> dict:
    from .faces import detect

    detector = detector or detect
    run_id = f"faces-{int(time.time())}"
    with session_scope() as s:
        ids = s.exec(
            select(Photo.id).where(Photo.missing == False, Photo.faces_model_ver < FACES_VERSION)  # noqa: E712
        ).all()
    totals = {"photos": len(ids), "faces_added": 0, "faces_updated": 0, "failed": 0}
    started = time.monotonic()
    for i, pid in enumerate(ids):
        ctx.check_cancel()
        if i % 5 == 0:
            rate = i / max(time.monotonic() - started, 1e-6)
            ctx.progress(i, len(ids), f"detecting faces ({rate:.1f} photos/s)")
        with session_scope() as s:
            p = s.get(Photo, pid)
        try:
            found = detector(_display_rgb(p))
        except Exception as exc:
            log.warning("face detection failed for %s: %s", p.rel_path, exc)
            totals["failed"] += 1
            found = None
        with session_scope() as s:
            # Rotated while detecting: the rotate reset this photo's marker and queued another run.
            if not update_if(s, Photo, pid, Photo.rotation == p.rotation, faces_model_ver=FACES_VERSION):
                continue
            if found is not None:
                result = merge_detections(s, s.get(Photo, pid), found, run_id)
                totals["faces_added"] += result["added"]
                totals["faces_updated"] += result["updated"]
    elapsed = time.monotonic() - started
    totals["seconds"] = round(elapsed, 1)
    totals["seconds_per_photo"] = round(elapsed / max(len(ids), 1), 3)
    return totals


def run_embed_missing(ctx: JobContext = NULL_CTX, embedder=None) -> dict:
    """Embeddings for faces a person drew (or restored ones the detector hasn't matched)."""
    from .faces import embed_region

    embedder = embedder or embed_region
    with session_scope() as s:
        ids = s.exec(select(Face.id).where(Face.embedding == None)).all()  # noqa: E711
    found = 0
    for i, fid in enumerate(ids):
        ctx.check_cancel()
        ctx.progress(i, len(ids), "embedding drawn faces")
        with session_scope() as s:
            f = s.get(Face, fid)
            p = s.get(Photo, f.photo_id)
            box = rotate_box((f.x, f.y, f.w, f.h), f.frame_rotation, p.rotation)
            d = embedder(_display_rgb(p), box)
            if d is not None:
                f.embedding, f.det_score, f.est_age, f.detector_ver = to_blob(d.embedding), d.det_score, d.age, DETECTOR_VERSION
                found += 1
            else:
                f.detector_ver = "none"  # box kept for export; nothing to embed
                f.embedding = b""
            s.add(f)
    return {"faces": len(ids), "embedded": found}


# --- clustering -------------------------------------------------------------------


def _emb(f: Face) -> Optional[np.ndarray]:
    if not f.embedding:
        return None
    return from_blob(f.embedding)


def run_cluster(ctx: JobContext = NULL_CTX) -> dict:
    """Group unnamed, un-ignored faces so a whole group can be named at once."""
    from scipy.cluster.hierarchy import fcluster, linkage

    with session_scope() as s:
        faces = [
            f for f in s.exec(select(Face).where(Face.person_id == None, Face.ignored == False)).all()  # noqa: E711,E712
            if f.embedding
        ]
        for f in s.exec(select(Face).where(Face.cluster_id != None)).all():  # noqa: E711
            f.cluster_id = None
            s.add(f)
        if len(faces) < 2:
            return {"faces": len(faces), "clusters": 0}
        ctx.progress(0, len(faces), "clustering faces")
        x = np.stack([from_blob(f.embedding) for f in faces])
        x /= np.linalg.norm(x, axis=1, keepdims=True) + 1e-9
        labels = fcluster(linkage(x, method=CLUSTER_METHOD, metric="cosine"), t=CLUSTER_DISTANCE, criterion="distance")
        sizes: dict[int, int] = {}
        for lab in labels:
            sizes[lab] = sizes.get(lab, 0) + 1
        # Stable, readable ids: biggest cluster = 1.
        order = {lab: i + 1 for i, lab in enumerate(sorted(sizes, key=lambda k: -sizes[k]))}
        for f, lab in zip(faces, labels):
            f.cluster_id = order[lab] if sizes[lab] > 1 else None
            s.add(f)
        multi = sum(1 for n in sizes.values() if n > 1)
    return {"faces": len(faces), "clusters": multi, "singletons": sum(1 for n in sizes.values() if n == 1)}


def person_centroids(s: Session) -> dict[int, np.ndarray]:
    sums: dict[int, list[np.ndarray]] = {}
    for f in s.exec(select(Face).where(Face.person_id != None, Face.ignored == False)).all():  # noqa: E711,E712
        e = _emb(f)
        if e is not None:
            sums.setdefault(f.person_id, []).append(e)
    out = {}
    for pid, embs in sums.items():
        c = np.mean(embs, axis=0)
        out[pid] = c / (np.linalg.norm(c) + 1e-9)
    return out


def suggest(embs: list[np.ndarray], centroids: dict[int, np.ndarray]) -> Optional[tuple[int, float]]:
    """Best named person for a group of faces (suggestion only, never auto-applied)."""
    if not embs or not centroids:
        return None
    c = np.mean(embs, axis=0)
    c /= np.linalg.norm(c) + 1e-9
    pid, sim = max(((pid, float(c @ v)) for pid, v in centroids.items()), key=lambda t: t[1])
    return (pid, sim) if sim >= SUGGEST_MIN_SIMILARITY else None


# --- naming ---------------------------------------------------------------------------


def get_or_create_person(s: Session, name: str) -> Person:
    name = name.strip()
    person = s.exec(select(Person).where(Person.name == name)).first()
    if person is None:
        person = Person(name=name)
        s.add(person)
        s.flush()
    return person


def person_for_name(s: Session, name: str) -> tuple[Person, list]:
    """The person with this name, plus the edit-log change that adds them if they're new
    (so undoing the naming also removes the person it created)."""
    name = name.strip()
    person = s.exec(select(Person).where(Person.name == name)).first()
    if person is not None:
        return person, []
    person = Person(name=name)
    s.add(person)
    s.flush()
    return person, [(person, editlog.CREATED, True)]


def assign(s: Session, face_ids: list[int], person_id: Optional[int], description: str = "",
           extra: Iterable[tuple] = ()) -> Optional[str]:
    faces = s.exec(select(Face).where(Face.id.in_(face_ids))).all()
    changes = list(extra)
    for f in faces:
        changes += [(f, "person_id", person_id), (f, "ignored", False), (f, "suggested_person_id", None)]
        if person_id is not None and f.source in ("auto", REVERTED):
            changes.append((f, "source", "manual"))
    if not description:
        who = s.get(Person, person_id).name if person_id else "nobody"
        description = f"{len(faces)} face(s) → {who}"
    return editlog.record(s, changes, description)


def accept(s: Session, face_ids: list[int]) -> Optional[str]:
    """Confirm automatic names and suggestions; confirmed faces become exemplars."""
    changes = []
    for f in s.exec(select(Face).where(Face.id.in_(face_ids))).all():
        if f.person_id is None and f.suggested_person_id is not None:
            changes.append((f, "person_id", f.suggested_person_id))
        if f.person_id is not None or f.suggested_person_id is not None:
            changes.append((f, "suggested_person_id", None))
            if f.source in ("auto", REVERTED):
                changes.append((f, "source", "accepted"))
    return editlog.record(s, changes, f"confirm {len(face_ids)} name(s)")


def reject(s: Session, face_ids: list[int]) -> Optional[str]:
    """"Not this person": remember it so the same name isn't suggested again for this face."""
    changes = []
    for f in s.exec(select(Face).where(Face.id.in_(face_ids))).all():
        target = f.person_id or f.suggested_person_id
        if target is None:
            continue
        rejected = sorted(set(f.rejected_person_ids or []) | {target})
        changes += [(f, "person_id", None), (f, "suggested_person_id", None), (f, "rejected_person_ids", rejected)]
    return editlog.record(s, changes, f"not that person ({len(face_ids)} face(s))")


def revert_run(s: Session, run_id: str) -> Optional[str]:
    """Clear one naming run's automatic names (as an undoable batch).

    The faces are marked so later runs only suggest names for them; otherwise the next
    run, which almost any edit triggers, would name them again. Date estimates can't be
    reverted this way: they're recomputed from their evidence, so fix the evidence instead.
    """
    if not run_id.startswith("names-"):
        raise ValueError("only naming runs can be reverted; date estimates follow their evidence")
    changes = []
    for f in s.exec(select(Face).where(Face.run_id == run_id, Face.source == "auto", Face.person_id != None)).all():  # noqa: E711
        changes += [(f, "person_id", None), (f, "source", REVERTED)]
    return editlog.record(s, changes, f"revert {run_id}")


def set_ignored(s: Session, face_ids: list[int], ignored: bool) -> Optional[str]:
    faces = s.exec(select(Face).where(Face.id.in_(face_ids))).all()
    changes = [(f, "ignored", ignored) for f in faces]
    if ignored:
        changes += [(f, "person_id", None) for f in faces]
    return editlog.record(s, changes, f"{'ignore' if ignored else 'un-ignore'} {len(faces)} face(s)")


def merge_people(s: Session, source_id: int, target_id: int) -> Optional[str]:
    faces = s.exec(select(Face).where(Face.person_id == source_id)).all()
    src, dst = s.get(Person, source_id), s.get(Person, target_id)
    changes = [(f, "person_id", target_id) for f in faces]
    if dst.birth_year is None and src.birth_year is not None:
        changes.append((dst, "birth_year", src.birth_year))
    return editlog.record(s, changes, f"merge {src.name} into {dst.name}")


# --- crops ----------------------------------------------------------------------------


def face_crop(f: Face, p: Photo, size: int = 200) -> Path:
    """Square-ish crop around a face in the photo's current rotation, cached on disk."""
    settings = get_settings()
    box = rotate_box((f.x, f.y, f.w, f.h), f.frame_rotation, p.rotation)
    key = f"{f.id}_{p.rotation}_{hash(box) & 0xffffffff:x}_{size}"
    out = settings.data_dir / "faces" / f"{key}.jpg"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    img = open_display(settings.photos_root / p.rel_path, p.rotation, max_size=DETECT_EDGE).convert("RGB")
    W, H = img.size
    x, y, w, h = box
    cx, cy, side = (x + w / 2) * W, (y + h / 2) * H, max(w * W, h * H) * 1.5
    crop = img.crop((int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2)))
    crop.thumbnail((size, size), Image.Resampling.LANCZOS)
    save_jpeg(crop, out, quality=85)
    return out
