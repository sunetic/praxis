"""Page artifacts. Authoring conversations use the shared native Run API."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import Page, PageRelease
from app.services.agent.persistence import run_db
from app.services.function.native_authoring import AuthoringError
from app.services.page.contracts import PageSource, RevisionHash
from app.services.page.native_authoring import PageAuthoringStore
from app.services.page.validation import validate_page

router = APIRouter(prefix="/pages", tags=["Pages"])


class CreatePage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="New Page", min_length=1, max_length=255)
    description: str | None = None


class UpdatePage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class SavePage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: RevisionHash
    source: PageSource


class CheckPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: RevisionHash


class PublishPage(CheckPage):
    validation_id: str = Field(min_length=1)


def serialize(record):
    return json.loads(
        json.dumps(
            {column.name: getattr(record, column.name) for column in record.__table__.columns},
            default=str,
        )
    )


def get(db, page_id):
    page = db.get(Page, page_id)
    if page is None:
        raise HTTPException(404, "Page not found")
    return page


def store(request):
    return PageAuthoringStore(request.app.state.agent_runtime.sessions)


async def operation(method, *args, **kwargs):
    try:
        return await run_db(method, *args, **kwargs)
    except AuthoringError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("")
def list_pages(db: Session = Depends(get_db)):
    return [serialize(page) for page in db.query(Page).order_by(Page.updated_at.desc()).all()]


@router.get("/navigation")
def navigation(db: Session = Depends(get_db)):
    pages = db.query(Page).filter(Page.status != "archived").order_by(Page.updated_at.desc()).all()
    return [
        {
            "id": page.id,
            "name": page.name,
            "status": page.status,
            "path": f"/page/{page.id}" if page.current_release_id else f"/page/workspace/{page.id}",
            "entry_type": "published" if page.current_release_id else "workspace",
            "current_release_id": page.current_release_id,
            "updated_at": str(page.updated_at),
        }
        for page in pages
    ]


@router.post("", status_code=201)
def create_page(payload: CreatePage, db: Session = Depends(get_db)):
    page = Page(**payload.model_dump(), status="draft", draft_payload={"files": {}, "bindings": {}})
    db.add(page)
    db.commit()
    db.refresh(page)
    return serialize(page)


@router.get("/{page_id}")
def read_page(page_id: int, db: Session = Depends(get_db)):
    return serialize(get(db, page_id))


@router.patch("/{page_id}")
def update_page(page_id: int, payload: UpdatePage, db: Session = Depends(get_db)):
    page = get(db, page_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "name" and value is None:
            raise HTTPException(422, "name must not be null")
        setattr(page, key, value)
    db.commit()
    db.refresh(page)
    return serialize(page)


@router.get("/{page_id}/draft")
async def read_draft(page_id: int, request: Request):
    return await operation(store(request).read, page_id)


@router.put("/{page_id}/draft")
async def write_draft(page_id: int, payload: SavePage, request: Request):
    return await operation(
        store(request).write,
        page_id,
        expected_revision=payload.expected_revision,
        source=payload.source,
        run_id=None,
    )


@router.post("/{page_id}/validate")
async def validate_draft(page_id: int, payload: CheckPage, request: Request):
    try:
        return await validate_page(
            store(request), page_id, expected_revision=payload.expected_revision, run_id=None
        )
    except AuthoringError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{page_id}/preview")
async def preview(page_id: int, revision_id: str, request: Request):
    # JSON transport only; the client must use an opaque sandboxed frame.
    return await operation(store(request).preview, page_id, revision_id=revision_id)


@router.post("/{page_id}/publish")
async def publish(page_id: int, payload: PublishPage, request: Request):
    return await operation(store(request).publish, page_id, **payload.model_dump())


@router.get("/{page_id}/releases")
def releases(page_id: int, db: Session = Depends(get_db)):
    get(db, page_id)
    return [
        serialize(release)
        for release in db.query(PageRelease)
        .filter(PageRelease.page_id == page_id)
        .order_by(PageRelease.version.desc())
        .all()
    ]


@router.get("/{page_id}/published")
def published(page_id: int, db: Session = Depends(get_db)):
    page = get(db, page_id)
    release = db.get(PageRelease, page.current_release_id) if page.current_release_id else None
    if not release or page.status == "archived":
        raise HTTPException(404, "Published Page not found")
    return {
        "page": {"id": page.id, "name": page.name, "status": page.status},
        "release": serialize(release),
    }


@router.post("/{page_id}/archive")
def archive(page_id: int, db: Session = Depends(get_db)):
    page = get(db, page_id)
    page.status = "archived"
    db.commit()
    return {"page_id": page_id, "status": "archived"}


@router.delete("/{page_id}", status_code=204)
def delete(page_id: int, db: Session = Depends(get_db)):
    db.delete(get(db, page_id))
    db.commit()
