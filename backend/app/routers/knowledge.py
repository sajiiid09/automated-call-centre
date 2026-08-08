"""Knowledge base admin: agent identity, FAQs, documents, and a search probe.

Thin by design — parsing, chunking, embedding and retrieval all live in
services/knowledge_ingest.py and services/knowledge.py.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Faq, KbDocument
from app.schemas import (
    AgentProfileOut,
    AgentProfileUpdate,
    ChunkHitOut,
    FaqCreate,
    FaqMatchOut,
    FaqOut,
    FaqUpdate,
    KbDocumentOut,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.services import knowledge, knowledge_ingest

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _faq_out(faq: Faq) -> FaqOut:
    return FaqOut.model_validate(faq).model_copy(update={"indexed": faq.embedding is not None})


def _faq_or_404(db: Session, faq_id: uuid.UUID) -> Faq:
    faq = db.get(Faq, faq_id)
    if faq is None:
        raise HTTPException(404, "FAQ not found")
    return faq


def _document_or_404(db: Session, document_id: uuid.UUID) -> KbDocument:
    document = db.get(KbDocument, document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    return document


# --- agent profile --------------------------------------------------------


@router.get("/profile", response_model=AgentProfileOut)
def get_profile(db: Session = Depends(get_db)):
    return knowledge.get_or_create_profile(db)


@router.patch("/profile", response_model=AgentProfileOut)
def update_profile(payload: AgentProfileUpdate, db: Session = Depends(get_db)):
    profile = knowledge.get_or_create_profile(db)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, key, value)
    db.commit()
    db.refresh(profile)
    return profile


# --- FAQs -----------------------------------------------------------------


@router.get("/faqs", response_model=list[FaqOut])
def list_faqs(db: Session = Depends(get_db)):
    return [_faq_out(f) for f in knowledge.list_faqs(db)]


@router.post("/faqs", response_model=FaqOut, status_code=201)
async def create_faq(payload: FaqCreate, db: Session = Depends(get_db)):
    faq = Faq(**payload.model_dump())
    db.add(faq)
    db.commit()
    db.refresh(faq)
    await _try_embed(faq.id)
    db.refresh(faq)
    return _faq_out(faq)


@router.patch("/faqs/{faq_id}", response_model=FaqOut)
async def update_faq(faq_id: uuid.UUID, payload: FaqUpdate, db: Session = Depends(get_db)):
    faq = _faq_or_404(db, faq_id)
    changes = payload.model_dump(exclude_unset=True)
    # Only the question is embedded, so editing an answer or toggling `enabled`
    # must not spend an embeddings call.
    requires_embedding = "question" in changes and changes["question"] != faq.question

    for key, value in changes.items():
        setattr(faq, key, value)
    db.commit()

    if requires_embedding:
        await _try_embed(faq.id)
    db.refresh(faq)
    return _faq_out(faq)


@router.delete("/faqs/{faq_id}", status_code=204)
def delete_faq(faq_id: uuid.UUID, db: Session = Depends(get_db)):
    db.delete(_faq_or_404(db, faq_id))
    db.commit()


async def _try_embed(faq_id: uuid.UUID) -> None:
    """Embed an FAQ, but never fail the write because the API is down.

    An unembedded FAQ is inert rather than broken: it simply never matches, and
    the row shows `indexed: false` in the dashboard until it is retried.
    """
    if not knowledge.embeddings.is_configured():
        return
    try:
        await knowledge.embed_faq(faq_id)
    except Exception as exc:  # noqa: BLE001 — surfaced via `indexed`, not a 500
        from loguru import logger

        logger.warning(f"Could not embed FAQ {faq_id}: {type(exc).__name__}")


# --- documents ------------------------------------------------------------


@router.get("/documents", response_model=list[KbDocumentOut])
def list_documents(db: Session = Depends(get_db)):
    return knowledge.list_documents(db)


@router.post("/documents", response_model=KbDocumentOut, status_code=202)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile,
    db: Session = Depends(get_db),
):
    filename = file.filename or "document"
    raw = await file.read()
    if len(raw) > settings.kb_max_upload_bytes:
        raise HTTPException(
            400, f"File is larger than the {settings.kb_max_upload_bytes} byte limit"
        )

    try:
        content = knowledge_ingest.extract_text(filename, raw)
    except knowledge_ingest.UnsupportedDocument as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 — a corrupt PDF is a 400, not a 500
        raise HTTPException(400, f"Could not read {filename}: {exc}")

    document = KbDocument(
        title=filename.rsplit(".", 1)[0] or filename,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(raw),
        content=content,
        status="pending",
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    # Embedding a long document is dozens of round trips; the client polls
    # GET /documents for the status rather than holding the connection open.
    background.add_task(knowledge_ingest.ingest_document, document.id)
    return document


@router.post("/documents/{document_id}/reindex", response_model=KbDocumentOut, status_code=202)
def reindex_document(
    document_id: uuid.UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    document = _document_or_404(db, document_id)
    document.status = "pending"
    document.error = None
    db.commit()
    db.refresh(document)
    background.add_task(knowledge_ingest.ingest_document, document.id)
    return document


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, db: Session = Depends(get_db)):
    db.delete(_document_or_404(db, document_id))
    db.commit()


# --- search probe ---------------------------------------------------------


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(payload: KnowledgeSearchRequest, db: Session = Depends(get_db)):
    """What the agent would find for this utterance, and whether it would speak.

    Returns the top FAQ regardless of threshold so near-misses are visible —
    this is how you tune faq_threshold without making phone calls.
    """
    profile = knowledge.get_or_create_profile(db)
    threshold = profile.faq_threshold
    top_k = payload.top_k or profile.rag_top_k

    try:
        faq, chunks = await knowledge.search(payload.query, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 — report the cause, don't 500
        raise HTTPException(502, f"Embeddings lookup failed: {exc}")

    return KnowledgeSearchResponse(
        faq=FaqMatchOut(id=faq.id, question=faq.question, answer=faq.answer, score=faq.score)
        if faq
        else None,
        threshold=threshold,
        would_bypass_llm=bool(faq and faq.score >= threshold),
        chunks=[
            ChunkHitOut(title=c.title, content=c.content, score=c.score)
            for c in chunks
            if c.score >= profile.rag_min_score
        ],
    )
