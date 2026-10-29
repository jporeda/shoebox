"""Append-only edit log with batch undo.

Every user-facing change goes through ``record`` so it can be undone as a batch
(e.g. "set year on 40 photos" or "name this cluster").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Iterable

from sqlmodel import Session, SQLModel, select

from .models import EditBatch, Edit, EventInstance, Face, LooseBack, Person, Photo, utcnow

ENTITIES: dict[str, type[SQLModel]] = {
    "photo": Photo,
    "face": Face,
    "person": Person,
    "eventinstance": EventInstance,
    "looseback": LooseBack,
}


def _jsonable(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


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


def undo(session: Session, batch_id: str | None = None) -> dict:
    """Revert a batch (default: the latest one not yet undone).

    A field is only reverted if it still holds the value this batch wrote;
    otherwise it's reported as a conflict and left alone.
    """
    if batch_id is None:
        batch = session.exec(
            select(EditBatch).where(EditBatch.undone == False).order_by(EditBatch.ts.desc())  # noqa: E712
        ).first()
    else:
        batch = session.get(EditBatch, batch_id)
    if batch is None or batch.undone:
        return {"undone": None, "reverted": 0, "conflicts": []}
    edits = session.exec(select(Edit).where(Edit.batch_id == batch.id).order_by(Edit.id.desc())).all()
    reverted, conflicts = 0, []
    # Per entity, all-or-nothing: reverting some fields of a photo that was edited
    # again later would leave it half old, half new (e.g. year without precision).
    blocked: set[tuple[str, int]] = set()
    for e in edits:
        model = ENTITIES.get(e.entity)
        obj = session.get(model, e.entity_id) if model else None
        if obj is None or _jsonable(getattr(obj, e.field)) != e.new:
            conflicts.append({"entity": e.entity, "id": e.entity_id, "field": e.field})
            blocked.add((e.entity, e.entity_id))
    for e in edits:
        if (e.entity, e.entity_id) in blocked:
            continue
        obj = session.get(ENTITIES[e.entity], e.entity_id)
        setattr(obj, e.field, e.old)
        if isinstance(obj, Photo):
            obj.updated_at = utcnow()
        session.add(obj)
        reverted += 1
    batch.undone = True
    session.add(batch)
    return {"undone": batch.id, "description": batch.description, "reverted": reverted, "conflicts": conflicts}
