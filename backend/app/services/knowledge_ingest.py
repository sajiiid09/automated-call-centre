"""Turn an uploaded document into embedded, searchable chunks.

Runs off-request via FastAPI BackgroundTasks: a 40-page PDF is dozens of HTTP
round trips to the embeddings API, which would time out the upload and block
the worker. The handler stores the extracted text and returns 202; this module
does the slow part and moves the row through
``pending -> processing -> ready | failed``.

Because reindexing works from the stored text, the original bytes are dropped —
there is no blob store in this system and a bytea column of PDFs would have no
reader.
"""

import io
import re
import uuid

from loguru import logger
from sqlalchemy import delete

from app.db import SessionLocal
from app.models import KbChunk, KbDocument
from app.services import embeddings

SUPPORTED_EXTENSIONS = (".pdf", ".txt", ".md")

# ~300 tokens. Four of these ground an answer without inflating the per-turn
# prompt, which is the whole point of the latency budget.
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
MIN_CHUNK_CHARS = 40

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class UnsupportedDocument(ValueError):
    """The upload is not a file type we can extract text from."""


def extract_text(filename: str, raw: bytes) -> str:
    """Plain text from a supported upload. Raises UnsupportedDocument."""
    lower = filename.lower()
    if lower.endswith((".txt", ".md")):
        return raw.decode("utf-8", errors="replace")
    if lower.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    raise UnsupportedDocument(
        f"Only {', '.join(SUPPORTED_EXTENSIONS)} are supported, got {filename!r}"
    )


def _hard_split(paragraph: str) -> list[str]:
    """Break an oversized paragraph on sentences, then on raw length."""
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(paragraph):
        if len(current) + len(sentence) + 1 <= CHUNK_CHARS:
            current = f"{current} {sentence}".strip()
            continue
        if current:
            pieces.append(current)
        # a single sentence longer than a chunk: cut it bluntly
        while len(sentence) > CHUNK_CHARS:
            pieces.append(sentence[:CHUNK_CHARS])
            sentence = sentence[CHUNK_CHARS:]
        current = sentence
    if current:
        pieces.append(current)
    return pieces


def chunk_text(content: str) -> list[str]:
    """Paragraph-greedy chunks with a trailing overlap for context continuity."""
    paragraphs = [" ".join(p.split()) for p in _PARAGRAPH_SPLIT.split(content or "")]
    paragraphs = [p for p in paragraphs if p]

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        for part in _hard_split(paragraph) if len(paragraph) > CHUNK_CHARS else [paragraph]:
            if current and len(current) + len(part) + 2 > CHUNK_CHARS:
                chunks.append(current)
                # carry the tail forward so a fact split across the boundary is
                # still retrievable from the following chunk
                current = (current[-CHUNK_OVERLAP:] + "\n\n" + part).strip()
            else:
                current = f"{current}\n\n{part}".strip() if current else part
    if current:
        chunks.append(current)

    # Drop fragments — page numbers, stray headers — but only as a filter
    # between real chunks. A whole document that is simply short was uploaded
    # on purpose, and silently discarding it would report as "no text found".
    kept = [c for c in chunks if len(c.replace(" ", "")) >= MIN_CHUNK_CHARS]
    return kept or chunks


def _set_status(document_id: uuid.UUID, status: str, *, error: str | None = None) -> None:
    with SessionLocal() as db:
        document = db.get(KbDocument, document_id)
        if document is None:
            return
        document.status = status
        document.error = error
        db.commit()


async def ingest_document(document_id: uuid.UUID) -> None:
    """Chunk, embed and store one document. Never raises.

    Idempotent: existing chunks for the document are replaced, so this doubles
    as the reindex path.
    """
    try:
        with SessionLocal() as db:
            document = db.get(KbDocument, document_id)
            if document is None:
                return
            content = document.content or ""
            document.status = "processing"
            document.error = None
            db.commit()

        chunks = chunk_text(content)
        if not chunks:
            _set_status(
                document_id,
                "failed",
                error="No extractable text — is this a scanned PDF?",
            )
            return

        vectors = await embeddings.embed_texts(chunks)

        with SessionLocal() as db:
            document = db.get(KbDocument, document_id)
            if document is None:
                return
            # An explicit DELETE, not ORM cascade: the unit of work orders
            # inserts before deletes, so on reindex the new ordinal 0 would
            # collide with the old one before it was removed.
            db.execute(delete(KbChunk).where(KbChunk.document_id == document_id))
            db.flush()
            for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors)):
                db.add(
                    KbChunk(
                        document_id=document_id,
                        ordinal=ordinal,
                        content=chunk,
                        embedding=vector,
                    )
                )
            document.chunk_count = len(chunks)
            document.status = "ready"
            document.error = None
            db.commit()

        logger.info(f"Indexed document {document_id}: {len(chunks)} chunks")
    except Exception as exc:
        logger.exception(f"Ingestion failed for document {document_id}")
        _set_status(document_id, "failed", error=str(exc)[:500])
