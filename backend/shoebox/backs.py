"""Read printed lab dates off back scans with local OCR (tesseract).

Lab backprints come in a few shapes: "FOTO DATE: DEC., 1975", "OCT., 1978",
Kodak paper codes like "FEB 86 K", stamps like "JULY 91", and dot-matrix
"07/06/02". OCR on 2-digit years is noisy, so a reading is only *accepted*
(used to estimate dates) when it has a 4-digit year or several OCR passes agree,
and it falls inside the plausible range. Everything else is kept as a candidate
for a person to confirm on the loose-backs page.
"""

from __future__ import annotations

import collections
import difflib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage
from sqlmodel import select

from .config import get_settings
from .db import session_scope
from .jobs_ctx import NULL_CTX, JobContext
from .models import LooseBack, Photo

log = logging.getLogger(__name__)

OCR_VERSION = 2  # 2: per-line crops in 8 orientations (mirrored stamps), dot-matrix digits
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
_MON_YEAR = re.compile(r"\b([A-Z0-9]{3,5})[A-Z]*[.,:;]*\s{0,3}['’`]?\s?([0-9OIlS]{4}|[0-9OIlS]{2})\b")
_NUMERIC = re.compile(r"\b(\d{2})/(\d{2})/(\d{2})\b")
# Dot-matrix lab lines like "APR/15/02 15:28" or "AUG/21/01" (month/day/2-digit year).
_MON_DAY_YEAR = re.compile(r"\b([A-Z0-9]{3})\s?/\s?(\d{1,2})\s?/\s?(\d{2})\b")
ACCEPT_CONFIDENCE = 0.6


@dataclass(frozen=True)
class Hit:
    month: int | None
    year: int
    four_digit: bool
    exact_month: bool


def _digits(s: str) -> str:
    return s.replace("O", "0").replace("I", "1").replace("l", "1").replace("S", "5")


def _year(s: str) -> int | None:
    s = _digits(s)
    if not s.isdigit():
        return None
    y = int(s)
    if len(s) == 2:
        y += 1900 if y >= 40 else 2000
    return y


def parse_dates(text: str) -> list[Hit]:
    """Find month/year dates in OCR text, tolerating common misreads (DEG -> DEC)."""
    out: list[Hit] = []
    up = text.upper()
    for tok, dd, yy in _MON_DAY_YEAR.findall(up):
        match = difflib.get_close_matches(tok.replace("0", "O"), MONTHS, n=1, cutoff=0.66)
        if match and 1 <= int(dd) <= 31 and (y := _year(yy)):
            out.append(Hit(MONTHS.index(match[0]) + 1, y, False, match[0] == tok))
    up = _MON_DAY_YEAR.sub(" ", up)  # so "APR/15/02" isn't also read as April 2015
    for tok, yr in _MON_YEAR.findall(up):
        tok3 = tok.replace("0", "O")[:3]
        match = difflib.get_close_matches(tok3, MONTHS, n=1, cutoff=0.66)
        y = _year(yr)
        if match and y:
            out.append(Hit(MONTHS.index(match[0]) + 1, y, len(yr) == 4, match[0] == tok3))
    for mm, dd, yy in _NUMERIC.findall(up):
        if 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31 and (y := _year(yy)):
            out.append(Hit(int(mm), y, False, True))
    return out


def _variants(img: Image.Image) -> Iterable[Image.Image]:
    # Coloured inks (green, magenta, cyan) become dark when taking the min channel.
    gray = ImageOps.autocontrast(Image.fromarray(np.asarray(img.convert("RGB")).min(axis=2)), cutoff=1)
    w, h = gray.size
    if max(w, h) < 2000:
        gray = gray.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
    yield gray
    arr = np.asarray(gray)
    yield Image.fromarray(np.where(arr < np.percentile(arr, 6), 0, 255).astype(np.uint8))


