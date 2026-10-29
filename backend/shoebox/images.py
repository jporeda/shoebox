"""Image decoding, thumbnails, and hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image, ImageOps

from .config import get_settings

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pass

Image.MAX_IMAGE_PIXELS = 400_000_000  # large flatbed scans are legitimate

THUMB_VERSION = 1


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def _to_8bit(img: Image.Image) -> Image.Image:
    """Normalize 16-bit / odd modes to 8-bit RGB or L."""
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):
        arr = np.asarray(img, dtype=np.uint32)
        peak = max(int(arr.max()), 1)
        shift = max(peak.bit_length() - 8, 0)
        return Image.fromarray((arr >> shift).clip(0, 255).astype(np.uint8), "L")
    if img.mode == "F":
        arr = np.asarray(img)
        arr = (arr - arr.min()) / max(float(np.ptp(arr)), 1e-9) * 255
        return Image.fromarray(arr.astype(np.uint8), "L")
    if img.mode in ("RGBA", "LA", "P", "CMYK", "YCbCr", "1"):
        return img.convert("RGB")
    return img


def open_display(path: Path, rotation: int = 0, max_size: int | None = None) -> Image.Image:
    """Open the first frame, apply EXIF orientation then our clockwise rotation."""
    with Image.open(path) as img:
        img.seek(0)
        if max_size:
            img.draft("RGB", (max_size, max_size))  # fast JPEG downscale
        img = ImageOps.exif_transpose(img)
        img = _to_8bit(img)
        if max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        else:
            img.load()
    if rotation % 360:
        img = img.rotate(-rotation, expand=True)  # PIL rotates counter-clockwise
    return img


def thumb_path(sha: str, size: int, rotation: int) -> Path:
    return get_settings().thumbs_dir / sha[:2] / f"{sha}_{size}_{rotation % 360}.jpg"


def ensure_thumb(src: Path, sha: str, size: int, rotation: int = 0) -> Path:
    out = thumb_path(sha, size, rotation)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    img = open_display(src, rotation, max_size=size)
    if img.mode != "RGB":
        img = img.convert("RGB")
    tmp = out.with_suffix(".tmp")
    img.save(tmp, "JPEG", quality=85, optimize=True)
    tmp.replace(out)
    return out


def phash(img: Image.Image) -> str:
    return str(imagehash.phash(img))


def hamming_groups(hashes: dict[int, str], max_distance: int) -> list[list[int]]:
    """Group ids whose 64-bit pHashes are within max_distance (union-find)."""
    ids = list(hashes)
    if len(ids) < 2:
        return []
    vals = np.array([int(hashes[i], 16) for i in ids], dtype=np.uint64)
    parent = list(range(len(ids)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    chunk = 1024
    for start in range(0, len(ids), chunk):
        block = vals[start : start + chunk]
        x = block[:, None] ^ vals[None, :]
        dist = np.unpackbits(x.view(np.uint8).reshape(*x.shape, 8), axis=-1).sum(-1)
        rows, cols = np.nonzero(dist <= max_distance)
        for r, c in zip(rows + start, cols):
            if r < c:
                ra, rb = find(r), find(c)
                if ra != rb:
                    parent[rb] = ra
    groups: dict[int, list[int]] = {}
    for i, pid in enumerate(ids):
        groups.setdefault(find(i), []).append(pid)
    return [sorted(g) for g in groups.values() if len(g) > 1]
