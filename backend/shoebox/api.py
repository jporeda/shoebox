"""HTTP API used by the web UI."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlmodel import Session, col, select

from . import editlog, jobs, services
from .config import get_settings
from .dates import DateParseError, format_partial, parse_partial_date
from .db import get_session
from .images import ensure_thumb
from .models import Album, BackText, EditBatch, EventInstance, EventKind, Job, LooseBack, Photo

router = APIRouter(prefix="/api")

Status = Literal["untagged", "inferred", "reviewed"]


# --- schemas ---------------------------------------------------------------------


class AlbumOut(BaseModel):
    id: int
    name: str
    rel_path: str
    sort_order: int
    count: int
    dated: int
    missing: bool
    loose_backs: int
    loose_backs_undated: int
    paired_backs: int
    paired_backs_undated: int


class PhotoSummary(BaseModel):
    id: int
    sha256: str
    rel_path: str
    album_id: int
    album_index: int
    rotation: int
    rotation_source: str
    back_sha256: Optional[str]
    year: Optional[int]
    date_label: str
    status: Status
    date_source: Optional[str]
    confidence: Optional[float]
    event_instance_id: Optional[int]
    event_label: Optional[str]
    dup_group_id: Optional[int]
    is_dup_keeper: bool
    width: Optional[int]
    height: Optional[int]


class PhotoDetail(PhotoSummary):
    album_name: str
    back_rel_path: Optional[str]
    caption: Optional[str]
    scan_datetime: Optional[str]
    trusted_label: str
    inferred_label: str
    inferred_confidence: Optional[float]
    inferred_source: Optional[str]
    back_date_label: str
    back_date_source: Optional[str]
    back_candidates: list[dict]
    back_transcription: Optional[str]
    dup_members: list[int]
    fixity_ok: Optional[bool]
    fixity_checked_at: Optional[datetime]


class PhotoPage(BaseModel):
    total: int
    items: list[PhotoSummary]


class BulkUpdate(BaseModel):
    ids: list[int]
    date: Optional[str] = None
    clear_date: bool = False
    event_kind_id: Optional[int] = None
    event_label: Optional[str] = None
    event_instance_id: Optional[int] = None
    clear_event: bool = False
    caption: Optional[str] = None
    rotate_by: Optional[int] = None
    date_source: Literal["manual", "back", "accepted"] = "manual"


class BulkResult(BaseModel):
    batch_id: Optional[str]
    photos: list[PhotoSummary]


class UndoRequest(BaseModel):
    batch_id: Optional[str] = None


class UndoResult(BaseModel):
    undone: Optional[str]
    description: Optional[str] = None
    reverted: int
    conflicts: list[dict]


class EditBatchOut(BaseModel):
    id: str
    ts: datetime
    description: str
    actor: str
    undone: bool


class EventKindIn(BaseModel):
    name: str
    month: Optional[int] = None
    day: Optional[int] = None


class EventKindOut(EventKindIn):
    id: int
    builtin: bool


class EventInstanceOut(BaseModel):
    id: int
    kind_id: int
    label: str
    year: Optional[int]
    count: int


class ParsedDate(BaseModel):
    ok: bool
    label: str = ""
    error: str = ""


class NeighbourOut(BaseModel):
    id: int
    sha256: str
    rotation: int
    rel_path: str
    distance: int
    date_label: str
    status: Status
    inferred_source: Optional[str]


class BackOut(BaseModel):
    kind: Literal["loose", "paired"]
    id: int
    album_id: int
    album_name: str
    rel_path: str
    sha256: str
    date_label: str
    source: Optional[str]
    confidence: Optional[float]
    candidates: list[dict]
    transcription: Optional[str]
    neighbours: list[NeighbourOut]


class BackDate(BaseModel):
    date: Optional[str] = None  # "1978-10" or "1978"; empty/None clears


class JobIn(BaseModel):
    type: Literal[
        "ingest", "orient", "read_backs", "estimate", "evaluate", "claude_backs", "faces", "cluster", "export", "backup",
        "fixity", "restore",
    ]
    params: dict = {}


class JobOut(BaseModel):
    id: int
    type: str
    state: str
    params: dict
    progress: int
    total: int
    message: str
    result: dict
    error: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]


class ClaudeEstimate(BaseModel):
    scope: str
    backs: int
    model: str
    input_tokens: int
    cost_low_usd: float
    cost_high_usd: float
    has_api_key: bool


class Stats(BaseModel):
    photos: int
    reviewed: int
    inferred: int
    untagged: int
    with_back: int
    duplicate_groups: int
    albums: int


# --- helpers ---------------------------------------------------------------------


def _status(p: Photo) -> Status:
    if p.t_year is not None:
        return "reviewed"
    if p.i_year is not None:
        return "inferred"
    return "untagged"


def _labels(s: Session) -> dict[int, str]:
    return {i.id: i.label for i in s.exec(select(EventInstance)).all()}


def summarize(p: Photo, labels: dict[int, str]) -> PhotoSummary:
    if p.t_year is not None:
        label = format_partial(p.t_year, p.t_month, p.t_day, p.t_precision, p.t_min, p.t_max)
        year, source = p.t_year, p.t_source
    elif p.i_year is not None:
        label = "≈" + format_partial(p.i_year, p.i_month, None, p.i_precision, p.i_min, p.i_max)
        year, source = p.i_year, "inferred"
    else:
        label, year, source = "", None, None
    return PhotoSummary(
        id=p.id,
        sha256=p.sha256,
        rel_path=p.rel_path,
        album_id=p.album_id,
        album_index=p.album_index,
        rotation=p.rotation,
        rotation_source=p.rotation_source,
        back_sha256=p.back_sha256,
        year=year,
        date_label=label,
        status=_status(p),
        date_source=source,
        confidence=p.i_confidence if p.t_year is None else None,
        event_instance_id=p.event_instance_id,
        event_label=labels.get(p.event_instance_id) if p.event_instance_id else None,
        dup_group_id=p.dup_group_id,
        is_dup_keeper=p.is_dup_keeper,
        width=p.width,
        height=p.height,
    )


def _loose_open():
    """Loose backs still waiting on a decision (no date, not marked "no date")."""
    return (LooseBack.d_year == None) & ((LooseBack.d_source == None) | (LooseBack.d_source != "manual"))  # noqa: E711


def _paired_open():
    return (Photo.back_year == None) & ((Photo.back_source == None) | (Photo.back_source != "manual"))  # noqa: E711


def _month_label(year: Optional[int], month: Optional[int]) -> str:
    if year is None:
        return ""
    return f"{year:04d}-{month:02d}" if month else f"{year:04d}"


def _detail(s: Session, p: Photo) -> PhotoDetail:
    base = summarize(p, _labels(s))
    members = (
        s.exec(select(Photo.id).where(Photo.dup_group_id == p.dup_group_id)).all() if p.dup_group_id else []
    )
    return PhotoDetail(
        **base.model_dump(),
        album_name=s.get(Album, p.album_id).name,
        back_rel_path=p.back_rel_path,
        caption=p.caption,
        scan_datetime=p.scan_datetime,
        trusted_label=format_partial(p.t_year, p.t_month, p.t_day, p.t_precision, p.t_min, p.t_max),
        inferred_label=format_partial(p.i_year, p.i_month, None, p.i_precision, p.i_min, p.i_max),
        inferred_confidence=p.i_confidence,
        inferred_source=p.i_source,
        back_date_label=_month_label(p.back_year, p.back_month),
        back_date_source=p.back_source,
        back_candidates=p.back_candidates or [],
        back_transcription=(bt.transcription if (bt := s.exec(select(BackText).where(BackText.photo_id == p.id)).first()) else None),
        dup_members=list(members),
        fixity_ok=p.fixity_ok,
        fixity_checked_at=p.fixity_checked_at,
    )


# --- albums & photos -----------------------------------------------------------


@router.get("/albums", response_model=list[AlbumOut])
def list_albums(s: Session = Depends(get_session)):
    counts = dict(
        s.exec(select(Photo.album_id, func.count()).where(Photo.missing == False).group_by(Photo.album_id)).all()  # noqa: E712
    )
    dated = dict(
        s.exec(
            select(Photo.album_id, func.count())
            .where(Photo.missing == False, (Photo.t_year != None) | (Photo.i_year != None))  # noqa: E711,E712
            .group_by(Photo.album_id)
        ).all()
    )
    live_loose = select(LooseBack.album_id, func.count()).where(LooseBack.missing == False)  # noqa: E712
    loose = dict(s.exec(live_loose.group_by(LooseBack.album_id)).all())
    loose_undated = dict(s.exec(live_loose.where(_loose_open()).group_by(LooseBack.album_id)).all())
    live_paired = select(Photo.album_id, func.count()).where(Photo.missing == False, Photo.back_sha256 != None)  # noqa: E711,E712
    paired = dict(s.exec(live_paired.group_by(Photo.album_id)).all())
    paired_undated = dict(s.exec(live_paired.where(_paired_open()).group_by(Photo.album_id)).all())
    return [
        AlbumOut(
            id=a.id, name=a.name, rel_path=a.rel_path, sort_order=a.sort_order,
            count=counts.get(a.id, 0), dated=dated.get(a.id, 0), missing=a.missing,
            loose_backs=loose.get(a.id, 0), loose_backs_undated=loose_undated.get(a.id, 0),
            paired_backs=paired.get(a.id, 0), paired_backs_undated=paired_undated.get(a.id, 0),
        )
        for a in s.exec(select(Album).order_by(Album.sort_order)).all()
    ]


@router.get("/photos", response_model=PhotoPage)
def list_photos(
    album_id: Optional[int] = None,
    person_id: Optional[int] = None,
    status: Optional[Status] = None,
    duplicates: bool = False,
    event_instance_id: Optional[int] = None,
    order: Literal["album", "chrono", "confidence"] = "album",
    offset: int = 0,
    limit: int = Query(20000, le=50000),
    s: Session = Depends(get_session),
):
    q = select(Photo).join(Album).where(Photo.missing == False)  # noqa: E712
    if album_id is not None:
        q = q.where(Photo.album_id == album_id)
    if status == "reviewed":
        q = q.where(Photo.t_year != None)  # noqa: E711
    elif status == "inferred":
        q = q.where(Photo.t_year == None, Photo.i_year != None)  # noqa: E711
    elif status == "untagged":
        q = q.where(Photo.t_year == None, Photo.i_year == None)  # noqa: E711
    if duplicates:
        q = q.where(Photo.dup_group_id != None)  # noqa: E711
    if event_instance_id is not None:
        q = q.where(Photo.event_instance_id == event_instance_id)
    if person_id is not None:
        from .models import Face

        q = q.where(Photo.id.in_(select(Face.photo_id).where(Face.person_id == person_id)))
    total = s.exec(select(func.count()).select_from(q.subquery())).one()
    if order == "chrono":
        eff = func.coalesce(Photo.t_year, Photo.i_year)
        q = q.order_by(
            case((eff == None, 1), else_=0), eff,  # noqa: E711
            func.coalesce(Photo.t_month, 7), func.coalesce(Photo.t_day, 1),
            Album.sort_order, Photo.album_index,
        )
    elif order == "confidence":  # review queue: least sure first
        q = q.order_by(case((Photo.i_confidence == None, 1), else_=0), Photo.i_confidence, Album.sort_order, Photo.album_index)  # noqa: E711
    elif duplicates:
        q = q.order_by(Photo.dup_group_id, Photo.id)
    else:
        q = q.order_by(Album.sort_order, Photo.album_index)
    rows = s.exec(q.offset(offset).limit(limit)).all()
    labels = _labels(s)
    return PhotoPage(total=total, items=[summarize(p, labels) for p in rows])


@router.get("/photos/{photo_id}", response_model=PhotoDetail)
def get_photo(photo_id: int, s: Session = Depends(get_session)):
    p = s.get(Photo, photo_id)
    if p is None:
        raise HTTPException(404, "photo not found")
    return _detail(s, p)


@router.patch("/photos", response_model=BulkResult)
def update_photos(body: BulkUpdate, s: Session = Depends(get_session)):
    try:
        batch = services.bulk_update(
            s, body.ids, source=body.date_source, **body.model_dump(exclude={"ids", "date_source"})
        )
    except (DateParseError, ValueError) as exc:
        raise HTTPException(422, str(exc))
    s.commit()
    if body.rotate_by:
        jobs.enqueue("faces")
    if body.date or body.clear_date or body.event_kind_id or body.event_instance_id or body.clear_event:
        jobs.enqueue("estimate")  # new anchors re-date the neighbours
    affected = set(body.ids)
    for p in s.exec(select(Photo).where(col(Photo.id).in_(body.ids))).all():
        if p.dup_group_id:
            affected |= set(s.exec(select(Photo.id).where(Photo.dup_group_id == p.dup_group_id)).all())
    labels = _labels(s)
    photos = s.exec(select(Photo).where(col(Photo.id).in_(affected))).all()
    return BulkResult(batch_id=batch, photos=[summarize(p, labels) for p in photos])


@router.post("/photos/{photo_id}/keeper", response_model=PhotoDetail)
def keep_photo(photo_id: int, s: Session = Depends(get_session)):
    services.set_keeper(s, photo_id)
    s.commit()
    return get_photo(photo_id, s)


@router.get("/parse-date", response_model=ParsedDate)
def parse_date(text: str):
    try:
        pd = parse_partial_date(text)
    except DateParseError as exc:
        return ParsedDate(ok=False, error=str(exc))
    return ParsedDate(ok=True, label=format_partial(pd.year, pd.month, pd.day, pd.precision, pd.min, pd.max))


@router.get("/img/{sha}/{kind}/{rotation}.jpg")
def image(sha: str, kind: Literal["thumb", "preview"], rotation: int, s: Session = Depends(get_session)):
    settings = get_settings()
    p = s.exec(select(Photo).where(Photo.sha256 == sha)).first()
    rel = p.rel_path if p else None
    if p is None:
        p = s.exec(select(Photo).where(Photo.back_sha256 == sha)).first()
        rel = p.back_rel_path if p else None
    if rel is None:
        lb = s.exec(select(LooseBack).where(LooseBack.sha256 == sha)).first()
        rel = lb.rel_path if lb else None
    if rel is None:
        raise HTTPException(404, "image not found")
    size = settings.thumb_size if kind == "thumb" else settings.preview_size
    path = ensure_thumb(settings.photos_root / rel, sha, size, rotation)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


# --- loose backs -----------------------------------------------------------------


def _loose_out(s: Session, lb: LooseBack, albums: dict[int, Album], window: int) -> BackOut:
    near = s.exec(
        select(Photo).where(
            Photo.album_id == lb.album_id,
            Photo.missing == False,  # noqa: E712
            Photo.list_index >= lb.list_index - window,
            Photo.list_index <= lb.list_index + window,
        ).order_by(Photo.list_index)
    ).all()
    labels = _labels(s)
    neighbours = []
    for p in near:
        sm = summarize(p, labels)
        neighbours.append(
            NeighbourOut(id=p.id, sha256=p.sha256, rotation=p.rotation, rel_path=p.rel_path,
                         distance=p.list_index - lb.list_index, date_label=sm.date_label, status=sm.status,
                         inferred_source=p.i_source)
        )
    return BackOut(
        kind="loose",
        id=lb.id, album_id=lb.album_id, album_name=albums[lb.album_id].name, rel_path=lb.rel_path,
        sha256=lb.sha256, date_label=_month_label(lb.d_year, lb.d_month), source=lb.d_source,
        confidence=lb.d_confidence, candidates=lb.candidates or [], transcription=lb.transcription,
        neighbours=neighbours,
    )


def _back_date_values(body: BackDate) -> tuple:
    if body.date and body.date.strip():
        try:
            pd = parse_partial_date(body.date)
        except DateParseError as exc:
            raise HTTPException(422, str(exc))
        if pd.precision not in ("year", "month", "day"):
            raise HTTPException(422, "enter a single year or month, e.g. 1978 or 1978-10")
        return (pd.year, pd.month, "manual", 1.0)
    return (None, None, "manual", None)  # "no date on this back" is also a decision


def _paired_out(s: Session, p: Photo, albums: dict[int, Album]) -> BackOut:
    sm = summarize(p, _labels(s))
    bt = s.exec(select(BackText).where(BackText.photo_id == p.id)).first()
    return BackOut(
        kind="paired", id=p.id, album_id=p.album_id, album_name=albums[p.album_id].name,
        rel_path=p.back_rel_path, sha256=p.back_sha256, date_label=_month_label(p.back_year, p.back_month),
        source=p.back_source, confidence=p.back_confidence, candidates=p.back_candidates or [],
        transcription=bt.transcription if bt else None,
        neighbours=[NeighbourOut(id=p.id, sha256=p.sha256, rotation=p.rotation, rel_path=p.rel_path, distance=0,
                                 date_label=sm.date_label, status=sm.status, inferred_source=p.i_source)],
    )


@router.get("/paired-backs", response_model=list[BackOut])
def paired_backs(album_id: Optional[int] = None, undated: bool = False, s: Session = Depends(get_session)):
    q = select(Photo).join(Album).where(Photo.missing == False, Photo.back_sha256 != None)  # noqa: E711,E712
    if album_id is not None:
        q = q.where(Photo.album_id == album_id)
    if undated:
        q = q.where(_paired_open())
    albums = {a.id: a for a in s.exec(select(Album)).all()}
    return [_paired_out(s, p, albums) for p in s.exec(q.order_by(Album.sort_order, Photo.list_index)).all()]


@router.patch("/paired-backs/{photo_id}", response_model=BackOut)
def set_paired_back_date(photo_id: int, body: BackDate, s: Session = Depends(get_session)):
    from .estimate import run_estimates

    p = s.get(Photo, photo_id)
    if p is None or not p.back_sha256:
        raise HTTPException(404, "photo has no back scan")
    values = _back_date_values(body)
    editlog.record(
        s, [(p, f, v) for f, v in zip(("back_year", "back_month", "back_source", "back_confidence"), values)],
        f"date on back {p.back_rel_path.rsplit('/', 1)[-1]}",
    )
    s.commit()
    run_estimates(album_id=p.album_id)
    s.expire_all()
    albums = {a.id: a for a in s.exec(select(Album)).all()}
    return _paired_out(s, s.get(Photo, photo_id), albums)


@router.get("/loose-backs", response_model=list[BackOut])
def loose_backs(album_id: Optional[int] = None, undated: bool = False, s: Session = Depends(get_session)):
    q = select(LooseBack).join(Album).where(LooseBack.missing == False)  # noqa: E712
    if album_id is not None:
        q = q.where(LooseBack.album_id == album_id)
    if undated:
        q = q.where(_loose_open())
    q = q.order_by(Album.sort_order, LooseBack.list_index)
    albums = {a.id: a for a in s.exec(select(Album)).all()}
    window = get_settings().loose_back_window
    return [_loose_out(s, lb, albums, window) for lb in s.exec(q).all()]


@router.patch("/loose-backs/{loose_id}", response_model=BackOut)
def set_loose_back_date(loose_id: int, body: BackDate, s: Session = Depends(get_session)):
    from .estimate import run_estimates

    lb = s.get(LooseBack, loose_id)
    if lb is None:
        raise HTTPException(404, "loose back not found")
    values = _back_date_values(body)
    editlog.record(
        s, [(lb, f, v) for f, v in zip(("d_year", "d_month", "d_source", "d_confidence"), values)],
        f"date on back {lb.rel_path.rsplit('/', 1)[-1]}",
    )
    s.commit()
    run_estimates(album_id=lb.album_id)
    s.expire_all()
    albums = {a.id: a for a in s.exec(select(Album)).all()}
    return _loose_out(s, s.get(LooseBack, loose_id), albums, get_settings().loose_back_window)


# --- edits ---------------------------------------------------------------------


@router.post("/undo", response_model=UndoResult)
def undo(body: UndoRequest, s: Session = Depends(get_session)):
    result = editlog.undo(s, body.batch_id)
    s.commit()
    if result["undone"]:
        from .estimate import run_estimates

        run_estimates()  # the undone batch may have been a loose-back date; cheap at this scale
    return UndoResult(**result)


@router.get("/edits", response_model=list[EditBatchOut])
def recent_edits(limit: int = 30, s: Session = Depends(get_session)):
    rows = s.exec(select(EditBatch).order_by(EditBatch.ts.desc()).limit(limit)).all()
    return [EditBatchOut(**r.model_dump()) for r in rows]


# --- events --------------------------------------------------------------------


@router.get("/event-kinds", response_model=list[EventKindOut])
def event_kinds(s: Session = Depends(get_session)):
    return [EventKindOut(**k.model_dump()) for k in s.exec(select(EventKind).order_by(EventKind.name)).all()]


@router.post("/event-kinds", response_model=EventKindOut)
def add_event_kind(body: EventKindIn, s: Session = Depends(get_session)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name required")
    if s.exec(select(EventKind).where(EventKind.name == name)).first():
        raise HTTPException(409, "event kind exists")
    k = EventKind(name=name, month=body.month, day=body.day)
    s.add(k)
    s.commit()
    return EventKindOut(**k.model_dump())


@router.get("/event-instances", response_model=list[EventInstanceOut])
def event_instances(kind_id: Optional[int] = None, s: Session = Depends(get_session)):
    counts = dict(
        s.exec(select(Photo.event_instance_id, func.count()).group_by(Photo.event_instance_id)).all()
    )
    q = select(EventInstance).order_by(EventInstance.year, EventInstance.label)
    if kind_id is not None:
        q = q.where(EventInstance.kind_id == kind_id)
    return [
        EventInstanceOut(id=i.id, kind_id=i.kind_id, label=i.label, year=i.year, count=counts.get(i.id, 0))
        for i in s.exec(q).all()
    ]


# --- jobs & stats ----------------------------------------------------------------


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(limit: int = 30, s: Session = Depends(get_session)):
    return [JobOut(**j.model_dump()) for j in s.exec(select(Job).order_by(Job.id.desc()).limit(limit)).all()]


@router.post("/jobs", response_model=JobOut)
def start_job(body: JobIn):
    job = jobs.enqueue(body.type, body.params)
    return JobOut(**job.model_dump())


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    jobs.request_cancel(job_id)
    return {"ok": True}


@router.get("/claude/estimate", response_model=ClaudeEstimate)
def claude_estimate(scope: Literal["unread", "all"] = "unread"):
    from .vision import cost_estimate

    return ClaudeEstimate(**cost_estimate(scope))


@router.get("/stats", response_model=Stats)
def stats(s: Session = Depends(get_session)):
    live = select(Photo).where(Photo.missing == False)  # noqa: E712

    def count(q) -> int:
        return s.exec(select(func.count()).select_from(q.subquery())).one()

    return Stats(
        photos=count(live),
        reviewed=count(live.where(Photo.t_year != None)),  # noqa: E711
        inferred=count(live.where(Photo.t_year == None, Photo.i_year != None)),  # noqa: E711
        untagged=count(live.where(Photo.t_year == None, Photo.i_year == None)),  # noqa: E711
        with_back=count(live.where(Photo.back_sha256 != None)),  # noqa: E711
        duplicate_groups=s.exec(
            select(func.count(func.distinct(Photo.dup_group_id))).where(Photo.missing == False)  # noqa: E712
        ).one(),
        albums=s.exec(select(func.count()).select_from(Album).where(Album.missing == False)).one(),  # noqa: E712
    )
