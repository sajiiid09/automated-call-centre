"""Parsing and chunking, plus the document status lifecycle."""

import io

import pytest
from pypdf import PdfWriter

from app.models import KbChunk, KbDocument
from app.services.knowledge_ingest import (
    CHUNK_CHARS,
    CHUNK_OVERLAP,
    UnsupportedDocument,
    chunk_text,
    extract_text,
    ingest_document,
)


def minimal_pdf(lines: list[str]) -> bytes:
    """A tiny real PDF with extractable text.

    Hand-rolled rather than checked in as a binary fixture: it keeps the repo
    diffable and avoids a test-only dependency on a PDF writer.
    """
    stream = "BT /F1 12 Tf 72 720 Td 14 TL\n" + "".join(f"({t}) Tj T*\n" for t in lines) + "ET"
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
            b"/Resources<</Font<</F1 5 0 R>>>>>>"
        ),
        b"<</Length %d>>stream\n%s\nendstream" % (len(stream), stream.encode()),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj" % i + body + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def blank_pdf() -> bytes:
    """A PDF with no text layer — what a scan looks like to pypdf."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# --- extraction -----------------------------------------------------------


@pytest.mark.parametrize("name", ["notes.txt", "readme.md"])
def test_extract_plain_text(name):
    assert extract_text(name, b"We are open nine to five.") == "We are open nine to five."


def test_extract_pdf():
    text = extract_text("policy.pdf", minimal_pdf(["Refunds take fourteen days."]))
    assert "Refunds take fourteen days." in text


def test_extract_rejects_unsupported_types():
    with pytest.raises(UnsupportedDocument):
        extract_text("contract.docx", b"whatever")


def test_extract_survives_invalid_utf8():
    assert extract_text("notes.txt", b"caf\xff") != ""


# --- chunking -------------------------------------------------------------


def test_short_document_is_one_chunk():
    chunks = chunk_text("We are open nine to five, Monday to Friday, excluding holidays.")
    assert len(chunks) == 1


def test_long_document_splits_with_overlap():
    paragraphs = [f"Paragraph {i} " + ("filler words here " * 20) for i in range(12)]
    chunks = chunk_text("\n\n".join(paragraphs))

    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_CHARS * 2 for c in chunks)
    # the tail of one chunk is carried into the next, so a fact spanning the
    # boundary is still retrievable from either side
    assert chunks[1].startswith(chunks[0][-CHUNK_OVERLAP:].strip()[:60])


def test_oversized_paragraph_is_hard_split():
    chunks = chunk_text("word " * 2000)
    assert len(chunks) > 1


def test_fragments_between_real_chunks_are_dropped():
    body = "We are open nine to five every weekday of the year, holidays excepted."
    chunks = chunk_text(f"{body}\n\n7\n\n{body} Refunds take fourteen days to process.")
    assert all("7" != c.strip() for c in chunks)


def test_a_short_document_is_kept_rather_than_discarded():
    """Below the fragment threshold, but the operator uploaded it on purpose."""
    assert chunk_text("We are open nine to five.") == ["We are open nine to five."]


def test_empty_input():
    assert chunk_text("") == []


# --- lifecycle ------------------------------------------------------------


async def test_ingest_marks_document_ready(db, shared_session):
    document = KbDocument(
        title="handbook",
        filename="handbook.txt",
        content_type="text/plain",
        size_bytes=100,
        content="We are open nine to five.\n\nRefunds take fourteen days to process fully.",
    )
    db.add(document)
    db.commit()

    await ingest_document(document.id)
    db.refresh(document)

    assert document.status == "ready"
    assert document.chunk_count > 0
    assert document.error is None
    assert db.query(KbChunk).filter_by(document_id=document.id).count() == document.chunk_count


async def test_ingest_is_idempotent(db, shared_session):
    document = KbDocument(
        title="handbook",
        filename="handbook.txt",
        content_type="text/plain",
        size_bytes=100,
        content="We are open nine to five every weekday of the year.",
    )
    db.add(document)
    db.commit()

    await ingest_document(document.id)
    first = db.query(KbChunk).filter_by(document_id=document.id).count()
    await ingest_document(document.id)
    db.refresh(document)

    assert db.query(KbChunk).filter_by(document_id=document.id).count() == first
    assert document.status == "ready"


async def test_scanned_pdf_fails_with_a_useful_message(db, shared_session):
    document = KbDocument(
        title="scan",
        filename="scan.pdf",
        content_type="application/pdf",
        size_bytes=431,
        content=extract_text("scan.pdf", blank_pdf()),
    )
    db.add(document)
    db.commit()

    await ingest_document(document.id)
    db.refresh(document)

    assert document.status == "failed"
    assert "scanned" in document.error.lower()


async def test_ingest_never_raises_when_embeddings_fail(db, shared_session, fake_embeddings):
    import httpx

    fake_embeddings.post("http://embeddings.test/v1/embeddings").mock(
        return_value=httpx.Response(500)
    )
    document = KbDocument(
        title="handbook",
        filename="handbook.txt",
        content_type="text/plain",
        size_bytes=50,
        content="We are open nine to five on every weekday.",
    )
    db.add(document)
    db.commit()

    await ingest_document(document.id)  # must not raise
    db.refresh(document)

    assert document.status == "failed"
    assert document.error
