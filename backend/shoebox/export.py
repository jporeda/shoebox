"""Export: embedded-metadata copies (primary) and an XMP sidecar overlay.

Originals are never written. Copies go to <backup>/export/<YYYY>/ with
chronological names; sidecars go to <backup>/sidecars/<rel_path>.xmp
(the ``file.jpg.xmp`` convention Immich and digiKam read).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from sqlmodel import Session, select

from .backup import run_fixity
from .config import get_settings
from .dates import export_calendar_date, xmp_partial
from .db import session_scope
from .exif import ExifTool, escape
from .jobs_ctx import NULL_CTX, JobContext
from .faces import rotate_box
from .models import Album, EventInstance, EventKind, Face, Person, Photo

log = logging.getLogger(__name__)

EXPORT_VERSION = 2  # 2: people keywords and MWG face regions
FIRST_TIME = 9 * 3600  # ordinals count seconds from 09:00:00
ROT_FROM_ORIENTATION = {1: 0, 6: 90, 3: 180, 8: 270}
ORIENTATION_FROM_ROT = {v: k for k, v in ROT_FROM_ORIENTATION.items()}
IPTC_FORMATS = {".jpg", ".jpeg", ".tif", ".tiff"}


@dataclass
class ExportItem:
    photo_id: int
    rel_path: str
    sha256: str
    image_data_hash: str | None
    album_name: str
    album_order: int
    album_index: int
    year: int
    month: int | None
    day: int | None
    precision: str | None
    inferred: bool
    date_source: str | None
    confidence: float | None
    run_id: str | None
    exif_orientation: int
    rotation: int
    scan_datetime: str | None
    caption: str | None
    event_kind: str | None
    event_label: str | None
    keywords: list[str] = field(default_factory=list)
    hierarchical: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    # Named faces: (name, x, y, w, h) normalized, in the display frame at the photo's rotation.
    faces: list[tuple[str, float, float, float, float]] = field(default_factory=list)
    width: int = 0  # display size at rotation 0 (after the file's own EXIF orientation)
    height: int = 0
    cal: tuple[int, int, int] = (0, 0, 0)
    ordinal: int = 0

    @property
    def clock(self) -> time:
        secs = min(FIRST_TIME + self.ordinal, 24 * 3600 - 1)
        return time(secs // 3600, secs % 3600 // 60, secs % 60)

    def export_name(self) -> str:
        y, m, d = self.cal
        stem = Path(self.rel_path).stem
        ext = Path(self.rel_path).suffix.lower()
        return f"{y:04d}/{y:04d}-{m:02d}-{d:02d}_{self.ordinal:04d}_{_slug(self.album_name)}_{_slug(stem)}{ext}"

    def signature(self) -> str:
        d = {k: v for k, v in self.__dict__.items()}
        d["v"] = EXPORT_VERSION
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:40] or "x"


def build_plan(session: Session) -> list[ExportItem]:
    """Dated photos in chronological order with per-date ordinals across all albums."""
    albums = {a.id: a for a in session.exec(select(Album)).all()}
    kinds = {k.id: k for k in session.exec(select(EventKind)).all()}
    instances = {i.id: i for i in session.exec(select(EventInstance)).all()}
    photos = session.exec(select(Photo).where(Photo.missing == False)).all()  # noqa: E712
    names = {p.id: p.name for p in session.exec(select(Person)).all()}
    faces_by_photo: dict[int, list[Face]] = {}
    # Only names a person gave or confirmed leave the app; automatic names stay in review.
    for f in session.exec(
        select(Face).where(Face.person_id != None, Face.ignored == False, Face.source != "auto")  # noqa: E711,E712
    ).all():
        faces_by_photo.setdefault(f.photo_id, []).append(f)

    # One file per duplicate group: the keeper, else the lowest id.
    groups: dict[int, list[Photo]] = {}
    for p in photos:
        if p.dup_group_id is not None:
            groups.setdefault(p.dup_group_id, []).append(p)
    skip = set()
    for members in groups.values():
        keep = next((m for m in members if m.is_dup_keeper), min(members, key=lambda m: m.id))
        skip |= {m.id for m in members if m.id != keep.id}

    items: list[ExportItem] = []
    for p in photos:
        if p.id in skip:
            continue
        inferred = p.t_year is None
        year = p.i_year if inferred else p.t_year
        if year is None:
            continue
        album = albums[p.album_id]
        inst = instances.get(p.event_instance_id) if p.event_instance_id else None
        kind = kinds[inst.kind_id] if inst else None
        it = ExportItem(
            photo_id=p.id,
            rel_path=p.rel_path,
            sha256=p.sha256,
            image_data_hash=p.image_data_hash,
            album_name=album.name,
            album_order=album.sort_order,
            album_index=p.album_index,
            year=year,
            month=p.i_month if inferred else p.t_month,
            day=None if inferred else p.t_day,
            precision=p.i_precision if inferred else p.t_precision,
            inferred=inferred,
            date_source="inferred" if inferred else p.t_source,
            confidence=p.i_confidence if inferred else 1.0,
            run_id=p.i_run_id if inferred else None,
            exif_orientation=p.exif_orientation,
            rotation=p.rotation,
            scan_datetime=p.scan_datetime,
            caption=p.caption,
            event_kind=kind.name if kind else None,
            event_label=inst.label if inst else None,
        )
        it.cal = export_calendar_date(it.year, it.month, it.day, it.precision)
        kw, hier = [], []
        if kind:
            kw.append(kind.name)
            hier.append(f"Events|{kind.name}|{inst.label}")
            if inst.label != kind.name:
                kw.append(inst.label)
        for f in sorted(faces_by_photo.get(p.id, []), key=lambda f: f.x):
            it.faces.append((names[f.person_id], *rotate_box((f.x, f.y, f.w, f.h), f.frame_rotation, p.rotation)))
        it.people = sorted({name for name, *_ in it.faces})
        it.width, it.height = p.width or 0, p.height or 0
        kw += it.people
        hier += [f"People|{name}" for name in it.people]
        kw.append(f"Album: {album.name}")
        hier.append(f"Albums|{album.name}")
        if inferred or it.precision in ("range", "decade"):
            kw.append("date:estimated")
            hier.append("Dates|Estimated")
        it.keywords, it.hierarchical = kw, hier
        items.append(it)

    items.sort(key=lambda i: (i.cal, i.album_order, i.album_index, i.photo_id))
    prev, n = None, 0
    for it in items:
        n = n + 1 if it.cal == prev else 0
        it.ordinal, prev = n, it.cal
    return items


def _struct(value: str) -> str:
    """Escape a value inside an exiftool serialized structure."""
    return "".join("|" + c if c in ",]}|=[{" else c for c in escape(value))


def region_args(it: ExportItem, frame_rot: int, dims: tuple[int, int]) -> list[str]:
    """MWG face regions for named faces. frame_rot: rotation of the pixels the regions describe,
    relative to the photo's display at rotation 0; dims: those pixels' width and height."""
    if not it.faces or not all(dims):
        return ["-XMP-mwg-rs:RegionInfo="]
    regions = []
    for name, x, y, w, h in it.faces:
        bx, by, bw, bh = rotate_box((x, y, w, h), it.rotation, frame_rot)
        regions.append(
            "{Area={X=%.5f,Y=%.5f,W=%.5f,H=%.5f,Unit=normalized},Name=%s,Type=Face}"
            % (bx + bw / 2, by + bh / 2, bw, bh, _struct(name))
        )
    w, h = dims
    return [f"-XMP-mwg-rs:RegionInfo={{AppliedToDimensions={{W={w},H={h},Unit=pixel}},RegionList=[{','.join(regions)}]}}"]


def _dims(it: ExportItem, rot: int) -> tuple[int, int]:
    return (it.height, it.width) if rot % 180 else (it.width, it.height)


def tag_args(it: ExportItem, tz: str, ext: str) -> list[str]:
    y, m, d = it.cal
    c = it.clock
    stamp = f"{y:04d}:{m:02d}:{d:02d} {c.hour:02d}:{c.minute:02d}:{c.second:02d}"
    args = [
        f"-EXIF:DateTimeOriginal={stamp}",
        f"-EXIF:OffsetTimeOriginal={tz}",
        f"-XMP-exif:DateTimeOriginal={stamp}{tz}",
        f"-XMP-xmp:CreateDate={stamp}{tz}",
        f"-FileModifyDate={stamp}{tz}",
        "-XMP-dc:Subject=",
        "-XMP-lr:HierarchicalSubject=",
        "-XMP-iptcExt:Event=",
        "-XMP-dc:Description=",
    ]
    if it.scan_datetime:
        args.append(f"-EXIF:CreateDate={it.scan_datetime}")  # = DateTimeDigitized
    args += [f"-XMP-dc:Subject={escape(k)}" for k in it.keywords]
    args += [f"-XMP-lr:HierarchicalSubject={escape(h)}" for h in it.hierarchical]
    if it.event_label:
        args.append(f"-XMP-iptcExt:Event={escape(it.event_label)}")
    args.append("-XMP-iptcExt:PersonInImage=")
    args += [f"-XMP-iptcExt:PersonInImage={escape(n)}" for n in it.people]
    if it.caption:
        args += [f"-XMP-dc:Description={escape(it.caption)}", f"-EXIF:ImageDescription={escape(it.caption)}"]
    if ext in IPTC_FORMATS:
        args += ["-IPTC:CodedCharacterSet=UTF8", "-IPTC:Keywords="]
        args += [f"-IPTC:Keywords={escape(k)}" for k in it.keywords]
        if it.caption:
            args.append(f"-IPTC:Caption-Abstract={escape(it.caption)}")
        args.append("-Photoshop:IPTCDigest=new")
    args += [
        f"-XMP-shoebox:PhotoID={it.photo_id}",
        f"-XMP-shoebox:OriginalSHA256={it.sha256}",
        f"-XMP-shoebox:OriginalPath={escape(it.rel_path)}",
        f"-XMP-shoebox:DateSource={it.date_source or ''}",
        f"-XMP-shoebox:DatePrecision={it.precision or 'year'}",
    ]
    if it.image_data_hash:
        args.append(f"-XMP-shoebox:ImageDataHash={it.image_data_hash}")
    if it.confidence is not None:
        args.append(f"-XMP-shoebox:DateConfidence={it.confidence:.3f}")
    if it.run_id:
        args.append(f"-XMP-shoebox:RunID={it.run_id}")
    # Written last so nothing else overwrites the partial date.
    args.append(f"-XMP-photoshop:DateCreated#={xmp_partial(it.year, it.month, it.day, it.precision)}")
    return args


def _bake_rotation(src: Path, tmp: Path, it: ExportItem) -> tuple[bool, int | None]:
    """Copy src to tmp, baking rotation into pixels when lossless.

    Returns (baked, orientation_tag_to_write).
    """
    base = ROT_FROM_ORIENTATION.get(it.exif_orientation)
    if base is None:  # mirrored orientation: leave the file's own tag alone
        shutil.copyfile(src, tmp)
        return False, None
    total = (base + it.rotation) % 360
    if total == 0:
        shutil.copyfile(src, tmp)
        return True, 1 if it.exif_orientation != 1 else None
    if src.suffix.lower() in (".jpg", ".jpeg"):
        proc = subprocess.run(
            ["jpegtran", "-copy", "all", "-perfect", "-rotate", str(total), "-outfile", str(tmp), str(src)],
            capture_output=True,
        )
        if proc.returncode == 0:
            return True, 1
        log.info("lossless rotate not possible for %s (%s); using Orientation tag", it.rel_path, proc.stderr[:200])
    shutil.copyfile(src, tmp)
    return False, ORIENTATION_FROM_ROT[total]


def run_export(ctx: JobContext = NULL_CTX, sidecars: bool = True) -> dict:
    settings = get_settings()
    root = settings.photos_root
    out_root = settings.backup_dir / "export"
    side_root = settings.backup_dir / "sidecars"
    index_path = out_root / "export-index.json"
    out_root.mkdir(parents=True, exist_ok=True)
    old_index: dict = json.loads(index_path.read_text()) if index_path.exists() else {}

    with session_scope() as s:
        plan = build_plan(s)

    ctx.progress(0, len(plan), "verifying originals before export")
    fix = run_fixity(ctx, photo_ids=[it.photo_id for it in plan])
    bad = set(fix["changed"]) | set(fix["missing"])

    new_index: dict = {}
    written = skipped_same = 0
    failed: list[dict] = []
    not_baked: list[str] = []
    with ExifTool() as et:
        for i, it in enumerate(plan):
            ctx.check_cancel()
            if i % 10 == 0:
                ctx.progress(i, len(plan), f"exporting {it.rel_path}")
            if it.rel_path in bad:
                failed.append({"path": it.rel_path, "error": "original failed fixity check"})
                continue
            name, sig = it.export_name(), it.signature()
            prev = old_index.get(str(it.photo_id))
            if prev and prev["sig"] == sig and (out_root / prev["path"]).exists():
                new_index[str(it.photo_id)] = prev
                skipped_same += 1
                continue
            dest = out_root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f".{dest.name}.tmp{dest.suffix}")
            try:
                baked, orient = _bake_rotation(root / it.rel_path, tmp, it)
                args = tag_args(it, settings.export_tz_offset, dest.suffix.lower())
                if orient is not None:
                    args.insert(0, f"-Orientation#={orient}")
                args.append(f"-XMP-shoebox:RotationBaked={'True' if baked else 'False'}")
                if baked:
                    args += region_args(it, it.rotation, _dims(it, it.rotation))
                else:
                    raw = -ROT_FROM_ORIENTATION.get(it.exif_orientation, 0)
                    args += region_args(it, raw, _dims(it, raw))
                et.write(tmp, ["-charset", "iptc=UTF8", *args])
                os.replace(tmp, dest)
                if not baked and it.rotation:
                    not_baked.append(it.rel_path)
                if sidecars:
                    _write_sidecar(et, dest, side_root / f"{it.rel_path}.xmp", it)
                new_index[str(it.photo_id)] = {"path": name, "sig": sig}
                written += 1
            except Exception as exc:
                tmp.unlink(missing_ok=True)
                log.warning("export failed for %s: %s", it.rel_path, exc)
                failed.append({"path": it.rel_path, "error": str(exc)})

    # Remove files from earlier exports that moved or dropped out (only ones we wrote).
    removed = 0
    for pid, entry in old_index.items():
        if new_index.get(pid, {}).get("path") != entry["path"]:
            (out_root / entry["path"]).unlink(missing_ok=True)
            removed += 1
    index_path.write_text(json.dumps(new_index, indent=1, sort_keys=True))
    return {
        "planned": len(plan),
        "written": written,
        "unchanged": skipped_same,
        "removed_stale": removed,
        "failed": failed,
        "rotation_tag_only": not_baked,
        "fixity": {k: fix[k] for k in ("checked", "changed", "missing")},
    }


def _write_sidecar(et: ExifTool, exported: Path, side: Path, it: ExportItem) -> None:
    """XMP sidecar describing the *original* (rotation expressed as a tag)."""
    side.parent.mkdir(parents=True, exist_ok=True)
    side.unlink(missing_ok=True)
    base = ROT_FROM_ORIENTATION.get(it.exif_orientation, 0)
    orient = ORIENTATION_FROM_ROT[(base + it.rotation) % 360]
    out, err = et.execute(
        "-tagsFromFile", str(exported), "-xmp:all",
        f"-XMP-tiff:Orientation#={orient}",
        "-XMP-shoebox:RotationBaked=False",
        # regions describe the original's raw pixels (its Orientation tag rotates them for display)
        *region_args(it, -base, _dims(it, -base)),
        "-o", str(side), str(exported),
    )
    if not side.exists():
        raise RuntimeError(f"sidecar not written: {out} {' '.join(err)}")
