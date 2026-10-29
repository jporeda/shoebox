"""Date estimates for photos nobody has dated yet.

Evidence (each with a year range and a weight):
  own_back    the photo's own back: printed lab date, or a date someone typed in
  loose_back  the nearest dated loose back within ``loose_back_window`` files
  neighbours  confirmed dates earlier/later in the same album, interpolated by position
  age         a named person with a birth year and an estimated age in the photo
  file_name   years in the file name (e.g. Album3_1984_1986_0161 -> 1984-1986)

Anchors for album-neighbour dating are dates a person entered or accepted, back dates
a person typed, and — at reduced weight (``OCR_ANCHOR_TRUST``) — back dates read by
OCR, which measured ~95% correct on these albums. Only faces a person named count for
ages. Estimates are outputs only — never inputs — so one wrong guess can't spread. Estimates live in the ``i_*`` fields, never touch a
date a person entered, show as "≈" in the UI, and export with ``date:estimated``.
"""

from __future__ import annotations

import uuid
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Iterable, Optional

from sqlmodel import select

from .config import get_settings
from .db import session_scope
from .jobs_ctx import NULL_CTX, JobContext
from .models import Face, LooseBack, Person, Photo

OWN_BACK = 0.9
LOOSE_BACK = 0.7  # nearest neighbour; minus LOOSE_STEP per extra file of distance
LOOSE_STEP = 0.1
MANUAL_BONUS = 0.1
FILENAME = 0.35
NEIGHBOUR_BOTH = 0.75  # between two anchors that agree; drops with the years and files between them
NEIGHBOUR_ONE = 0.5  # past the last anchor in an album
TRUSTED_FACE_SOURCES = ("manual", "drawn", "accepted")
OCR_ANCHOR_TRUST = 0.7  # a machine-read back date anchors neighbours, but counts for less


@dataclass
class Evidence:
    kind: str
    year: int
    month: Optional[int]
    lo: int
    hi: int
    weight: float
    text: str


@dataclass
class Estimate:
    year: int
    month: Optional[int]
    lo: int
    hi: int
    precision: str
    confidence: float
    source: str
    kind: str = ""
    evidence: list[Evidence] = field(default_factory=list)


@dataclass(frozen=True)
class Anchor:
    index: int  # list_index in the album folder
    year: int
    lo: int
    hi: int
    photo_id: Optional[int]
    label: str
    trust: float = 1.0
    loose_back_id: Optional[int] = None


def _label(year: int, month: Optional[int]) -> str:
    return f"{year:04d}-{month:02d}" if month else f"{year:04d}"


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# --- evidence ----------------------------------------------------------------------


def own_back_evidence(p: Photo) -> list[Evidence]:
    if not p.back_year:
        return []
    w = min(OWN_BACK + (MANUAL_BONUS if p.back_source == "manual" else 0), 0.95)
    return [Evidence("own_back", p.back_year, p.back_month, p.back_year, p.back_year, w,
                     f"back says {_label(p.back_year, p.back_month)} ({p.back_source})")]


def loose_back_evidence(p: Photo, loose: list[LooseBack], window: int) -> list[Evidence]:
    near = [(abs(lb.list_index - p.list_index), lb) for lb in loose
            if lb.d_year and abs(lb.list_index - p.list_index) <= window]
    if not near:
        return []
    # Nearest wins; on a tie prefer a reading a person entered.
    dist, lb = min(near, key=lambda t: (t[0], t[1].d_source != "manual"))
    w = LOOSE_BACK - LOOSE_STEP * (dist - 1) + (MANUAL_BONUS if lb.d_source == "manual" else 0)
    name = lb.rel_path.rsplit("/", 1)[-1]
    return [Evidence("loose_back", lb.d_year, lb.d_month, lb.d_year, lb.d_year, round(w, 2),
                     f"loose back {name}, {dist} file{'s' if dist != 1 else ''} away, says "
                     f"{_label(lb.d_year, lb.d_month)} ({lb.d_source})")]


