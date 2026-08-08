"""The embeddings client: batching, ordering, validation, retry policy."""

import json

import httpx
import pytest

from app.config import settings
from app.services import embeddings
from app.services.embeddings import EmbeddingError
from tests.conftest import FAKE_EMBEDDING_URL, fake_vector


async def test_embeds_in_input_order(fake_embeddings):
    vectors = await embeddings.embed_texts(["alpha", "beta"])
    assert vectors == [fake_vector("alpha"), fake_vector("beta")]


async def test_batches_large_inputs(fake_embeddings):
    texts = [f"text number {i}" for i in range(embeddings.EMBED_BATCH + 5)]
    vectors = await embeddings.embed_texts(texts)

    assert len(vectors) == len(texts)
    assert fake_embeddings.calls.call_count == 2


async def test_response_is_sorted_by_index(fake_embeddings):
    """Order is not guaranteed by the API, so chunks must not trust it."""

    def shuffled(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        data = [
            {"object": "embedding", "index": i, "embedding": fake_vector(t)}
            for i, t in enumerate(inputs)
        ]
        return httpx.Response(200, json={"data": list(reversed(data))})

    fake_embeddings.post(FAKE_EMBEDDING_URL).mock(side_effect=shuffled)

    vectors = await embeddings.embed_texts(["alpha", "beta", "gamma"])
    assert vectors == [fake_vector("alpha"), fake_vector("beta"), fake_vector("gamma")]


async def test_wrong_dimension_is_rejected(fake_embeddings):
    fake_embeddings.post(FAKE_EMBEDDING_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}
        )
    )
    with pytest.raises(EmbeddingError, match="dimensions"):
        await embeddings.embed_one("alpha")


async def test_short_response_is_rejected(fake_embeddings):
    fake_embeddings.post(FAKE_EMBEDDING_URL).mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    with pytest.raises(EmbeddingError, match="expected 2 embeddings"):
        await embeddings.embed_texts(["alpha", "beta"])


async def test_server_errors_are_retried_then_raise(fake_embeddings):
    route = fake_embeddings.post(FAKE_EMBEDDING_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(EmbeddingError):
        await embeddings.embed_texts(["alpha"], retries=2)
    assert route.call_count == 3


async def test_call_path_does_not_retry(fake_embeddings):
    """A retry on the call path costs more than the LLM fallback it avoids."""
    route = fake_embeddings.post(FAKE_EMBEDDING_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(EmbeddingError):
        await embeddings.embed_one("alpha")
    assert route.call_count == 1


async def test_client_errors_are_not_retried(fake_embeddings):
    """A bad key or model name will not fix itself on the second attempt."""
    route = fake_embeddings.post(FAKE_EMBEDDING_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(EmbeddingError, match="401"):
        await embeddings.embed_texts(["alpha"], retries=2)
    assert route.call_count == 1


async def test_sends_bearer_auth_and_model(fake_embeddings):
    await embeddings.embed_one("alpha")

    request = fake_embeddings.calls.last.request
    assert request.headers["authorization"] == "Bearer test-key"
    assert json.loads(request.content)["model"] == "fake-embed"


async def test_unconfigured_raises_rather_than_calling_out(monkeypatch, fake_embeddings):
    monkeypatch.setattr(settings, "embedding_api_key", "")
    assert embeddings.is_configured() is False
    with pytest.raises(EmbeddingError, match="not set"):
        await embeddings.embed_one("alpha")
    assert fake_embeddings.calls.call_count == 0


async def test_empty_input_short_circuits(fake_embeddings):
    assert await embeddings.embed_texts([]) == []
    assert fake_embeddings.calls.call_count == 0


async def test_api_key_never_reaches_the_logs(fake_embeddings, caplog):
    """The key is in every request header; it must not be in any log record."""
    fake_embeddings.post(FAKE_EMBEDDING_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(EmbeddingError):
        await embeddings.embed_texts(["alpha"], retries=1)

    assert "test-key" not in caplog.text
