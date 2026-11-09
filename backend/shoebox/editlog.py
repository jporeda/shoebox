"""Append-only edit log with batch undo.

Every user-facing change goes through ``record`` so it can be undone as a batch
(e.g. "set year on 40 photos" or "name this cluster").
"""

from __future__ import annotations

import base64
import uuid
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import DateTime, or_, true
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


# Who made the edits: "client:<id>" for a browser (its X-Shoebox-Client header, set by the app's
# middleware), ANONYMOUS for any other web request (no valid id: curl, a script, a stale tab), and
# "user" outside a request (CLI, worker jobs). "restore" is passed explicitly by backup.restore.
CURRENT_ACTOR: ContextVar[str] = ContextVar("shoebox_actor", default="user")
CLIENT_PREFIX = "client:"
ANONYMOUS = "anonymous"


def undoable(actor: str, explicit: bool = False):
    """SQL filter: the batches ``actor`` may undo.

    Batches whose actor isn't "client:..." are *shared* (jobs, restore, CLI, anonymous requests,
    edits from before per-device undo).
    - A browser's Undo button (``explicit=False``) takes back only that browser's own batches.
      By explicit batch id it may also take back a shared batch (e.g. "undo this job run").
    - A web request without a valid client id may only touch shared batches, never a device's.
    - Outside a web request (CLI): every batch.
    """
    shared = ~EditBatch.actor.startswith(CLIENT_PREFIX)
    if actor.startswith(CLIENT_PREFIX):
        own = EditBatch.actor == actor
        return or_(own, shared) if explicit else own
    if actor == ANONYMOUS:
        return shared
    return true()


def shared_batches():
    """SQL filter: batches no single device owns (see ``undoable``)."""
    return ~EditBatch.actor.startswith(CLIENT_PREFIX)


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
    actor: str | None = None,
) -> str | None:
    """Apply (obj, field, new_value) changes and log them. Returns the batch id."""
    actor = actor or CURRENT_ACTOR.get()
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


def _touched_later(session: Session, batch_id: str, edits: list[Edit]) -> set[tuple[str, int]]:
    """(entity, id) pairs among ``edits`` that a newer, not-undone batch has edited since.
    Stage fields don't count: jobs bump them and undo ignores them."""
    if not edits:
        return set()
    ours = {(e.entity, e.entity_id) for e in edits}
    rows = session.exec(
        select(Edit.entity, Edit.entity_id)
        .join(EditBatch, EditBatch.id == Edit.batch_id)
        .where(
            Edit.batch_id != batch_id,
            Edit.id > max(e.id for e in edits),
            EditBatch.undone == False,  # noqa: E712
            Edit.field.not_in(STAGE_FIELDS),
            Edit.entity_id.in_({i for _, i in ours}),
        )
    ).all()
    return {(entity, i) for entity, i in rows if (entity, i) in ours}


def undo(session: Session, batch_id: str | None = None, skip: bool = False, actor: str = "user") -> dict:
    """Revert a batch (default: the latest one not yet undone that ``actor`` may undo).

    A field is only reverted if it still holds the value this batch wrote;
    otherwise it's reported as a conflict and left alone. If nothing at all can be
    reverted, the batch stays in place (``blocked``) rather than being silently used
    up; ``skip`` then marks it undone without changing anything.
    """
    if batch_id is None:
        batch = session.exec(
            select(EditBatch).where(EditBatch.undone == False, undoable(actor)).order_by(EditBatch.ts.desc())  # noqa: E712
        ).first()
    else:  # someone else's batch reads as "nothing to undo", same as an unknown id
        batch = session.exec(select(EditBatch).where(EditBatch.id == batch_id, undoable(actor, explicit=True))).first()
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
    # A row another batch edited since (and that wasn't undone) is left alone entirely: with Undo
    # per device, batches are no longer undone strictly newest first, so a field still holding our
    # value isn't enough, and deleting a row we created would throw away the later edits to it.
    touched_later = _touched_later(session, batch.id, edits)
    for e in edits:
        if e.field == DELETED:
            continue  # recreating a deleted row can't clash with later edits
        if (e.entity, e.entity_id) in touched_later:
            conflicts.append({"entity": e.entity, "id": e.entity_id, "field": e.field})
            blocked.add((e.entity, e.entity_id))
            continue
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
