"""Scan the photo root into albums and photos.

Re-runnable: photos are identified by file SHA-256, then by exiftool's
ImageDataHash (survives metadata-only edits by other apps), so renamed or moved
files keep their tags. Unchanged files (same path, size, mtime) aren't re-hashed.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from natsort import natsort_keygen
from PIL import Image
from sqlmodel import Session, select

from .config import IMAGE_EXTENSIONS, get_settings
from .db import session_scope
from .exif import ExifTool
from .images import THUMB_VERSION, ensure_thumb, hamming_groups, open_display, phash, sha256_file
from .jobs_ctx import NULL_CTX, JobContext
from .models import Album, LooseBack, Photo, utcnow

log = logging.getLogger(__name__)

ROOT_ALBUM = "(root)"
COMMIT_EVERY = 50
_natkey = natsort_keygen()
_BACKLIKE = re.compile(r"(back|reverse|verso|rear)", re.I)


@dataclass
class ScannedFront:
    rel_path: str
    album_rel: str
    album_index: int
    size: int
    mtime: float
    back_rel: str | None = None
    list_index: int = 0
    name_years: tuple[int, int] | None = None


@dataclass
class ScannedLooseBack:
    rel_path: str
    album_rel: str
    list_index: int
    size: int
    mtime: float


@dataclass
class ScanResult:
    albums: list[str] = field(default_factory=list)
    fronts: list[ScannedFront] = field(default_factory=list)
    loose_backs: list[ScannedLooseBack] = field(default_factory=list)
    backlike_unmatched: list[str] = field(default_factory=list)

    @property
    def orphan_backs(self) -> list[str]:
        return [b.rel_path for b in self.loose_backs]


def name_years(stem: str, pattern: str) -> tuple[int, int] | None:
    if not pattern:
        return None
    m = re.search(pattern, stem)
    if not m:
        return None
    a = int(m.group("a"))
    b = int(m.group("b")) if m.groupdict().get("b") else a
    return (min(a, b), max(a, b))


def scan_tree(root: Path, back_pattern: str, name_year_pattern: str = "") -> ScanResult:
    pattern = re.compile(back_pattern, re.I)
    result = ScanResult()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted((d for d in dirnames if not d.startswith(".")), key=_natkey)
        images = sorted(
            (f for f in filenames if not f.startswith(".") and Path(f).suffix.lower() in IMAGE_EXTENSIONS),
            key=_natkey,
        )
        if not images:
            continue
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        album_rel = ROOT_ALBUM if rel_dir == "." else rel_dir
        result.albums.append(album_rel)
        position = {f: i for i, f in enumerate(images)}

        stems: dict[str, str] = {}
        for f in images:
            stems.setdefault(Path(f).stem.lower(), f)
        backs: dict[str, str] = {}  # front stem (lower) -> back filename
        fronts: list[str] = []
        for f in images:
            m = pattern.match(Path(f).stem)
            if m and m.group("stem").lower() in stems and stems[m.group("stem").lower()] != f:
                backs[m.group("stem").lower()] = f
            elif m:
                # A back whose front isn't here: kept as a loose back that dates nearby fronts.
                st = (Path(dirpath) / f).stat()
                result.loose_backs.append(
                    ScannedLooseBack(_rel(rel_dir, f), album_rel, position[f], st.st_size, st.st_mtime)
                )
            else:
                fronts.append(f)
                if _BACKLIKE.search(Path(f).stem):
                    result.backlike_unmatched.append(_rel(rel_dir, f))

        for idx, f in enumerate(fronts):
            st = (Path(dirpath) / f).stat()
            back = backs.get(Path(f).stem.lower())
            result.fronts.append(
                ScannedFront(
                    rel_path=_rel(rel_dir, f),
                    album_rel=album_rel,
                    album_index=idx,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    back_rel=_rel(rel_dir, back) if back else None,
                    list_index=position[f],
                    name_years=name_years(Path(f).stem, name_year_pattern),
                )
            )
    return result


def _rel(rel_dir: str, name: str) -> str:
    return name if rel_dir == "." else f"{rel_dir}/{name}"


def _find(d: dict, suffix: str):
    for k, v in d.items():
        if k == suffix or k.endswith(":" + suffix):
            return v
    return None


def run_ingest(ctx: JobContext = NULL_CTX) -> dict:
    settings = get_settings()
    root = settings.photos_root
    if not root.is_dir():
        raise FileNotFoundError(f"photo root {root} is not a directory (check PHOTO_ROOT mount)")
    ctx.progress(0, 0, "scanning folders")
    scan = scan_tree(root, settings.back_pattern, settings.name_year_pattern)
    report: dict = {
        "albums": len(scan.albums),
        "fronts_seen": len(scan.fronts),
        "backs_paired": sum(1 for f in scan.fronts if f.back_rel),
        "loose_backs": len(scan.loose_backs),
        "orphan_backs": scan.orphan_backs,
        "backlike_unmatched": scan.backlike_unmatched,
        "new": 0,
        "moved": 0,
        "metadata_changed": 0,
        "missing": [],
        "exact_duplicate_files": [],
        "unreadable": [],
    }

    with session_scope() as s:
        album_ids = _upsert_albums(s, scan.albums)

    seen_ids: set[int] = set()
    total = len(scan.fronts)
    with ExifTool() as et:
        for i, front in enumerate(scan.fronts):
            ctx.check_cancel()
            if i % COMMIT_EVERY == 0:
                ctx.progress(i, total, f"hashing {front.rel_path}")
            try:
                with session_scope() as s:
                    pid = _ingest_one(s, et, root, front, album_ids[front.album_rel], report)
                if pid:
                    seen_ids.add(pid)
            except Exception as exc:  # unreadable file shouldn't stop the run
                log.warning("ingest failed for %s: %s", front.rel_path, exc)
                report["unreadable"].append({"path": front.rel_path, "error": str(exc)})

    with session_scope() as s:
        for p in s.exec(select(Photo).where(Photo.missing == False)).all():  # noqa: E712
            if p.id not in seen_ids:
                p.missing = True
                s.add(p)
                report["missing"].append(p.rel_path)

    _sync_loose_backs(root, scan.loose_backs, album_ids, ctx)

    report["thumbnails"] = make_thumbnails(ctx)
    report["duplicate_groups"] = group_duplicates()
    ctx.progress(total, total, "done")
    return report


def _upsert_albums(s: Session, album_rels: list[str]) -> dict[str, int]:
    existing = {a.rel_path: a for a in s.exec(select(Album)).all()}
    ordered = sorted(album_rels, key=_natkey)
    for order, rel in enumerate(ordered):
        a = existing.get(rel)
        if a is None:
            a = Album(rel_path=rel, name=Path(rel).name if rel != ROOT_ALBUM else rel)
            existing[rel] = a
        a.sort_order = order
        a.missing = False
        s.add(a)
    for rel, a in existing.items():
        if rel not in album_rels:
            a.missing = True
            s.add(a)
    s.flush()
    return {rel: existing[rel].id for rel in album_rels}


def _ingest_one(s: Session, et: ExifTool, root: Path, front: ScannedFront, album_id: int, report: dict) -> int | None:
    path = root / front.rel_path
    photo = s.exec(select(Photo).where(Photo.rel_path == front.rel_path)).first()
    unchanged = (
        photo is not None
        and photo.file_size == front.size
        and abs(photo.file_mtime - front.mtime) < 1e-3
    )
    if not unchanged:
        sha = sha256_file(path)
        by_sha = s.exec(select(Photo).where(Photo.sha256 == sha)).first()
        if by_sha is not None and by_sha.rel_path != front.rel_path and (root / by_sha.rel_path).exists() and not by_sha.missing:
            report["exact_duplicate_files"].append({"path": front.rel_path, "same_as": by_sha.rel_path})
            return None
        meta = et.read([path], ["ImageDataHash", "Orientation", "DateTimeOriginal", "CreateDate"],
                       extra=["-api", "ImageHashType=SHA256"])[0]
        idh = _find(meta, "ImageDataHash")
        if by_sha is not None:
            photo = by_sha
            if photo.rel_path != front.rel_path:
                report["moved"] += 1
        else:
            by_idh = s.exec(select(Photo).where(Photo.image_data_hash == idh)).first() if idh else None
            if by_idh is not None and (by_idh.missing or by_idh.rel_path == front.rel_path or not (root / by_idh.rel_path).exists()):
                photo = by_idh
                report["metadata_changed"] += 1
            elif photo is not None:
                # Same path, different pixels: the file was replaced. Treat as a new photo.
                photo.missing = True
                photo.rel_path = f"{photo.rel_path}#replaced-{photo.id}"
                s.add(photo)
                photo = None
            if photo is None:
                photo = Photo(sha256=sha, rel_path=front.rel_path, album_id=album_id)
                report["new"] += 1
        photo.sha256 = sha
        photo.image_data_hash = idh
        photo.exif_orientation = int(_find(meta, "Orientation") or 1)
        photo.scan_datetime = _find(meta, "DateTimeOriginal") or _find(meta, "CreateDate")
        if photo.scan_datetime is not None:
            photo.scan_datetime = str(photo.scan_datetime)
        photo.file_size = front.size
        photo.file_mtime = front.mtime
        photo.thumb_ver = 0
        photo.orient_ver = 0

    photo.rel_path = front.rel_path
    photo.album_id = album_id
    photo.album_index = front.album_index
    photo.list_index = front.list_index
    photo.name_year_min, photo.name_year_max = front.name_years or (None, None)
    photo.missing = False
    if front.back_rel != photo.back_rel_path or (front.back_rel and not photo.back_sha256) or not unchanged:
        new_back = sha256_file(root / front.back_rel) if front.back_rel else None
        if new_back != photo.back_sha256:
            photo.back_ocr_ver = 0
            if photo.back_source != "manual":
                photo.back_year = photo.back_month = photo.back_source = photo.back_confidence = None
        photo.back_rel_path = front.back_rel
        photo.back_sha256 = new_back
    photo.updated_at = utcnow()
    s.add(photo)
    s.flush()
    return photo.id


def _sync_loose_backs(root: Path, scanned: list[ScannedLooseBack], album_ids: dict[str, int], ctx: JobContext) -> None:
    seen: set[str] = set()
    for i, lb in enumerate(scanned):
        ctx.check_cancel()
        if i % COMMIT_EVERY == 0:
            ctx.progress(i, len(scanned), "loose backs")
        seen.add(lb.rel_path)
        with session_scope() as s:
            row = s.exec(select(LooseBack).where(LooseBack.rel_path == lb.rel_path)).first()
            if row is not None and row.file_size == lb.size and abs(row.file_mtime - lb.mtime) < 1e-3:
                row.list_index, row.album_id, row.missing = lb.list_index, album_ids[lb.album_rel], False
                s.add(row)
                continue
            sha = sha256_file(root / lb.rel_path)
            if row is None:
                row = s.exec(select(LooseBack).where(LooseBack.sha256 == sha)).first()  # renamed/moved
            if row is None:
                row = LooseBack(rel_path=lb.rel_path, sha256=sha, album_id=album_ids[lb.album_rel])
            if row.sha256 != sha:
                row.ocr_ver = 0
                if row.d_source != "manual":
                    row.d_year = row.d_month = row.d_source = row.d_confidence = None
            row.rel_path, row.sha256, row.album_id = lb.rel_path, sha, album_ids[lb.album_rel]
            row.list_index, row.file_size, row.file_mtime, row.missing = lb.list_index, lb.size, lb.mtime, False
            s.add(row)
    with session_scope() as s:
        for row in s.exec(select(LooseBack).where(LooseBack.missing == False)).all():  # noqa: E712
            if row.rel_path not in seen:
                row.missing = True
                s.add(row)


def make_thumbnails(ctx: JobContext = NULL_CTX) -> dict:
    settings = get_settings()
    root = settings.photos_root
    with session_scope() as s:
        ids = s.exec(
            select(Photo.id).where(Photo.missing == False, Photo.thumb_ver < THUMB_VERSION)  # noqa: E712
        ).all()
    done = failed = 0
    for i, pid in enumerate(ids):
        ctx.check_cancel()
        if i % 10 == 0:
            ctx.progress(i, len(ids), "thumbnails")
        with session_scope() as s:
            p = s.get(Photo, pid)
            try:
                src = root / p.rel_path
                img = open_display(src, 0, max_size=settings.preview_size)
                with Image.open(src) as raw:
                    w, h = raw.size
                if p.exif_orientation in (5, 6, 7, 8):
                    w, h = h, w
                p.width, p.height = w, h
                p.phash = phash(img)
                ensure_thumb(src, p.sha256, settings.thumb_size, p.rotation)
                if p.back_rel_path and p.back_sha256:
                    ensure_thumb(root / p.back_rel_path, p.back_sha256, settings.thumb_size, 0)
                p.thumb_ver = THUMB_VERSION
                done += 1
            except Exception as exc:
                log.warning("thumbnail failed for %s: %s", p.rel_path, exc)
                failed += 1
            s.add(p)
    return {"generated": done, "failed": failed}


def group_duplicates() -> int:
    settings = get_settings()
    with session_scope() as s:
        photos = s.exec(select(Photo).where(Photo.missing == False, Photo.phash != None)).all()  # noqa: E711,E712
        groups = hamming_groups({p.id: p.phash for p in photos}, settings.dup_phash_distance)
        group_of = {pid: g[0] for g in groups for pid in g}
        for p in photos:
            gid = group_of.get(p.id)
            if p.dup_group_id != gid:
                p.dup_group_id = gid
                if gid is None:
                    p.is_dup_keeper = False
                s.add(p)
    return len(groups)
