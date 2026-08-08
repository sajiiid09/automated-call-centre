"""The /api/knowledge surface: profile, FAQs, documents, and the search probe."""

import pytest

from tests.test_knowledge_ingest import minimal_pdf

# TestClient runs BackgroundTasks synchronously once the response is returned,
# so an upload is fully indexed by the time the request completes.


@pytest.fixture()
def client(client, shared_session):
    """The shared client, with background session factories bound to the test
    transaction.

    Ingestion and FAQ embedding run off-request and open their own
    SessionLocal, which is a different connection and cannot see rows the
    request has only written inside the fixture's transaction.
    """
    return client


# --- agent profile --------------------------------------------------------


def test_profile_is_created_on_first_read_and_is_a_singleton(client):
    first = client.get("/api/knowledge/profile")
    second = client.get("/api/knowledge/profile")

    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["company_name"] == "the company"
    assert "$company_name" in first.json()["greeting_template"]


def test_profile_update_persists(client):
    resp = client.patch(
        "/api/knowledge/profile",
        json={
            "company_name": "Acme Utilities",
            "greeting_template": "Thanks for calling $company_name.",
            "faq_threshold": 0.9,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["company_name"] == "Acme Utilities"
    assert resp.json()["faq_threshold"] == 0.9
    assert client.get("/api/knowledge/profile").json()["company_name"] == "Acme Utilities"


def test_profile_rejects_an_out_of_range_threshold(client):
    assert client.patch("/api/knowledge/profile", json={"faq_threshold": 1.4}).status_code == 422


# --- FAQs -----------------------------------------------------------------


def _create_faq(client, question="What are your opening hours?", answer="Nine to five."):
    return client.post("/api/knowledge/faqs", json={"question": question, "answer": answer})


def test_faq_is_embedded_on_create(client, fake_embeddings):
    resp = _create_faq(client)

    assert resp.status_code == 201
    assert resp.json()["indexed"] is True
    assert fake_embeddings.calls.call_count == 1


def test_editing_only_the_answer_does_not_re_embed(client, fake_embeddings):
    faq_id = _create_faq(client).json()["id"]
    calls_after_create = fake_embeddings.calls.call_count

    resp = client.patch(f"/api/knowledge/faqs/{faq_id}", json={"answer": "Nine to six."})

    assert resp.status_code == 200
    assert resp.json()["answer"] == "Nine to six."
    assert fake_embeddings.calls.call_count == calls_after_create


def test_editing_the_question_re_embeds(client, fake_embeddings):
    faq_id = _create_faq(client).json()["id"]
    calls_after_create = fake_embeddings.calls.call_count

    client.patch(f"/api/knowledge/faqs/{faq_id}", json={"question": "When do you open?"})

    assert fake_embeddings.calls.call_count == calls_after_create + 1


def test_faq_crud(client):
    faq_id = _create_faq(client).json()["id"]
    assert len(client.get("/api/knowledge/faqs").json()) == 1

    assert client.delete(f"/api/knowledge/faqs/{faq_id}").status_code == 204
    assert client.get("/api/knowledge/faqs").json() == []
    assert client.delete(f"/api/knowledge/faqs/{faq_id}").status_code == 404


def test_faq_answer_length_is_capped(client):
    """The answer is spoken verbatim, so an essay is a bug, not a feature."""
    resp = _create_faq(client, answer="x" * 700)
    assert resp.status_code == 422


def test_faq_survives_an_embeddings_outage(client, fake_embeddings):
    """An unembedded FAQ is inert, not a failed write."""
    import httpx

    fake_embeddings.post("http://embeddings.test/v1/embeddings").mock(
        return_value=httpx.Response(500)
    )
    resp = _create_faq(client)

    assert resp.status_code == 201
    assert resp.json()["indexed"] is False


# --- documents ------------------------------------------------------------


def _upload(client, name="handbook.txt", body=b"We are open nine to five on weekdays."):
    return client.post("/api/knowledge/documents", files={"file": (name, body, "text/plain")})


def test_upload_indexes_the_document(client):
    resp = _upload(client)

    assert resp.status_code == 202
    document_id = resp.json()["id"]

    listed = client.get("/api/knowledge/documents").json()
    assert len(listed) == 1
    assert listed[0]["id"] == document_id
    assert listed[0]["status"] == "ready"
    assert listed[0]["chunk_count"] > 0


def test_pdf_upload(client):
    resp = client.post(
        "/api/knowledge/documents",
        files={
            "file": (
                "policy.pdf",
                minimal_pdf(["Refunds take fourteen days to process."]),
                "application/pdf",
            )
        },
    )
    assert resp.status_code == 202
    assert client.get("/api/knowledge/documents").json()[0]["status"] == "ready"


def test_unsupported_type_is_rejected(client):
    resp = client.post(
        "/api/knowledge/documents",
        files={"file": ("contract.docx", b"binary", "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert ".pdf" in resp.json()["detail"]


def test_oversized_upload_is_rejected(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "kb_max_upload_bytes", 10)
    resp = _upload(client, body=b"x" * 100)

    assert resp.status_code == 400
    assert "limit" in resp.json()["detail"]


def test_reindex_and_delete(client):
    document_id = _upload(client).json()["id"]

    assert client.post(f"/api/knowledge/documents/{document_id}/reindex").status_code == 202
    assert client.get("/api/knowledge/documents").json()[0]["status"] == "ready"

    assert client.delete(f"/api/knowledge/documents/{document_id}").status_code == 204
    assert client.get("/api/knowledge/documents").json() == []


# --- search probe ---------------------------------------------------------


def test_search_matches_a_paraphrased_faq(client):
    _create_faq(client, question="What are your opening hours?", answer="Nine to five.")
    client.patch("/api/knowledge/profile", json={"faq_threshold": 0.3})

    body = client.post("/api/knowledge/search", json={"query": "what are your hours"}).json()

    assert body["faq"]["answer"] == "Nine to five."
    assert body["faq"]["score"] >= body["threshold"]
    assert body["would_bypass_llm"] is True


def test_search_reports_a_near_miss_instead_of_hiding_it(client):
    """The top FAQ comes back below threshold too — that is how you tune it."""
    _create_faq(client, question="What are your opening hours?", answer="Nine to five.")
    client.patch("/api/knowledge/profile", json={"faq_threshold": 0.99})

    body = client.post("/api/knowledge/search", json={"query": "what are your hours"}).json()

    assert body["faq"] is not None
    assert body["faq"]["score"] < body["threshold"]
    assert body["would_bypass_llm"] is False


def test_search_returns_document_chunks(client):
    _upload(client, body=b"Refunds take fourteen days to process after approval.")

    body = client.post("/api/knowledge/search", json={"query": "refunds take fourteen days"}).json()

    assert body["faq"] is None
    assert body["chunks"]
    assert "Refunds" in body["chunks"][0]["content"]


def test_search_reports_an_embeddings_outage_as_502(client, fake_embeddings):
    import httpx

    fake_embeddings.post("http://embeddings.test/v1/embeddings").mock(
        return_value=httpx.Response(500)
    )
    resp = client.post("/api/knowledge/search", json={"query": "anything"})

    assert resp.status_code == 502
