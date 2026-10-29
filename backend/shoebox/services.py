"""Photo edits used by the API and restore. All changes go through the edit log."""

from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from . import editlog
from .dates import PartialDate, parse_partial_date
from .models import EventInstance, EventKind, Photo

TRUSTED_FIELDS = ("t_year", "t_month", "t_day", "t_min", "t_max", "t_precision", "t_source")


def with_dup_group(session: Session, photos: list[Photo]) -> list[Photo]:
    """Expand a selection to include every member of each photo's duplicate group."""
    ids = {p.id for p in photos}
    gids = {p.dup_group_id for p in photos if p.dup_group_id is not None}
    out = list(photos)
    if gids:
        for p in session.exec(select(Photo).where(Photo.dup_group_id.in_(gids))).all():
            if p.id not in ids:
                ids.add(p.id)
                out.append(p)
    return out


def get_or_create_instance(
    session: Session, kind: EventKind, year: Optional[int], label: Optional[str] = None
) -> EventInstance:
    label = (label or "").strip() or (f"{kind.name} {year}" if year else kind.name)
    inst = session.exec(
        select(EventInstance).where(EventInstance.kind_id == kind.id, EventInstance.label == label)
    ).first()
    if inst is None:
        inst = EventInstance(kind_id=kind.id, label=label, year=year, month=kind.month, day=kind.day)
        session.add(inst)
        session.flush()
    return inst


def _date_changes(p: Photo, pd: Optional[PartialDate], source: str) -> list[tuple[Photo, str, Any]]:
    values = pd.as_trusted(source) if pd else dict.fromkeys(TRUSTED_FIELDS)
    return [(p, f, values[f]) for f in TRUSTED_FIELDS]


def _event_date(inst: EventInstance) -> Optional[PartialDate]:
    if inst.year is None:
        return None
    if inst.month and inst.day:
        return PartialDate(year=inst.year, month=inst.month, day=inst.day, min=inst.year, max=inst.year, precision="day")
    return PartialDate(year=inst.year, min=inst.year, max=inst.year)


def bulk_update(
    session: Session,
    photo_ids: list[int],
    *,
    date: Optional[str] = None,
    clear_date: bool = False,
    event_kind_id: Optional[int] = None,
    event_label: Optional[str] = None,
    event_instance_id: Optional[int] = None,
    clear_event: bool = False,
    caption: Optional[str] = None,
    rotate_by: Optional[int] = None,
    source: str = "manual",
    description: str = "",
) -> Optional[str]:
    photos = list(session.exec(select(Photo).where(Photo.id.in_(photo_ids))).all())
    if not photos:
        return None
    # Tags are shared across duplicate groups; rotation is per file.
    tagged = with_dup_group(session, photos)
    changes: list[tuple[Any, str, Any]] = []

    pd = parse_partial_date(date) if date else None

    inst: Optional[EventInstance] = None
    if event_instance_id is not None:
        inst = session.get(EventInstance, event_instance_id)
        if inst is None:
            raise ValueError(f"event instance {event_instance_id} not found")
    elif event_kind_id is not None:
        kind = session.get(EventKind, event_kind_id)
        if kind is None:
            raise ValueError(f"event kind {event_kind_id} not found")
        year = pd.year if pd and pd.precision in ("day", "month", "year") else None
        if year is None and pd is None:
            years = {p.t_year for p in photos if p.t_year and p.t_precision in ("day", "month", "year")}
            year = years.pop() if len(years) == 1 else None
        inst = get_or_create_instance(session, kind, year, event_label)
    ev_date = _event_date(inst) if inst is not None else None
    # "1985" + Christmas means Dec 25, 1985: a fixed-date holiday refines a bare year.
    if pd and pd.precision == "year" and ev_date and ev_date.precision == "day" and ev_date.year == pd.year:
        pd = ev_date
    if pd or clear_date:
        for p in tagged:
            changes += _date_changes(p, pd, source)

    if inst is not None:
        for p in tagged:
            changes.append((p, "event_instance_id", inst.id))
            # An event with a known date dates photos that have no trusted date of their own.
            if pd is None and ev_date and (p.t_year is None or p.t_source == "event"):
                changes += _date_changes(p, ev_date, "event")
    elif clear_event:
        for p in tagged:
            changes.append((p, "event_instance_id", None))

    if caption is not None:
        for p in tagged:
            changes.append((p, "caption", caption.strip() or None))

    if rotate_by:
        for p in photos:
            changes.append((p, "rotation", (p.rotation + rotate_by) % 360))
            changes.append((p, "rotation_source", "manual"))
            changes.append((p, "faces_model_ver", 0))  # look for faces again the right way up

    if not description:
        parts = []
        if pd:
            parts.append(f"date {date}")
        if clear_date:
            parts.append("clear date")
        if inst:
            parts.append(f"event {inst.label}")
        if clear_event:
            parts.append("clear event")
        if caption is not None:
            parts.append("caption")
        if rotate_by:
            parts.append(f"rotate {rotate_by}")
        description = f"{', '.join(parts) or 'edit'} on {len(photos)} photo(s)"
    return editlog.record(session, changes, description)


def set_keeper(session: Session, photo_id: int) -> Optional[str]:
    p = session.get(Photo, photo_id)
    if p is None or p.dup_group_id is None:
        return None
    members = session.exec(select(Photo).where(Photo.dup_group_id == p.dup_group_id)).all()
    changes = [(m, "is_dup_keeper", m.id == p.id) for m in members]
    return editlog.record(session, changes, f"keep {p.rel_path} in duplicate group")
