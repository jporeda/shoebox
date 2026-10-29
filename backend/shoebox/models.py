"""Database tables.

Dates on a photo are split into a *trusted* set (``t_*``: entered or accepted by a
person, or read from a back/event) and an *inferred* set (``i_*``: written only by
inference runs). Inference never writes ``t_*``; the effective date is trusted,
falling back to inferred.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column, LargeBinary, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Album(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    rel_path: str = Field(index=True, unique=True)
    name: str
    sort_order: int = 0
    missing: bool = False


class Photo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sha256: str = Field(index=True, unique=True)
    image_data_hash: Optional[str] = Field(default=None, index=True)
    rel_path: str = Field(index=True)
    album_id: int = Field(foreign_key="album.id", index=True)
    album_index: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    file_size: int = 0
    file_mtime: float = 0.0
    exif_orientation: int = 1
    scan_datetime: Optional[str] = None  # date already in the file (usually the scan date)

    # Position among *all* image files in the album folder (fronts and backs, by name),
    # used to relate loose backs to nearby fronts.
    list_index: int = 0
    # Years encoded in the file name, e.g. Album3_1984_1986_0161 -> 1984..1986.
    name_year_min: Optional[int] = None
    name_year_max: Optional[int] = None

    back_rel_path: Optional[str] = None
    back_sha256: Optional[str] = Field(default=None, index=True)
    # Date read from the paired back (printed lab stamp or entered by a person).
    back_year: Optional[int] = None
    back_month: Optional[int] = None
    back_source: Optional[str] = None  # ocr | manual | claude
    back_confidence: Optional[float] = None
    back_candidates: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    back_ocr_ver: int = 0
    back_claude_ver: int = 0

    # Clockwise degrees applied after the file's own EXIF orientation.
    rotation: int = 0
    rotation_source: str = "none"  # none | auto | manual

    phash: Optional[str] = None
    dup_group_id: Optional[int] = Field(default=None, index=True)
    is_dup_keeper: bool = False

    # Trusted date.
    t_year: Optional[int] = Field(default=None, index=True)
    t_month: Optional[int] = None
    t_day: Optional[int] = None
    t_min: Optional[int] = None
    t_max: Optional[int] = None
    t_precision: Optional[str] = None  # day | month | year | range | decade
    t_source: Optional[str] = None  # manual | accepted | back | imprint | event | restore

    # Inferred date (written by inference runs only).
    i_year: Optional[int] = Field(default=None, index=True)
    i_month: Optional[int] = None
    i_min: Optional[int] = None
    i_max: Optional[int] = None
    i_precision: Optional[str] = None
    i_confidence: Optional[float] = None
    i_source: Optional[str] = None  # human-readable evidence for the estimate
    i_run_id: Optional[str] = None

    event_instance_id: Optional[int] = Field(default=None, foreign_key="eventinstance.id", index=True)
    caption: Optional[str] = None

    missing: bool = False
    fixity_checked_at: Optional[datetime] = None
    fixity_ok: Optional[bool] = None

    # Pipeline stage markers: a stage reruns when its version is below the current one.
    thumb_ver: int = 0
    orient_ver: int = 0
    faces_model_ver: int = 0
    vision_ver: int = 0

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class LooseBack(SQLModel, table=True):
    """A back scan with no front of the same name. Its date informs nearby fronts."""

    id: Optional[int] = Field(default=None, primary_key=True)
    album_id: int = Field(foreign_key="album.id", index=True)
    rel_path: str = Field(index=True, unique=True)
    sha256: str = Field(index=True)
    list_index: int = 0
    file_size: int = 0
    file_mtime: float = 0.0
    missing: bool = False
    d_year: Optional[int] = None
    d_month: Optional[int] = None
    d_source: Optional[str] = None  # ocr | manual | claude
    d_confidence: Optional[float] = None
    candidates: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    ocr_ver: int = 0
    transcription: Optional[str] = None
    claude_raw: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    claude_ver: int = 0


class BackText(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    photo_id: int = Field(foreign_key="photo.id", index=True, unique=True)
    transcription: Optional[str] = None
    dates: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    raw: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class Person(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    birth_year: Optional[int] = None
    notes: Optional[str] = None


class Face(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    photo_id: int = Field(foreign_key="photo.id", index=True)
    # Normalized box (0..1) in the frame given by frame_rotation.
    x: float
    y: float
    w: float
    h: float
    frame_rotation: int = 0
    embedding: Optional[bytes] = Field(default=None, sa_column=Column(LargeBinary))
    det_score: Optional[float] = None
    detector_ver: Optional[str] = None
    person_id: Optional[int] = Field(default=None, foreign_key="person.id", index=True)
    source: str = "auto"  # auto | manual | drawn | accepted
    ignored: bool = False
    confidence: Optional[float] = None
    est_age: Optional[float] = None
    run_id: Optional[str] = None
    cluster_id: Optional[int] = Field(default=None, index=True)
    # Name suggested by matching but not applied (too unsure, or the face looks under 6).
    suggested_person_id: Optional[int] = Field(default=None, foreign_key="person.id", index=True)
    # People a person said this face is *not*; never suggested for it again.
    rejected_person_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))


class EventKind(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    month: Optional[int] = None
    day: Optional[int] = None
    builtin: bool = False


class EventInstance(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("kind_id", "label"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    kind_id: int = Field(foreign_key="eventkind.id", index=True)
    label: str
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None


class Suggestion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    photo_id: Optional[int] = Field(default=None, foreign_key="photo.id", index=True)
    face_id: Optional[int] = Field(default=None, foreign_key="face.id", index=True)
    field: str
    value: Any = Field(default=None, sa_column=Column(JSON))
    confidence: float = 0.0
    source: str = ""
    state: str = "pending"  # pending | accepted | rejected
    run_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class EditBatch(SQLModel, table=True):
    id: str = Field(primary_key=True)
    ts: datetime = Field(default_factory=utcnow)
    description: str = ""
    actor: str = "user"
    undone: bool = False


class Edit(SQLModel, table=True):
    """Append-only change log; one row per field change."""

    id: Optional[int] = Field(default=None, primary_key=True)
    batch_id: str = Field(foreign_key="editbatch.id", index=True)
    entity: str
    entity_id: int
    field: str
    old: Any = Field(default=None, sa_column=Column(JSON))
    new: Any = Field(default=None, sa_column=Column(JSON))


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    state: str = Field(default="queued", index=True)  # queued | running | done | failed | cancelled
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    progress: int = 0
    total: int = 0
    message: str = ""
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error: Optional[str] = None
    attempts: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None


class ClaudeBatch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    batch_id: Optional[str] = Field(default=None, index=True)
    job_id: Optional[int] = Field(default=None, foreign_key="job.id")
    custom_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    state: str = "pending"  # pending | submitted | ended | processed
    created_at: datetime = Field(default_factory=utcnow)


class Meta(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: Any = Field(default=None, sa_column=Column(JSON))
