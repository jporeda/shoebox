"""Partial-date parsing and the export date rules."""

from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass
from typing import Optional

MIN_YEAR, MAX_YEAR = 1850, 2100


class DateParseError(ValueError):
    pass


@dataclass
class PartialDate:
    year: int
    month: Optional[int] = None
    day: Optional[int] = None
    min: Optional[int] = None
    max: Optional[int] = None
    precision: str = "year"  # day | month | year | range | decade

    def as_trusted(self, source: str) -> dict:
        d = asdict(self)
        return {
            "t_year": d["year"],
            "t_month": d["month"],
            "t_day": d["day"],
            "t_min": d["min"],
            "t_max": d["max"],
            "t_precision": d["precision"],
            "t_source": source,
        }


def _check_year(y: int) -> int:
    if not MIN_YEAR <= y <= MAX_YEAR:
        raise DateParseError(f"year {y} out of range {MIN_YEAR}-{MAX_YEAR}")
    return y


def parse_partial_date(text: str) -> PartialDate:
    """Parse what a person types into the year box.

    Accepts ``1987``, ``1987-06``, ``1987-06-14``, ``1980s``, ``1985-1988``
    (range), and ``~1987`` / ``c1987`` / ``ca. 1987`` (circa, +/-2 years).
    """
    t = text.strip().lower()
    if not t:
        raise DateParseError("empty date")

    if m := re.fullmatch(r"(?:~|c\.?|ca\.?|circa)\s*(\d{4})", t):
        y = _check_year(int(m[1]))
        return PartialDate(year=y, min=y - 2, max=y + 2, precision="range")
    if m := re.fullmatch(r"(\d{3})0'?s", t):
        y = _check_year(int(m[1]) * 10)
        return PartialDate(year=y, min=y, max=y + 9, precision="decade")
    if m := re.fullmatch(r"(\d{4})\s*(?:-|–|to)\s*(\d{4})", t):
        a, b = sorted((_check_year(int(m[1])), _check_year(int(m[2]))))
        if a == b:
            return PartialDate(year=a, min=a, max=a)
        return PartialDate(year=(a + b) // 2, min=a, max=b, precision="range")
    if m := re.fullmatch(r"(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?", t):
        y = _check_year(int(m[1]))
        if m[2] is None:
            return PartialDate(year=y, min=y, max=y)
        mo = int(m[2])
        if not 1 <= mo <= 12:
            raise DateParseError(f"month {mo} out of range")
        if m[3] is None:
            return PartialDate(year=y, month=mo, min=y, max=y, precision="month")
        d = int(m[3])
        if not 1 <= d <= calendar.monthrange(y, mo)[1]:
            raise DateParseError(f"day {d} out of range")
        return PartialDate(year=y, month=mo, day=d, min=y, max=y, precision="day")
    raise DateParseError(f"can't read date {text!r}")


def format_partial(year, month=None, day=None, precision=None, lo=None, hi=None) -> str:
    """Human/round-trippable string for a stored date."""
    if year is None:
        return ""
    if precision == "decade":
        return f"{year}s"
    if precision == "range" and lo is not None and hi is not None:
        return f"{lo}-{hi}"
    if precision == "day" and month and day:
        return f"{year:04d}-{month:02d}-{day:02d}"
    if precision == "month" and month:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}"


def export_calendar_date(year: int, month: int | None, day: int | None, precision: str | None) -> tuple[int, int, int]:
    """Calendar date written to DateTimeOriginal.

    day -> as given; month -> 1st of month; year/range -> Jul 1; decade -> Jan 1.
    """
    if precision == "day" and month and day:
        return year, month, day
    if precision == "month" and month:
        return year, month, 1
    if precision == "decade":
        return year, 1, 1
    return year, 7, 1


def xmp_partial(year: int, month: int | None, day: int | None, precision: str | None) -> str:
    """Value for XMP-photoshop:DateCreated, keeping only the known precision.

    Colon-separated because that's what exiftool accepts on input; it stores the
    ISO form ("1987-06") in the XMP packet.
    """
    if precision == "day" and month and day:
        return f"{year:04d}:{month:02d}:{day:02d}"
    if precision == "month" and month:
        return f"{year:04d}:{month:02d}"
    return f"{year:04d}"
