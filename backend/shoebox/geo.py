"""Map coordinates for a photo's place, for exports (Apple Photos puts photos on its map only from GPS).

A city is placed at its centre, from an offline list of the world's towns: GeoNames' cities500
(CC-BY 4.0, geonames.org), downloaded into the Docker image at build time. Nothing is looked up
online, so place names never leave this machine. "Disney" anywhere in the place is Cinderella
Castle at Walt Disney World.

A city that can't be pinned down gets no coordinates: a misspelt or unknown name, a country or
state that doesn't match, or several towns sharing the name with no state given.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .config import get_settings

log = logging.getLogger(__name__)

# Cinderella Castle, Magic Kingdom, Walt Disney World, Florida.
DISNEY = (28.419411, -81.581200)

CITIES = "cities500.txt"
ADMIN1 = "admin1CodesASCII.txt"
COUNTRIES = "countryInfo.txt"

# Populated places that aren't a town to put a photo in: a section of a city (a neighbourhood),
# historical, abandoned or destroyed places.
SKIP_FEATURES = {"PPLX", "PPLH", "PPLQ", "PPLW"}

# Common ways of writing a country that aren't its GeoNames name or ISO code.
COUNTRY_ALIASES = {
    "usa": "US", "u.s.a.": "US", "u.s.": "US", "united states of america": "US", "america": "US",
    "uk": "GB", "u.k.": "GB", "great britain": "GB", "britain": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "northern ireland": "GB",
}


@dataclass(frozen=True)
class Town:
    lat: float
    lon: float
    country: str  # ISO 3166 alpha-2
    admin1: str  # GeoNames first-level code within the country (US states: the postal code, "MA")
    population: int


@dataclass
class World:
    towns: dict[str, list[Town]]  # by normalized name
    countries: dict[str, str]  # normalized name or code -> ISO alpha-2
    admin1: dict[tuple[str, str], str]  # (ISO alpha-2, normalized name or code) -> admin1 code


def norm(text: str) -> str:
    """Case, accents and spacing don't matter: "Montréal " matches "montreal"."""
    s = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.casefold().split())


def _lines(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip() and not line.startswith("#"):
                yield line.rstrip("\n").split("\t")


@lru_cache(maxsize=4)
def _load(folder: str) -> Optional[World]:
    root = Path(folder)
    if not (root / CITIES).is_file():
        log.warning("no town list at %s: exports get no map coordinates for cities", root)
        return None
    towns: dict[str, list[Town]] = {}
    for row in _lines(root / CITIES):
        if len(row) < 15 or row[7] in SKIP_FEATURES:
            continue
        town = Town(float(row[4]), float(row[5]), row[8], row[10], int(row[14] or 0))
        for name in {norm(row[1]), norm(row[2])}:
            towns.setdefault(name, []).append(town)
    countries: dict[str, str] = dict(COUNTRY_ALIASES)
    if (root / COUNTRIES).is_file():
        for row in _lines(root / COUNTRIES):
            if len(row) > 4:
                for name in (row[0], row[1], row[4]):  # ISO2, ISO3, name
                    countries[norm(name)] = row[0]
    admin1: dict[tuple[str, str], str] = {}
    if (root / ADMIN1).is_file():
        for row in _lines(root / ADMIN1):
            if len(row) > 2 and "." in row[0]:
                cc, code = row[0].split(".", 1)
                for name in (code, row[1], row[2]):
                    admin1[(cc, norm(name))] = code
    return World(towns, countries, admin1)


def world() -> Optional[World]:
    return _load(str(get_settings().geonames_dir))


def locate(country: Optional[str], state: Optional[str], city: Optional[str]) -> Optional[tuple[float, float]]:
    """(latitude, longitude) for a photo's place, or None when it can't be pinned down."""
    if any("disney" in (v or "").casefold() for v in (country, state, city)):
        return DISNEY
    if not (city or "").strip():
        return None  # a country or state alone isn't put on the map
    w = world()
    if w is None:
        return None
    found = w.towns.get(norm(city), [])
    if (country or "").strip():
        iso = w.countries.get(norm(country))
        found = [t for t in found if t.country == iso] if iso else []
    if (state or "").strip():
        found = [t for t in found if w.admin1.get((t.country, norm(state))) == t.admin1]
    if not found:
        return None
    if len(found) > 1 and not (state or "").strip():
        return None  # several towns with this name, and no state to tell them apart
    best = max(found, key=lambda t: t.population)  # a town and a same-named village in one state
    return best.lat, best.lon