def _ink(img: Image.Image) -> np.ndarray:
    """Per-pixel ink strength: darker than the paper, or more colourful than it."""
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    mn, mx = a.min(axis=2), a.max(axis=2)
    dark = np.clip(np.percentile(mn, 60) - mn, 0, 255)  # dot-matrix and any dark ink
    sat = np.clip((mx - mn) - np.percentile(mx - mn, 60), 0, 255)  # pale cyan / magenta / green stamps
    return np.maximum(dark, sat).astype(np.float32)


def _line_crops(img: Image.Image, max_regions: int = 14) -> list[Image.Image]:
    """Crop the strongest single lines of ink (horizontal or vertical), cleaned for OCR."""
    ink = _ink(img)
    mask = ink > max(12.0, np.percentile(ink, 99) * 0.35)
    h, w = mask.shape
    k = max(3, w // 120)
    slices = []
    for struct in (np.ones((max(1, k // 2), k * 4)), np.ones((k * 4, max(1, k // 2)))):
        labels, _ = ndimage.label(ndimage.binary_dilation(mask, structure=struct))
        slices += ndimage.find_objects(labels)
    scored = []
    for ys, xs in slices:
        bh, bw = ys.stop - ys.start, xs.stop - xs.start
        if bh * bw >= 150 and bh <= h * 0.6 and bw <= w * 0.9:
            scored.append((float(ink[ys, xs].sum()), ys, xs))
    scored.sort(key=lambda t: -t[0])
    out = []
    for _, ys, xs in scored[:max_regions]:
        y0, y1 = max(0, ys.start - 6), min(h, ys.stop + 6)
        x0, x1 = max(0, xs.start - 6), min(w, xs.stop + 6)
        c = ndimage.grey_dilation(ink[y0:y1, x0:x1], size=(2, 2))  # join dot-matrix dots
        c = 255 - np.clip(c / max(float(c.max()), 1.0) * 255 * 1.4, 0, 255)
        g = Image.fromarray(c.astype(np.uint8))
        scale = min(6.0, max(1.0, 64 / max(1, min(g.size))))
        out.append(g.resize((int(g.width * scale), int(g.height * scale)), Image.Resampling.LANCZOS))
    return out


def _orientations(g: Image.Image) -> Iterable[tuple[int, Image.Image]]:
    """All 8 orientations: lab stamps show up rotated, upside down, and mirrored."""
    for r in (0, 90, 180, 270):
        x = g.rotate(r, expand=True)
        yield r, x
        yield r, ImageOps.mirror(x)


def ocr_texts(path: Path) -> Iterable[str]:
    import pytesseract

    with Image.open(path) as raw:
        img = ImageOps.exif_transpose(raw)
        img.load()
    for crop in _line_crops(img):
        for r, o in _orientations(crop):
            if o.height > o.width * 1.6:  # tesseract reads horizontal lines
                continue
            yield pytesseract.image_to_string(o, config="--psm 7")
            if r in (0, 180):  # dot-matrix lines: digits and slashes only
                yield pytesseract.image_to_string(o, config="--psm 7 -c tessedit_char_whitelist=0123456789/")
    # Whole-image pass catches text the line finder misses (e.g. on striped postcard stock).
    for variant in _variants(img):
        for r in (0, 180, 90, 270):
            yield pytesseract.image_to_string(variant.rotate(r, expand=True), config="--psm 11")


def read_back(path: Path, texts: Callable[[Path], Iterable[str]] = ocr_texts) -> dict:
    """OCR a back. Returns {"candidates": [...], "accepted": {...} | None}."""
    settings = get_settings()
    votes: collections.Counter[Hit] = collections.Counter()
    for txt in texts(path):
        for hit in parse_dates(txt):
            if settings.ocr_min_year <= hit.year <= settings.ocr_max_year:
                votes[hit] += 1
    by_date: collections.Counter[tuple[int | None, int]] = collections.Counter()
    strong: dict[tuple[int | None, int], bool] = {}
    for hit, n in votes.items():
        key = (hit.month, hit.year)
        by_date[key] += n
        strong[key] = strong.get(key, False) or hit.four_digit
    candidates = [
        {"month": m, "year": y, "votes": n, "four_digit": strong[(m, y)]} for (m, y), n in by_date.most_common(5)
    ]
    accepted = None
    if candidates:
        top = candidates[0]
        runner_up = candidates[1]["votes"] if len(candidates) > 1 else 0
        if (top["four_digit"] or top["votes"] >= 2) and top["votes"] > runner_up:
            conf = min(0.9, 0.5 + 0.1 * top["votes"] + (0.2 if top["four_digit"] else 0))
            accepted = {"year": top["year"], "month": top["month"], "confidence": round(conf, 2)}
    return {"candidates": candidates, "accepted": accepted}


def _consistent(accepted: dict | None, lo: int | None, hi: int | None) -> dict | None:
    """Reject a reading that contradicts the years in the file name (beyond 2 years slack)."""
    if accepted and lo is not None and not (lo - 2 <= accepted["year"] <= hi + 2):
        return None
    return accepted


def run_read_backs(ctx: JobContext = NULL_CTX, reader: Callable[[Path], dict] = read_back) -> dict:
    root = get_settings().photos_root
    with session_scope() as s:
        loose = s.exec(
            select(LooseBack.id).where(LooseBack.missing == False, LooseBack.ocr_ver < OCR_VERSION)  # noqa: E712
        ).all()
        paired = s.exec(
            select(Photo.id).where(
                Photo.missing == False, Photo.back_sha256 != None, Photo.back_ocr_ver < OCR_VERSION  # noqa: E711,E712
            )
        ).all()
    total = len(loose) + len(paired)
    accepted = done = 0
    for lid in loose:
        ctx.check_cancel()
        if done % 5 == 0:
            ctx.progress(done, total, "reading loose backs")
        with session_scope() as s:
            lb = s.get(LooseBack, lid)
            if lb.d_year is not None or lb.d_source == "manual":
                lb.ocr_ver = OCR_VERSION
                s.add(lb)
                done += 1
                continue
            try:
                result = reader(root / lb.rel_path)
            except Exception as exc:
                log.warning("OCR failed for %s: %s", lb.rel_path, exc)
                result = {"candidates": [], "accepted": None}
            lb.candidates = result["candidates"]
            fronts = s.exec(
                select(Photo.name_year_min, Photo.name_year_max).where(
                    Photo.album_id == lb.album_id, Photo.name_year_min != None  # noqa: E711
                )
            ).first()
            acc = _consistent(result["accepted"], *(fronts or (None, None)))
            if lb.d_source != "manual":
                lb.d_year, lb.d_month = (acc["year"], acc["month"]) if acc else (None, None)
                lb.d_source, lb.d_confidence = ("ocr", acc["confidence"]) if acc else (None, None)
            accepted += acc is not None
            lb.ocr_ver = OCR_VERSION
            s.add(lb)
        done += 1
    for pid in paired:
        ctx.check_cancel()
        if done % 5 == 0:
            ctx.progress(done, total, "reading paired backs")
        with session_scope() as s:
            p = s.get(Photo, pid)
            if p.back_year is not None or p.back_source == "manual":
                p.back_ocr_ver = OCR_VERSION
                s.add(p)
                done += 1
                continue
            try:
                result = reader(root / p.back_rel_path)
            except Exception as exc:
                log.warning("OCR failed for %s: %s", p.back_rel_path, exc)
                result = {"candidates": [], "accepted": None}
            p.back_candidates = result["candidates"]
            acc = _consistent(result["accepted"], p.name_year_min, p.name_year_max)
            if p.back_source != "manual":
                p.back_year, p.back_month = (acc["year"], acc["month"]) if acc else (None, None)
                p.back_source, p.back_confidence = ("ocr", acc["confidence"]) if acc else (None, None)
            accepted += acc is not None
            p.back_ocr_ver = OCR_VERSION
            s.add(p)
        done += 1
    return {"loose_backs": len(loose), "paired_backs": len(paired), "dates_accepted": accepted}
