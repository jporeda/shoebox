"""Permanent layer: DB snapshots, truth.json, checksum manifest, fixity, restore."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import select

from . import editlog
from .config import get_settings
from .db import get_meta, session_scope, set_meta
from .images import sha256_file
from .jobs_ctx import NULL_CTX, JobContext
from .models import EventInstance, EventKind, Face, LooseBack, Person, Photo, Suggestion, utcnow
from .services import TRUSTED_FIELDS, get_or_create_instance

log = logging.getLogger(__name__)

TRUTH_FORMAT_VERSION = 1


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _prune(directory: Path, pattern: str, keep: int) -> None:
    files = sorted(directory.glob(pattern))
    for old in files[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)


def snapshot(reason: str = "manual") -> Path:
    """Consistent online copy of the live DB via VACUUM INTO."""
    settings = get_settings()
    out_dir = settings.backup_dir / "db"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in reason)[:40]
    out = out_dir / f"shoebox-{_stamp()}-{safe}.db"
    conn = sqlite3.connect(settings.db_path, timeout=30)
    try:
        conn.execute("VACUUM INTO ?", (str(out),))
    finally:
        conn.close()
    if not reason.startswith("pre-upgrade"):
        _prune(out_dir, "shoebox-*.db", settings.snapshot_keep + len(list(out_dir.glob("*pre-upgrade*.db"))))
    return out


# --- truth.json -------------------------------------------------------------


def build_truth() -> dict:
    """Everything a person entered or confirmed, keyed so it survives renames."""
    with session_scope() as s:
        kinds = {k.id: k for k in s.exec(select(EventKind)).all()}
        instances = {i.id: i for i in s.exec(select(EventInstance)).all()}
        people = {p.id: p for p in s.exec(select(Person)).all()}
        photos_out = []
        for p in s.exec(select(Photo)).all():
            entry: dict = {}
            if p.t_year is not None and p.t_source != "event":
                entry["date"] = {f[2:]: getattr(p, f) for f in TRUSTED_FIELDS}
            if p.event_instance_id:
                inst = instances[p.event_instance_id]
                entry["event"] = {"kind": kinds[inst.kind_id].name, "label": inst.label}
            if p.caption:
                entry["caption"] = p.caption
            if p.rotation_source == "manual":
                entry["rotation"] = p.rotation
            if p.is_dup_keeper:
                entry["dup_keeper"] = True
            if p.back_source == "manual":
                entry["back_date"] = {"year": p.back_year, "month": p.back_month}
            faces = [
                {
                    "box": [f.x, f.y, f.w, f.h],
                    "frame_rotation": f.frame_rotation,
                    "detector_ver": f.detector_ver,
                    "source": f.source,
                    "person": people[f.person_id].name if f.person_id else None,
                    "ignored": f.ignored,
                    "not": sorted(people[i].name for i in f.rejected_person_ids or [] if i in people),
                }
                for f in s.exec(select(Face).where(Face.photo_id == p.id)).all()
                if f.source in ("manual", "drawn", "accepted") or f.ignored or f.rejected_person_ids
            ]
            if faces:
                entry["faces"] = faces
            decisions = [
                {"field": g.field, "value": g.value, "state": g.state, "source": g.source}
                for g in s.exec(
                    select(Suggestion).where(Suggestion.photo_id == p.id, Suggestion.state != "pending")
                ).all()
            ]
            if decisions:
                entry["decisions"] = decisions
            if entry:
                entry.update(sha256=p.sha256, image_data_hash=p.image_data_hash, rel_path=p.rel_path)
                photos_out.append(entry)

        loose_out = [
            {"sha256": lb.sha256, "rel_path": lb.rel_path, "date": {"year": lb.d_year, "month": lb.d_month}}
            for lb in s.exec(select(LooseBack).where(LooseBack.d_source == "manual")).all()
        ]

        return {
            "format_version": TRUTH_FORMAT_VERSION,
            "generated_at": utcnow().isoformat(),
            "people": [
                {"name": p.name, "birth_year": p.birth_year, "notes": p.notes} for p in people.values()
            ],
            "event_kinds": [
                {"name": k.name, "month": k.month, "day": k.day} for k in kinds.values() if not k.builtin
            ],
            "event_instances": [
                {"kind": kinds[i.kind_id].name, "label": i.label, "year": i.year, "month": i.month, "day": i.day}
                for i in instances.values()
            ],
            "photos": sorted(photos_out, key=lambda e: e["rel_path"]),
            "loose_backs": sorted(loose_out, key=lambda e: e["rel_path"]),
        }


def export_truth() -> Path:
    settings = get_settings()
    truth_dir = settings.backup_dir / "truth"
    text = json.dumps(build_truth(), indent=1, ensure_ascii=False, sort_keys=True)
    current = truth_dir / "truth.json"
    if current.exists() and _strip_ts(current.read_text(encoding="utf-8")) == _strip_ts(text):
        return current
    _atomic_write(truth_dir / "history" / f"truth-{_stamp()}.json", text)
    _prune(truth_dir / "history", "truth-*.json", settings.truth_history_keep)
    _atomic_write(current, text)
    return current


def _strip_ts(text: str) -> str:
    d = json.loads(text)
    d.pop("generated_at", None)
    return json.dumps(d, sort_keys=True)


# --- manifest + fixity ----------------------------------------------------------


def write_manifest() -> Path:
    """BagIt-style manifest of originals, relative to the photo root.

    Verify without the app:  cd $PHOTO_ROOT && shasum -a 256 -c $BACKUP_ROOT/manifest-sha256.txt
    """
    settings = get_settings()
    lines = []
    with session_scope() as s:
        for p in s.exec(select(Photo).where(Photo.missing == False).order_by(Photo.rel_path)).all():  # noqa: E712
            lines.append(f"{p.sha256}  {p.rel_path}")
            if p.back_rel_path and p.back_sha256:
                lines.append(f"{p.back_sha256}  {p.back_rel_path}")
        for lb in s.exec(select(LooseBack).where(LooseBack.missing == False)).all():  # noqa: E712
            lines.append(f"{lb.sha256}  {lb.rel_path}")
    lines.sort(key=lambda line: line.split("  ", 1)[1])
    out = settings.backup_dir / "manifest-sha256.txt"
    _atomic_write(out, "\n".join(lines) + ("\n" if lines else ""))
    return out


def run_fixity(ctx: JobContext = NULL_CTX, photo_ids: list[int] | None = None) -> dict:
    """Re-hash originals (and backs) and compare to the recorded hashes."""
    settings = get_settings()
    root = settings.photos_root
    with session_scope() as s:
        q = select(Photo).where(Photo.missing == False)  # noqa: E712
        if photo_ids is not None:
            q = q.where(Photo.id.in_(photo_ids))
        targets = [(p.id, p.rel_path, p.sha256, p.back_rel_path, p.back_sha256) for p in s.exec(q).all()]
        loose = (
            [(lb.rel_path, lb.sha256) for lb in s.exec(select(LooseBack).where(LooseBack.missing == False)).all()]  # noqa: E712
            if photo_ids is None
            else []
        )
    changed, missing, ok = [], [], 0
    for i, (pid, rel, sha, back_rel, back_sha) in enumerate(targets):
        ctx.check_cancel()
        if i % 20 == 0:
            ctx.progress(i, len(targets), "verifying checksums")
        good = True
        for r, expected in ((rel, sha), (back_rel, back_sha)):
            if not r:
                continue
            path = root / r
            if not path.exists():
                missing.append(r)
                good = False
            elif sha256_file(path) != expected:
                changed.append(r)
                good = False
        ok += good
        with session_scope() as s:
            p = s.get(Photo, pid)
            p.fixity_checked_at = utcnow()
            p.fixity_ok = good
            s.add(p)
    for rel, expected in loose:
        path = root / rel
        if not path.exists():
            missing.append(rel)
        elif sha256_file(path) != expected:
            changed.append(rel)
    new_files: list[str] = []
    if photo_ids is None:
        from .ingest import scan_tree

        known = {t[1] for t in targets} | {t[3] for t in targets if t[3]} | {rel for rel, _ in loose}
        scan = scan_tree(root, settings.back_pattern)
        on_disk = {f.rel_path for f in scan.fronts} | {f.back_rel for f in scan.fronts if f.back_rel}
        new_files = sorted(on_disk - known | (set(scan.orphan_backs) - known))
        with session_scope() as s:
            set_meta(s, "last_fixity_at", utcnow())
    return {"checked": len(targets), "ok": ok, "changed": changed, "missing": missing, "new_files": new_files}


def fixity_due() -> bool:
    with session_scope() as s:
        last = get_meta(s, "last_fixity_at")
    if not last:
        return True
    return datetime.fromisoformat(last) < utcnow() - timedelta(days=get_settings().fixity_interval_days)


def run_backup(reason: str = "manual") -> dict:
    return {
        "snapshot": str(snapshot(reason)),
        "truth": str(export_truth()),
        "manifest": str(write_manifest()),
    }


# --- restore -------------------------------------------------------------------


def _restore_faces(s, p: Photo, entries: list[dict]) -> None:
    """Re-attach named/ignored faces: match a detected face by overlap, else recreate the box."""
    from .faces import iou, rotate_box
    from .people import MATCH_IOU, get_or_create_person

    existing = list(s.exec(select(Face).where(Face.photo_id == p.id)).all())
    for e in entries:
        box = rotate_box(tuple(e["box"]), e.get("frame_rotation", 0), p.rotation)
        match = max(
            existing,
            key=lambda f: iou(rotate_box((f.x, f.y, f.w, f.h), f.frame_rotation, p.rotation), box),
            default=None,
        )
        if match is None or iou(rotate_box((match.x, match.y, match.w, match.h), match.frame_rotation, p.rotation), box) < MATCH_IOU:
            match = Face(photo_id=p.id, x=box[0], y=box[1], w=box[2], h=box[3], frame_rotation=p.rotation,
                         source=e.get("source") or "drawn")
            s.add(match)
            s.flush()
            existing.append(match)
        person_id = get_or_create_person(s, e["person"]).id if e.get("person") else None
        changes = [(match, "person_id", person_id), (match, "ignored", bool(e.get("ignored")))]
        if e.get("not"):
            changes.append((match, "rejected_person_ids", sorted(get_or_create_person(s, n).id for n in e["not"])))
        if e.get("source") in ("manual", "drawn", "accepted"):
            changes.append((match, "source", e["source"]))
        editlog.record(s, changes, f"restore faces {p.rel_path}", actor="restore")


def restore_truth(truth: dict, ctx: JobContext = NULL_CTX) -> dict:
    """Apply a truth export onto the current DB (photos must already be ingested)."""
    if truth.get("format_version", 0) > TRUTH_FORMAT_VERSION:
        raise ValueError(f"truth format {truth['format_version']} is newer than supported {TRUTH_FORMAT_VERSION}")
    matched, unmatched = 0, []
    with session_scope() as s:
        for person in truth.get("people", []):
            existing = s.exec(select(Person).where(Person.name == person["name"])).first()
            changes = []
            if existing is None:
                existing = Person(name=person["name"])
                s.add(existing)
                s.flush()
            for f in ("birth_year", "notes"):
                changes.append((existing, f, person.get(f)))
            editlog.record(s, changes, f"restore person {person['name']}", actor="restore")
        for k in truth.get("event_kinds", []):
            if not s.exec(select(EventKind).where(EventKind.name == k["name"])).first():
                s.add(EventKind(name=k["name"], month=k.get("month"), day=k.get("day")))
        s.flush()
        kinds = {k.name: k for k in s.exec(select(EventKind)).all()}
        for i in truth.get("event_instances", []):
            inst = get_or_create_instance(s, kinds[i["kind"]], i.get("year"), i["label"])
            inst.month, inst.day = i.get("month"), i.get("day")
            s.add(inst)

        entries = truth.get("photos", [])
        for n, e in enumerate(entries):
            if n % 50 == 0:
                ctx.progress(n, len(entries), "restoring")
            p = (
                s.exec(select(Photo).where(Photo.sha256 == e["sha256"])).first()
                or (e.get("image_data_hash") and s.exec(select(Photo).where(Photo.image_data_hash == e["image_data_hash"])).first())
                or s.exec(select(Photo).where(Photo.rel_path == e["rel_path"])).first()
            )
            if not p:
                unmatched.append(e["rel_path"])
                continue
            matched += 1
            changes = []
            if "date" in e:
                changes += [(p, f, e["date"][f[2:]]) for f in TRUSTED_FIELDS]
            if "event" in e:
                inst = get_or_create_instance(s, kinds[e["event"]["kind"]], None, e["event"]["label"])
                changes.append((p, "event_instance_id", inst.id))
            if "caption" in e:
                changes.append((p, "caption", e["caption"]))
            if "rotation" in e:
                changes += [(p, "rotation", e["rotation"]), (p, "rotation_source", "manual")]
            if e.get("dup_keeper"):
                changes.append((p, "is_dup_keeper", True))
            if "back_date" in e:
                changes += [
                    (p, "back_year", e["back_date"]["year"]),
                    (p, "back_month", e["back_date"]["month"]),
                    (p, "back_source", "manual"),
                ]
            editlog.record(s, changes, f"restore {p.rel_path}", actor="restore")
            _restore_faces(s, p, e.get("faces", []))
        for e in truth.get("loose_backs", []):
            lb = (
                s.exec(select(LooseBack).where(LooseBack.sha256 == e["sha256"])).first()
                or s.exec(select(LooseBack).where(LooseBack.rel_path == e["rel_path"])).first()
            )
            if not lb:
                unmatched.append(e["rel_path"])
                continue
            matched += 1
            editlog.record(
                s,
                [(lb, "d_year", e["date"]["year"]), (lb, "d_month", e["date"]["month"]),
                 (lb, "d_source", "manual"), (lb, "d_confidence", 1.0)],
                f"restore {lb.rel_path}",
                actor="restore",
            )
    from .estimate import run_estimates

    run_estimates()
    return {"matched": matched, "unmatched": unmatched}
