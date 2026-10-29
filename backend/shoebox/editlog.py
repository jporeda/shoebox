"""Append-only edit log with batch undo.

Every user-facing change goes through ``record`` so it can be undone as a batch
(e.g. "set year on 40 photos" or "name this cluster").
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import DateTime
from sqlmodel import Session, SQLModel, select

from .models import EditBatch, Edit, EventInstance, Face, LooseBack, Person, Photo, utcnow

ENTITIES: dict[str, type[SQLModel]] = {
    "photo": Photo,
    "face": Face,
    "person": Person,
    "eventinstance": EventInstance,
    "looseback": LooseBack,
}


# Progress markers the app updates on its own. Older batches recorded some of them;
# undo neither checks nor restores them.
STAGE_FIELDS = {"faces_model_ver", "orient_ver", "thumb_ver", "back_ocr_ver", "ocr_ver", "vision_ver"}


# Pseudo-fields for whole rows, used as (obj, CREATED, True) / (obj, DELETED, True) changes.
# Undoing a creation deletes the row; undoing a deletion recreates it from a snapshot.
CREATED, DELETED = "_created", "_deleted"


def _jsonable(v: Any) -> Any:
    if isinstance(v, bytes):
        return {"base64": base64.b64encode(v).decode()}
    return v.isoformat() if isinstance(v, datetime) else v


def _snapshot(obj: SQLModel) -> dict:
    return {c.name: _jsonable(getattr(obj, c.name)) for c in obj.__table__.columns}


def _recreate(session: Session, model: type[SQLModel], snap: dict) -> SQLModel:
    values = {}
    for c in model.__table__.columns:
        v = snap.get(c.name)
        if isinstance(v, dict) and set(v) == {"base64"}:
            v = base64.b64decode(v["base64"])
        elif isinstance(v, str) and isinstance(c.type, DateTime):
            v = datetime.fromisoformat(v)
        values[c.name] = v
    if session.get(model, values["id"]) is not None:
        values.pop("id")  # the id was reused meanwhile
    obj = model(**values)
    session.add(obj)
    session.flush()
    return obj


def _remove_created(session: Session, obj: SQLModel) -> bool:
    """Delete a row this batch created. False if something else now depends on it."""
    if isinstance(obj, Person):
        if session.exec(select(Face.id).where(Face.person_id == obj.id)).first() is not None:
            return False  # named on later faces too
        for f in session.exec(select(Face).where(Face.suggested_person_id == obj.id)).all():
            f.suggested_person_id = None  # a guess, recomputed by the next naming run
            session.add(f)
    session.delete(obj)
    session.flush()
    return True


def record(
    session: Session,
    changes: Iterable[tuple[SQLModel, str, Any]],
    description: str,
    actor: str = "user",
) -> str | None:
    """Apply (obj, field, new_value) changes and log them. Returns the batch id."""
    batch_id = uuid.uuid4().hex
    edits: list[Edit] = []
    for obj, field, new in changes:
        if field == CREATED:
            session.add(obj)
            session.flush()
            edits.append(Edit(batch_id=batch_id, entity=obj.__tablename__, entity_id=obj.id, field=CREATED, new=True))
            continue
        if field == DELETED:
            edits.append(Edit(batch_id=batch_id, entity=obj.__tablename__, entity_id=obj.id, field=DELETED,
                              old=_snapshot(obj)))
            session.delete(obj)
            session.flush()
            continue
        old = getattr(obj, field)
        if old == new:
            continue
        setattr(obj, field, new)
        if isinstance(obj, Photo):
            obj.updated_at = utcnow()
        session.add(obj)
        session.flush()  # assigns ids for newly created objects
        edits.append(
            Edit(
                batch_id=batch_id,
                entity=obj.__tablename__,
                entity_id=obj.id,
                field=field,
                old=_jsonable(old),
                new=_jsonable(new),
            )
        )
    if not edits:
        return None
    session.add(EditBatch(id=batch_id, description=description, actor=actor))
    session.flush()
    session.add_all(edits)
    return batch_id


def undo(session: Session, batch_id: str | None = None, skip: bool = False) -> dict:
    """Revert a batch (default: the latest one not yet undone).

    A field is only reverted if it still holds the value this batch wrote;
    otherwise it's reported as a conflict and left alone. If nothing at all can be
    reverted, the batch stays in place (``blocked``) rather than being silently used
    up; ``skip`` then marks it undone without changing anything.
    """
    if batch_id is None:
        batch = session.exec(
            select(EditBatch).where(EditBatch.undone == False).order_by(EditBatch.ts.desc())  # noqa: E712
        ).first()
    else:
        batch = session.get(EditBatch, batch_id)
    if batch is None or batch.undone:
        return {"undone": None, "reverted": 0, "conflicts": []}
    result = {"batch_id": batch.id, "description": batch.description}
    if skip:
        batch.undone = True
        session.add(batch)
        return {**result, "undone": batch.id, "reverted": 0, "conflicts": [], "skipped": True}
    edits = [
        e for e in session.exec(select(Edit).where(Edit.batch_id == batch.id).order_by(Edit.id.desc())).all()
        if e.field not in STAGE_FIELDS
    ]
    reverted, conflicts = 0, []
    # Per entity, all-or-nothing: reverting some fields of a photo that was edited
    # again later would leave it half old, half new (e.g. year without precision).
    blocked: set[tuple[str, int]] = set()
    for e in edits:
        if e.field == DELETED:
            continue  # recreating a deleted row can't clash with later edits
        model = ENTITIES.get(e.entity)
        obj = session.get(model, e.entity_id) if model else None
        if obj is None or (e.field != CREATED and _jsonable(getattr(obj, e.field)) != e.new):
            conflicts.append({"entity": e.entity, "id": e.entity_id, "field": e.field})
            blocked.add((e.entity, e.entity_id))
    for e in edits:  # newest first, so a row's own field edits are reverted before its creation
        if (e.entity, e.entity_id) in blocked:
            continue
        model = ENTITIES[e.entity]
        if e.field == DELETED:
            _recreate(session, model, e.old)
        elif e.field == CREATED:
            if not _remove_created(session, session.get(model, e.entity_id)):
                conflicts.append({"entity": e.entity, "id": e.entity_id, "field": e.field})
                continue
        else:
            obj = session.get(model, e.entity_id)
            setattr(obj, e.field, e.old)
            if isinstance(obj, Photo):
                obj.updated_at = utcnow()
            session.add(obj)
        reverted += 1
    if edits and not reverted:
        return {**result, "undone": None, "reverted": 0, "conflicts": conflicts, "blocked": True}
    batch.undone = True
    session.add(batch)
    return {**result, "undone": batch.id, "reverted": reverted, "conflicts": conflicts}
