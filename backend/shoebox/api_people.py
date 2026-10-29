"""HTTP API for faces and people."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, col, select

from . import editlog, jobs, people
from .dates import format_partial
from .db import get_session
from .faces import from_blob, rotate_box
from .models import Album, Face, Person, Photo

router = APIRouter(prefix="/api")


class FaceOut(BaseModel):
    id: int
    photo_id: int
    photo_sha256: str
    rotation: int
    x: float
    y: float
    w: float
    h: float
    person_id: Optional[int]
    person_name: Optional[str]
    source: str
    ignored: bool
    est_age: Optional[float]
    cluster_id: Optional[int]
    year_label: str
    suggested_person_id: Optional[int]
    suggested_name: Optional[str]
    confidence: Optional[float]


class Suggestion(BaseModel):
    person_id: int
    name: str
    similarity: float


class ClusterOut(BaseModel):
    cluster_id: int
    size: int
    # Every member, including ones past `faces`: naming acts on exactly these, since
    # cluster ids are renumbered whenever faces are grouped again.
    member_ids: list[int]
    faces: list[FaceOut]
    suggestion: Optional[Suggestion]


class PersonIn(BaseModel):
    name: Optional[str] = None
    birth_year: Optional[int] = None
    notes: Optional[str] = None


class PersonOut(BaseModel):
    id: int
    name: str
    birth_year: Optional[int]
    notes: Optional[str]
    face_count: int
    auto_count: int
    cover_face_id: Optional[int]
    first_year: Optional[int]
    last_year: Optional[int]


class AssignIn(BaseModel):
    face_ids: list[int]
    person_id: Optional[int] = None
    name: Optional[str] = None  # creates the person if needed


class IgnoreIn(BaseModel):
    face_ids: list[int]
    ignored: bool = True


class DrawIn(BaseModel):
    x: float
    y: float
    w: float
    h: float
    person_id: Optional[int] = None
    name: Optional[str] = None


class MergeIn(BaseModel):
    into_id: int


class FaceIds(BaseModel):
    face_ids: list[int]


class ReviewGroup(BaseModel):
    person_id: int
    name: str
    faces: list[FaceOut]


class BatchOut(BaseModel):
    batch_id: Optional[str]
    person: Optional[PersonOut] = None


def _year_label(p: Photo) -> str:
    if p.t_year is not None:
        return format_partial(p.t_year, p.t_month, p.t_day, p.t_precision, p.t_min, p.t_max)
    if p.i_year is not None:
        return "≈" + format_partial(p.i_year, p.i_month, None, p.i_precision, p.i_min, p.i_max)
    return ""


def face_out(f: Face, p: Photo, names: dict[int, str]) -> FaceOut:
    x, y, w, h = rotate_box((f.x, f.y, f.w, f.h), f.frame_rotation, p.rotation)
    return FaceOut(
        id=f.id, photo_id=p.id, photo_sha256=p.sha256, rotation=p.rotation, x=x, y=y, w=w, h=h,
        person_id=f.person_id, person_name=names.get(f.person_id) if f.person_id else None,
        source=f.source, ignored=f.ignored, est_age=f.est_age, cluster_id=f.cluster_id, year_label=_year_label(p),
        suggested_person_id=f.suggested_person_id,
        suggested_name=names.get(f.suggested_person_id) if f.suggested_person_id else None,
        confidence=f.confidence,
    )


def _names(s: Session) -> dict[int, str]:
    return {p.id: p.name for p in s.exec(select(Person)).all()}


def _faces_out(s: Session, faces: list[Face]) -> list[FaceOut]:
    names = _names(s)
    photos = {p.id: p for p in s.exec(select(Photo).where(col(Photo.id).in_({f.photo_id for f in faces}))).all()}
    return [face_out(f, photos[f.photo_id], names) for f in faces if f.photo_id in photos]


def _person_out(s: Session, person: Person) -> PersonOut:
    faces = s.exec(select(Face).where(Face.person_id == person.id)).all()
    photos = s.exec(select(Photo).where(col(Photo.id).in_({f.photo_id for f in faces}))).all()
    years = sorted(y for p in photos if (y := p.t_year if p.t_year is not None else p.i_year) is not None)
    cover = max(faces, key=lambda f: (f.source != "auto", f.w * f.h), default=None)
    return PersonOut(
        id=person.id, name=person.name, birth_year=person.birth_year, notes=person.notes, face_count=len(faces),
        auto_count=sum(f.source == "auto" for f in faces),
        cover_face_id=cover.id if cover else None, first_year=years[0] if years else None,
        last_year=years[-1] if years else None,
    )


# --- people ------------------------------------------------------------------------


@router.get("/people", response_model=list[PersonOut])
def list_people(s: Session = Depends(get_session)):
    return sorted((_person_out(s, p) for p in s.exec(select(Person)).all()), key=lambda p: (-p.face_count, p.name))


@router.post("/people", response_model=PersonOut)
def create_person(body: PersonIn, s: Session = Depends(get_session)):
    if not body.name or not body.name.strip():
        raise HTTPException(422, "name required")
    person, changes = people.person_for_name(s, body.name)
    if body.birth_year is not None:
        changes.append((person, "birth_year", body.birth_year))
    editlog.record(s, changes, f"add {person.name}")
    s.commit()
    return _person_out(s, person)


@router.patch("/people/{person_id}", response_model=PersonOut)
def update_person(person_id: int, body: PersonIn, s: Session = Depends(get_session)):
    person = s.get(Person, person_id)
    if person is None:
        raise HTTPException(404, "person not found")
    changes = []
    if body.name is not None and body.name.strip() and body.name.strip() != person.name:
        if s.exec(select(Person).where(Person.name == body.name.strip())).first():
            raise HTTPException(409, f"{body.name.strip()} already exists; merge instead")
        changes.append((person, "name", body.name.strip()))
    if "birth_year" in body.model_fields_set:
        if body.birth_year is not None and not 1850 <= body.birth_year <= 2100:
            raise HTTPException(422, "birth year out of range")
        changes.append((person, "birth_year", body.birth_year))
    if "notes" in body.model_fields_set:
        changes.append((person, "notes", body.notes))
    editlog.record(s, changes, f"edit {person.name}")
    s.commit()
    if "birth_year" in body.model_fields_set:
        jobs.enqueue("estimate")  # ages now date this person's photos
    return _person_out(s, person)


@router.post("/people/{person_id}/merge", response_model=BatchOut)
def merge_person(person_id: int, body: MergeIn, s: Session = Depends(get_session)):
    if person_id == body.into_id or not s.get(Person, person_id) or not s.get(Person, body.into_id):
        raise HTTPException(422, "pick two different people")
    batch = people.merge_people(s, person_id, body.into_id)
    s.commit()
    jobs.enqueue("estimate")
    return BatchOut(batch_id=batch, person=_person_out(s, s.get(Person, body.into_id)))


@router.get("/people/{person_id}/faces", response_model=list[FaceOut])
def person_faces(person_id: int, s: Session = Depends(get_session)):
    faces = list(s.exec(select(Face).where(Face.person_id == person_id)).all())
    out = _faces_out(s, faces)
    photos = {p.id: p for p in s.exec(select(Photo).where(col(Photo.id).in_({f.photo_id for f in faces}))).all()}
    albums = {a.id: a.sort_order for a in s.exec(select(Album)).all()}

    def key(fo: FaceOut):
        p = photos[fo.photo_id]
        y = p.t_year if p.t_year is not None else p.i_year
        return (y is None, y or 0, albums.get(p.album_id, 0), p.album_index)

    return sorted(out, key=key)


# --- clusters & faces ---------------------------------------------------------------


@router.get("/clusters", response_model=list[ClusterOut])
def list_clusters(per_cluster: int = 80, singles: bool = False, s: Session = Depends(get_session)):
    q = select(Face).where(Face.person_id == None, Face.ignored == False)  # noqa: E711,E712
    q = q.where(Face.cluster_id == None) if singles else q.where(Face.cluster_id != None)  # noqa: E711
    faces = list(s.exec(q).all())
    groups: dict[int, list[Face]] = {}
    for f in faces:
        groups.setdefault(f.cluster_id or 0, []).append(f)
    centroids = people.person_centroids(s)
    names = _names(s)
    photos = {p.id: p for p in s.exec(select(Photo).where(col(Photo.id).in_({f.photo_id for f in faces}))).all()}

    def era(f: Face):  # oldest first, so a sibling or a different era stands out in the row
        p = photos.get(f.photo_id)
        y = (p.t_year if p.t_year is not None else p.i_year) if p else None
        return (y is None, y or 0, p.album_id if p else 0, p.album_index if p else 0)

    out = []
    for cid, members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        members.sort(key=era)
        sug = None
        if cid:
            embs = [from_blob(f.embedding) for f in members if f.embedding]
            if (hit := people.suggest(embs, centroids)) is not None:
                sug = Suggestion(person_id=hit[0], name=names[hit[0]], similarity=round(hit[1], 3))
        out.append(ClusterOut(cluster_id=cid, size=len(members), member_ids=[f.id for f in members],
                              faces=_faces_out(s, members[: (len(members) if singles else per_cluster)]), suggestion=sug))
    return out


@router.get("/clusters/{cluster_id}/faces", response_model=list[FaceOut])
def cluster_faces(cluster_id: int, s: Session = Depends(get_session)):
    faces = s.exec(
        select(Face).where(Face.cluster_id == cluster_id, Face.person_id == None, Face.ignored == False)  # noqa: E711,E712
    ).all()
    return _faces_out(s, list(faces))


@router.get("/photos/{photo_id}/faces", response_model=list[FaceOut])
def photo_faces(photo_id: int, s: Session = Depends(get_session)):
    return _faces_out(s, list(s.exec(select(Face).where(Face.photo_id == photo_id)).all()))


@router.post("/faces/assign", response_model=BatchOut)
def assign_faces(body: AssignIn, s: Session = Depends(get_session)):
    person_id, new_person = body.person_id, []
    if body.name and body.name.strip():
        person, new_person = people.person_for_name(s, body.name)
        person_id = person.id
    elif person_id is not None and s.get(Person, person_id) is None:
        raise HTTPException(404, "person not found")
    batch = people.assign(s, body.face_ids, person_id, extra=new_person)
    s.commit()
    jobs.enqueue("estimate")
    return BatchOut(batch_id=batch, person=_person_out(s, s.get(Person, person_id)) if person_id else None)


@router.post("/faces/ignore", response_model=BatchOut)
def ignore_faces(body: IgnoreIn, s: Session = Depends(get_session)):
    batch = people.set_ignored(s, body.face_ids, body.ignored)
    s.commit()
    jobs.enqueue("estimate")
    return BatchOut(batch_id=batch)


@router.post("/faces/accept", response_model=BatchOut)
def accept_faces(body: FaceIds, s: Session = Depends(get_session)):
    batch = people.accept(s, body.face_ids)
    s.commit()
    jobs.enqueue("estimate")
    return BatchOut(batch_id=batch)


@router.post("/faces/reject", response_model=BatchOut)
def reject_faces(body: FaceIds, s: Session = Depends(get_session)):
    batch = people.reject(s, body.face_ids)
    s.commit()
    jobs.enqueue("estimate")
    return BatchOut(batch_id=batch)


@router.get("/faces/review", response_model=list[ReviewGroup])
def review_faces(s: Session = Depends(get_session)):
    """Automatic names and suggestions waiting for a yes/no, grouped by person."""
    faces = list(s.exec(
        select(Face).where(
            Face.ignored == False,  # noqa: E712
            ((Face.source == "auto") & (Face.person_id != None)) | (Face.suggested_person_id != None),  # noqa: E711
        )
    ).all())
    names = _names(s)
    groups: dict[int, list[Face]] = {}
    for f in faces:
        groups.setdefault(f.person_id or f.suggested_person_id, []).append(f)
    out = []
    for pid, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        members.sort(key=lambda f: -(f.confidence or 0))
        out.append(ReviewGroup(person_id=pid, name=names[pid], faces=_faces_out(s, members)))
    return out


@router.post("/runs/{run_id}/revert", response_model=BatchOut)
def revert_run(run_id: str, s: Session = Depends(get_session)):
    try:
        batch = people.revert_run(s, run_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    s.commit()
    return BatchOut(batch_id=batch)


@router.post("/photos/{photo_id}/faces", response_model=FaceOut)
def draw_face(photo_id: int, body: DrawIn, s: Session = Depends(get_session)):
    p = s.get(Photo, photo_id)
    if p is None:
        raise HTTPException(404, "photo not found")
    if not (0 <= body.x < 1 and 0 <= body.y < 1 and 0 < body.w <= 1 and 0 < body.h <= 1):
        raise HTTPException(422, "box must be inside the photo")
    person_id, changes = body.person_id, []
    if body.name and body.name.strip():
        person, changes = people.person_for_name(s, body.name)
        person_id = person.id
    f = Face(photo_id=p.id, x=body.x, y=body.y, w=body.w, h=body.h, frame_rotation=p.rotation,
             source="drawn", person_id=person_id)
    editlog.record(s, [*changes, (f, editlog.CREATED, True)], "draw a face")
    s.commit()
    jobs.enqueue("faces")  # computes its embedding in the worker, then re-infers
    return face_out(f, p, _names(s))


@router.delete("/faces/{face_id}")
def delete_face(face_id: int, s: Session = Depends(get_session)):
    """Drawn faces can be deleted; detected ones are ignored instead (so re-detection won't bring them back)."""
    f = s.get(Face, face_id)
    if f is None:
        raise HTTPException(404, "face not found")
    if f.source == "drawn":
        editlog.record(s, [(f, editlog.DELETED, True)], "delete a drawn face")
        s.commit()
        return {"deleted": True}
    people.set_ignored(s, [face_id], True)
    s.commit()
    return {"deleted": False, "ignored": True}


@router.get("/face/{face_id}.jpg")
def face_image(face_id: int, s: Session = Depends(get_session)):
    f = s.get(Face, face_id)
    if f is None:
        raise HTTPException(404, "face not found")
    path = people.face_crop(f, s.get(Photo, f.photo_id))
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/faces/stats")
def face_stats(s: Session = Depends(get_session)):
    def n(q) -> int:
        return s.exec(select(func.count()).select_from(q.subquery())).one()

    live = select(Face)
    return {
        "faces": n(live),
        "named": n(live.where(Face.person_id != None)),  # noqa: E711
        "ignored": n(live.where(Face.ignored == True)),  # noqa: E712
        "unnamed_in_clusters": n(live.where(Face.person_id == None, Face.ignored == False, Face.cluster_id != None)),  # noqa: E711,E712
        "unnamed_single": n(live.where(Face.person_id == None, Face.ignored == False, Face.cluster_id == None)),  # noqa: E711,E712
        "to_review": n(live.where(Face.ignored == False, ((Face.source == "auto") & (Face.person_id != None)) | (Face.suggested_person_id != None))),  # noqa: E711,E712
        "people": n(select(Person)),
    }
