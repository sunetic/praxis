import shutil
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import fmt_kv, get_logger
from app.db.database import get_db
from app.models import models
from app.schemas import schemas

router = APIRouter(prefix="/knowledge-bases", tags=["KnowledgeBases"])
logger = get_logger("api.knowledge")

settings = get_settings()
KNOWLEDGE_ROOT = Path(settings.data_dir if hasattr(settings, "data_dir") else "data") / "knowledge"


def _kb_dir(kb_id: int) -> Path:
    return KNOWLEDGE_ROOT / str(kb_id)


def _get_kb_or_404(kb_id: int, db: Session) -> models.KnowledgeBase:
    kb = db.query(models.KnowledgeBase).filter(models.KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return kb


def _to_response(kb: models.KnowledgeBase) -> dict:
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "tags": kb.tags,
        "document_count": len(kb.documents),
        "created_at": kb.created_at,
        "updated_at": kb.updated_at,
    }


@router.get("", response_model=List[schemas.KnowledgeBaseResponse])
def list_knowledge_bases(db: Session = Depends(get_db)):
    records = db.query(models.KnowledgeBase).all()
    logger.info("list_knowledge_bases %s", fmt_kv(count=len(records)))
    return [_to_response(kb) for kb in records]


@router.get("/{kb_id}", response_model=schemas.KnowledgeBaseResponse)
def get_knowledge_base(kb_id: int, db: Session = Depends(get_db)):
    kb = _get_kb_or_404(kb_id, db)
    return _to_response(kb)


@router.post("", response_model=schemas.KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
def create_knowledge_base(payload: schemas.KnowledgeBaseCreate, db: Session = Depends(get_db)):
    kb = models.KnowledgeBase(**payload.model_dump())
    db.add(kb)
    db.commit()
    db.refresh(kb)
    _kb_dir(kb.id).mkdir(parents=True, exist_ok=True)
    logger.info("create_knowledge_base %s", fmt_kv(kb_id=kb.id, name=kb.name))
    return _to_response(kb)


@router.patch("/{kb_id}", response_model=schemas.KnowledgeBaseResponse)
def update_knowledge_base(
    kb_id: int,
    payload: schemas.KnowledgeBaseUpdate,
    db: Session = Depends(get_db),
):
    kb = _get_kb_or_404(kb_id, db)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(kb, field, value)
    db.commit()
    db.refresh(kb)
    logger.info("update_knowledge_base %s", fmt_kv(kb_id=kb_id))
    return _to_response(kb)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_base(kb_id: int, db: Session = Depends(get_db)):
    kb = _get_kb_or_404(kb_id, db)
    kb_path = _kb_dir(kb_id)
    db.delete(kb)
    db.commit()
    if kb_path.exists():
        shutil.rmtree(kb_path, ignore_errors=True)
    logger.info("delete_knowledge_base %s", fmt_kv(kb_id=kb_id))
    return None


# ─── Documents ───────────────────────────────────────────────────────────────


@router.get("/{kb_id}/documents", response_model=List[schemas.KnowledgeDocumentResponse])
def list_documents(kb_id: int, db: Session = Depends(get_db)):
    _get_kb_or_404(kb_id, db)
    docs = (
        db.query(models.KnowledgeDocument)
        .filter(models.KnowledgeDocument.kb_id == kb_id)
        .order_by(models.KnowledgeDocument.created_at.desc())
        .all()
    )
    return docs


@router.post("/{kb_id}/documents", response_model=schemas.KnowledgeDocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(kb_id: int, file: UploadFile, db: Session = Depends(get_db)):
    _get_kb_or_404(kb_id, db)

    if not file.filename or not file.filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files are allowed")

    rel_path = Path(file.filename)
    if rel_path.is_absolute():
        rel_path = Path(rel_path.name)

    kb_path = _kb_dir(kb_id)
    file_path = kb_path / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)

    content = await file.read()

    counter = 1
    base_path = file_path
    while file_path.exists():
        file_path = base_path.parent / f"{base_path.stem}_{counter}.md"
        counter += 1

    file_path.write_bytes(content)

    rel_stored = file_path.relative_to(kb_path)
    title = file_path.stem.replace("-", " ").replace("_", " ")

    doc = models.KnowledgeDocument(
        kb_id=kb_id,
        title=title,
        filename=str(rel_stored),
        content_path=str(file_path),
        size_bytes=len(content),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    logger.info("upload_document %s", fmt_kv(kb_id=kb_id, doc_id=doc.id, filename=doc.filename))
    return doc


@router.get("/{kb_id}/documents/{doc_id}")
def get_document(kb_id: int, doc_id: int, db: Session = Depends(get_db)):
    _get_kb_or_404(kb_id, db)
    doc = (
        db.query(models.KnowledgeDocument)
        .filter(models.KnowledgeDocument.id == doc_id, models.KnowledgeDocument.kb_id == kb_id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    content = ""
    path = Path(doc.content_path)
    if path.exists():
        content = path.read_text(encoding="utf-8")

    return {
        "id": doc.id,
        "kb_id": doc.kb_id,
        "title": doc.title,
        "filename": doc.filename,
        "size_bytes": doc.size_bytes,
        "content": content,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
    }


@router.delete("/{kb_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(kb_id: int, doc_id: int, db: Session = Depends(get_db)):
    _get_kb_or_404(kb_id, db)
    doc = (
        db.query(models.KnowledgeDocument)
        .filter(models.KnowledgeDocument.id == doc_id, models.KnowledgeDocument.kb_id == kb_id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    path = Path(doc.content_path)
    kb_path = _kb_dir(kb_id)
    db.delete(doc)
    db.commit()
    if path.exists():
        path.unlink()
        parent = path.parent
        while parent != kb_path and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    logger.info("delete_document %s", fmt_kv(kb_id=kb_id, doc_id=doc_id))
    return None