def neighbour_evidence(p: Photo, anchors: list[Anchor], skip_within: int = -1) -> list[Evidence]:
    """Interpolate between the nearest dated photos before and after this one.

    Loose backs within ``skip_within`` files already count as loose-back evidence, so
    they're not used again as anchors.
    """
    others = [a for a in anchors if a.photo_id != p.id
              and not (a.loose_back_id is not None and abs(a.index - p.list_index) <= skip_within)]
    if not others:
        return []
    keys = [a.index for a in others]
    i = bisect_left(keys, p.list_index)
    left = others[i - 1] if i > 0 else None
    right = others[i] if i < len(others) else None
    if left and right:
        span = abs(right.year - left.year)
        gap = max(right.index - left.index, 1)
        if left.year <= right.year:
            year = round(left.year + (right.year - left.year) * (p.list_index - left.index) / gap)
        else:  # album out of order here: stay between them, unsure
            year = round((left.year + right.year) / 2)
        lo, hi = min(left.lo, right.lo), max(left.hi, right.hi)
        w = _clamp(NEIGHBOUR_BOTH - 0.07 * span - 0.005 * gap, 0.25, NEIGHBOUR_BOTH) * min(left.trust, right.trust)
        text = f"between {left.label} and {right.label} in this album"
        return [Evidence("neighbours", year, None, lo, hi, round(w, 2), text)]
    a = left or right
    d = abs(p.list_index - a.index)
    spread = 1 + d // 15
    w = _clamp(NEIGHBOUR_ONE - 0.01 * d, 0.2, NEIGHBOUR_ONE) * a.trust
    where = "after" if left else "before"
    return [Evidence("neighbours", a.year, None, a.lo - spread, a.hi + spread, round(w, 2),
                     f"{d} file{'s' if d != 1 else ''} {where} {a.label} in this album")]


def age_evidence(faces: Iterable[Face], people: dict[int, Person]) -> list[Evidence]:
    out = []
    for f in faces:
        person = people.get(f.person_id) if f.person_id else None
        if (person is None or person.birth_year is None or f.est_age is None or f.ignored
                or f.source not in TRUSTED_FACE_SOURCES):
            continue
        age = max(0.0, f.est_age)
        year = person.birth_year + round(age)
        u = max(2, round(0.3 * age))  # face age estimates are much tighter for children
        w = 0.6 if age < 10 else 0.45 if age < 20 else 0.3
        out.append(Evidence("age", year, None, year - u, year + u, w,
                            f"{person.name} looks about {round(age)} (born {person.birth_year})"))
    return out


def file_name_evidence(p: Photo) -> list[Evidence]:
    if p.name_year_min is None:
        return []
    lo, hi = p.name_year_min, p.name_year_max
    return [Evidence("file_name", (lo + hi) // 2, None, lo, hi, FILENAME,
                     f"file name says {lo}" + (f"-{hi}" if hi != lo else ""))]


# --- combining -----------------------------------------------------------------------


def combine(evidence: list[Evidence]) -> Optional[Estimate]:
    """Weighted median year; confidence grows when independent evidence agrees."""
    if not evidence:
        return None
    ev = sorted(evidence, key=lambda e: e.year)
    total = sum(e.weight for e in ev)
    acc = 0.0
    year = ev[-1].year
    for e in ev:
        acc += e.weight
        if acc >= total / 2:
            year = e.year
            break
    agree = [e for e in ev if e.lo - 1 <= year <= e.hi + 1]
    disagree = [e for e in ev if e not in agree]
    agree_w = sum(e.weight for e in agree)
    top = max(agree, key=lambda e: e.weight)
    conf = top.weight * (0.5 + 0.5 * agree_w / total)
    if len(agree) > 1:  # independent sources pointing the same way
        conf = min(0.95, conf + 0.05 * (len(agree) - 1))
    lo, hi = max(e.lo for e in agree), min(e.hi for e in agree)
    if lo > hi:
        lo = hi = year
    lo, hi = min(lo, year), max(hi, year)
    month_src = max((e for e in agree if e.month and e.year == year), key=lambda e: e.weight, default=None)
    month = month_src.month if month_src else None
    precision = "month" if month else "year" if lo == hi else "range"
    parts = [e.text for e in sorted(agree, key=lambda e: -e.weight)[:3]]
    if disagree:
        parts.append("but " + "; ".join(e.text for e in sorted(disagree, key=lambda e: -e.weight)[:2]))
    return Estimate(year, month, lo, hi, precision, round(conf, 2), "; ".join(parts), top.kind, ev)


# --- loading ------------------------------------------------------------------------


@dataclass
class World:
    photos: list[Photo]
    loose_by_album: dict[int, list[LooseBack]]
    anchors_by_album: dict[int, list[Anchor]]
    faces_by_photo: dict[int, list[Face]]
    people: dict[int, Person]


