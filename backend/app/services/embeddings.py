"""Client for the OpenAI-compatible embeddings endpoint.

Async on purpose: the FAQ gate calls this from inside the Pipecat frame task,
on the same event loop as the audio, so a blocking request here would stall the
call. The client is module-level and reused — a fresh TLS handshake per turn
would eat the latency budget on its own.

Two callers with opposite priorities:

- ingestion embeds many chunks and can afford to wait and retry;
- the call path embeds one utterance under a hard sub-second budget, where a
  retry costs more than the LLM fallback it is trying to avoid.

Hence the per-call ``timeout`` and ``retries`` overrides.
"""

import asyncio

import httpx
from loguru import logger

from app.config import settings

# Texts per request during ingestion. The call path always sends one.
EMBED_BATCH = 32

_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_BACKOFF_SECONDS = (0.25, 0.75)

_client: httpx.AsyncClient | None = None


class EmbeddingError(RuntimeError):
    """The embeddings endpoint was unreachable or returned something unusable."""


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.embedding_timeout_seconds, connect=3.0)
        )
    return _client


async def close() -> None:
    """Release the shared connection pool. Called from the app lifespan."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def is_configured() -> bool:
    return bool(settings.embedding_api_url and settings.embedding_api_key)


async def _post_batch(texts: list[str], timeout: float | None, retries: int) -> list[list[float]]:
    payload = {"model": settings.embedding_model_name, "input": texts}
    headers = {"Authorization": f"Bearer {settings.embedding_api_key}"}
    request_timeout = timeout if timeout is not None else settings.embedding_timeout_seconds

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        if attempt:
            await asyncio.sleep(_BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)])
        try:
            response = await _get_client().post(
                settings.embedding_api_url,
                json=payload,
                headers=headers,
                timeout=request_timeout,
            )
            if response.status_code in _RETRY_STATUS:
                # never log headers or the body — the key is in one and the
                # provider may echo request content in the other
                last_error = EmbeddingError(f"embeddings API returned {response.status_code}")
                logger.warning(f"Embeddings API {response.status_code}, attempt {attempt + 1}")
                continue
            response.raise_for_status()
            return _parse(response.json(), len(texts))
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_error = exc
            logger.warning(
                f"Embeddings API transport error, attempt {attempt + 1}: {type(exc).__name__}"
            )
        except EmbeddingError:
            raise
        except httpx.HTTPStatusError as exc:
            # 4xx that isn't in the retry set: a bad key or model name, so
            # retrying just repeats the mistake
            raise EmbeddingError(f"embeddings API returned {exc.response.status_code}") from exc

    raise EmbeddingError(f"embeddings API unreachable after {retries + 1} attempts") from last_error


def _parse(body: dict, expected: int) -> list[list[float]]:
    data = body.get("data")
    if not isinstance(data, list) or len(data) != expected:
        raise EmbeddingError(f"expected {expected} embeddings, got {len(data or [])}")

    # Order is not guaranteed by the OpenAI contract — sort by index rather
    # than trusting the response order, or chunks get the wrong vectors.
    try:
        ordered = sorted(data, key=lambda item: item["index"])
    except (KeyError, TypeError) as exc:
        raise EmbeddingError("embeddings response items are missing an index") from exc

    vectors = []
    for item in ordered:
        vector = item.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise EmbeddingError("embeddings response item has no embedding")
        if len(vector) != settings.embedding_dim:
            raise EmbeddingError(
                f"embedding has {len(vector)} dimensions, expected {settings.embedding_dim} "
                f"— EMBEDDING_DIM and the vector() column must match the model"
            )
        vectors.append([float(x) for x in vector])
    return vectors


async def embed_texts(
    texts: list[str],
    *,
    timeout: float | None = None,
    retries: int = 2,
) -> list[list[float]]:
    """Embed many texts, in input order. Batched; used by ingestion."""
    if not texts:
        return []
    if not is_configured():
        raise EmbeddingError("EMBEDDING_API_URL and EMBEDDING_API_KEY are not set")

    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start : start + EMBED_BATCH]
        vectors.extend(await _post_batch(batch, timeout, retries))
    return vectors


async def embed_one(
    text: str,
    *,
    timeout: float | None = None,
    retries: int = 0,
) -> list[float]:
    """Embed a single string. Defaults to no retry — this is the call path."""
    vectors = await embed_texts([text], timeout=timeout, retries=retries)
    return vectors[0]
