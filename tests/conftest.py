"""Test fixtures. All images are synthetic (no real faces in the repo)."""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from shoebox import config, db


def make_image(path: Path, seed: int, size=(320, 240), fmt: str | None = None, **save) -> Path:
    """Blocky random pattern: distinct per seed, stable under re-encoding (for pHash)."""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 255, (6, 8, 3), dtype=np.uint8)
    img = Image.fromarray(blocks).resize(size, Image.Resampling.NEAREST)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, fmt, **save)
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated photo root, data dir and backup dir with a migrated DB."""
    photos, data, backup = tmp_path / "photos", tmp_path / "data", tmp_path / "backup"
    photos.mkdir()
    monkeypatch.setenv("SHOEBOX_PHOTOS_ROOT", str(photos))
    monkeypatch.setenv("SHOEBOX_DATA_DIR", str(data))
    monkeypatch.setenv("SHOEBOX_BACKUP_DIR", str(backup))
    monkeypatch.setenv("SHOEBOX_CONFIG", str(tmp_path / "none.toml"))
    reset_caches()
    db.migrate()
    yield config.get_settings()
    reset_caches()


def reset_caches() -> None:
    config.get_settings.cache_clear()
    try:
        db.get_engine().dispose()
    except Exception:
        pass
    db.get_engine.cache_clear()


@pytest.fixture
def album_tree(env):
    """Two albums with backs, an orphan back and a back-like front."""
    root = env.photos_root
    make_image(root / "Album 1980s" / "IMG_0001.jpg", 1)
    make_image(root / "Album 1980s" / "IMG_0001_back.jpg", 101)
    make_image(root / "Album 1980s" / "IMG_0002.jpg", 2)
    make_image(root / "Album 1980s" / "IMG_0010.jpg", 10)
    make_image(root / "Album 1980s" / "IMG_0099_back.jpg", 199)  # orphan back
    make_image(root / "Album 1990s" / "scan 1.jpg", 3)
    make_image(root / "Album 1990s" / "scan 1 reverse.jpg", 103)
    make_image(root / "Album 1990s" / "scan 2.jpg", 4)
    make_image(root / "Album 1990s" / "backyard.jpg", 5)  # back-like name, really a front
    return root


random.seed(0)