def build_anchors(photos: list[Photo], loose: list[LooseBack], exclude: set[int] = frozenset()) -> dict[int, list[Anchor]]:
    """Trusted dates by album position: entered/accepted dates and back dates a person typed."""
    out: dict[int, list[Anchor]] = {}
    for p in photos:
        if p.id in exclude:
            continue
        if p.t_year is not None:
            label = _label(p.t_year, p.t_month) if p.t_precision in ("day", "month", "year") else (
                f"{p.t_min}-{p.t_max}" if p.t_min != p.t_max else str(p.t_year))
            out.setdefault(p.album_id, []).append(
                Anchor(p.list_index, p.t_year, p.t_min or p.t_year, p.t_max or p.t_year, p.id, label))
        elif p.back_year:
            manual = p.back_source == "manual"
            label = _label(p.back_year, p.back_month) + ("" if manual else f" ({p.back_source})")
            out.setdefault(p.album_id, []).append(
                Anchor(p.list_index, p.back_year, p.back_year, p.back_year, p.id, label,
                       1.0 if manual else OCR_ANCHOR_TRUST))
    for lb in loose:
        if lb.d_year:
            manual = lb.d_source == "manual"
            label = _label(lb.d_year, lb.d_month) + ("" if manual else f" ({lb.d_source})")
            out.setdefault(lb.album_id, []).append(
                Anchor(lb.list_index, lb.d_year, lb.d_year, lb.d_year, None, label,
                       1.0 if manual else OCR_ANCHOR_TRUST, lb.id))
    for anchors in out.values():
        anchors.sort(key=lambda a: a.index)
    return out


def load_world(s, album_id: Optional[int] = None, exclude: set[int] = frozenset()) -> World:
    pq = select(Photo).where(Photo.missing == False)  # noqa: E712
    lq = select(LooseBack).where(LooseBack.missing == False)  # noqa: E712
    if album_id is not None:
        pq, lq = pq.where(Photo.album_id == album_id), lq.where(LooseBack.album_id == album_id)
    photos = list(s.exec(pq).all())
    loose = list(s.exec(lq).all())
    loose_by_album: dict[int, list[LooseBack]] = {}
    for lb in loose:
        loose_by_album.setdefault(lb.album_id, []).append(lb)
    ids = {p.id for p in photos}
    faces_by_photo: dict[int, list[Face]] = {}
    for f in s.exec(select(Face).where(Face.person_id != None)).all():  # noqa: E711
        if f.photo_id in ids:
            faces_by_photo.setdefault(f.photo_id, []).append(f)
    people = {p.id: p for p in s.exec(select(Person)).all()}
    return World(photos, loose_by_album, build_anchors(photos, loose, exclude), faces_by_photo, people)


def estimate_for(p: Photo, world: World, window: int) -> Optional[Estimate]:
    evidence = (
        own_back_evidence(p)
        + loose_back_evidence(p, world.loose_by_album.get(p.album_id, []), window)
        + neighbour_evidence(p, world.anchors_by_album.get(p.album_id, []), window)
        + age_evidence(world.faces_by_photo.get(p.id, []), world.people)
        + file_name_evidence(p)
    )
    return combine(evidence)


def compute_estimates(world: World) -> dict[int, Optional[Estimate]]:
    window = get_settings().loose_back_window
    return {p.id: estimate_for(p, world, window) for p in world.photos}


def run_estimates(ctx: JobContext = NULL_CTX, album_id: Optional[int] = None) -> dict:
    run_id = f"est-{uuid.uuid4().hex[:8]}"
    counts = {"own_back": 0, "loose_back": 0, "neighbours": 0, "age": 0, "file_name": 0, "cleared": 0}
    with session_scope() as s:
        world = load_world(s, album_id)
        ctx.progress(0, len(world.photos), "estimating dates")
        for p in world.photos:
            est = estimate_for(p, world, get_settings().loose_back_window)
            if est is None:
                if p.i_year is not None and (p.i_run_id or "").startswith("est-"):
                    p.i_year = p.i_month = p.i_min = p.i_max = p.i_precision = None
                    p.i_confidence = p.i_source = p.i_run_id = None
                    counts["cleared"] += 1
                    s.add(p)
                continue
            if p.t_year is None:
                counts[est.kind] += 1
            new = (est.year, est.month, est.lo, est.hi, est.precision, est.confidence, est.source)
            if (p.i_year, p.i_month, p.i_min, p.i_max, p.i_precision, p.i_confidence, p.i_source) != new:
                p.i_year, p.i_month, p.i_min, p.i_max, p.i_precision, p.i_confidence, p.i_source = new
                p.i_run_id = run_id
                s.add(p)
    counts["estimated"] = sum(counts[k] for k in ("own_back", "loose_back", "neighbours", "age", "file_name"))
    counts["run_id"] = run_id
    return counts
