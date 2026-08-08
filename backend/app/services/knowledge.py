"""Knowledge-base retrieval for the live call path.

Two consumers with very different budgets:

- ``/api/knowledge/search`` — the dashboard's debug view, latency irrelevant.
- ``lookup_turn`` — called by ``agent.faq_gate`` once per caller utterance,
  inside the audio event loop, under a hard sub-second cap.

The embedding request is awaited (async httpx) and the SQLAlchemy queries run
in a worker thread, matching how the rest of the backend touches the DB from
async code.
"""

import asyncio
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from agent.faq_gate import TurnKnowledge
from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import AgentProfile, Faq, KbDocument
from app.services import embeddings

# Utterance -> vector. Bounded and per-process. Caches the *embedding*, not the
# search result, so editing an FAQ takes effect on the next turn while repeated
# phrasings still skip the HTTP round trip.
_VECTOR_CACHE: OrderedDict[str, list[float]] = OrderedDict()
_VECTOR_CACHE_MAX = 256

# Strong refs for fire-and-forget writes, so they aren't garbage collected
# mid-flight (asyncio only holds weak references to running tasks).
_background: set[asyncio.Task] = set()


@dataclass
class ProfileSnapshot:
    """Profile values read once at call start, so no turn hits the DB for them."""

    company_name: str
    greeting_template: str
    persona: str | None
    faq_threshold: float
    rag_top_k: int
    rag_min_score: float


@dataclass
class FaqMatch:
    id: uuid.UUID
    question: str
    answer: str
    score: float


@dataclass
class ChunkHit:
    content: str
    title: str
    score: float


# --- profile --------------------------------------------------------------


def get_or_create_profile(db: Session) -> AgentProfile:
    """The singleton row, created on first read.

    The migration seeds it for real deployments; this covers the test path,
    which builds its schema from the models and so never runs that INSERT.
    """
    profile = db.get(AgentProfile, 1)
    if profile is None:
        from agent.prompts import DEFAULT_INBOUND_GREETING

        profile = AgentProfile(
            id=1,
            company_name="the company",
            greeting_template=DEFAULT_INBOUND_GREETING,
            faq_threshold=settings.faq_threshold,
            rag_top_k=settings.rag_top_k,
            rag_min_score=settings.rag_min_score,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _snapshot(db: Session) -> ProfileSnapshot:
    profile = get_or_create_profile(db)
    return ProfileSnapshot(
        company_name=profile.company_name,
        greeting_template=profile.greeting_template,
        persona=profile.persona,
        faq_threshold=profile.faq_threshold,
        rag_top_k=profile.rag_top_k,
        rag_min_score=profile.rag_min_score,
    )


def load_profile_snapshot() -> ProfileSnapshot:
    """Blocking; run via asyncio.to_thread. Called once per call."""
    with SessionLocal() as db:
        return _snapshot(db)


# --- search ---------------------------------------------------------------


def _query_faq(vector: list[float]) -> FaqMatch | None:
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                SELECT id, question, answer, 1 - (embedding <=> CAST(:vec AS vector)) AS score
                FROM faqs
                WHERE enabled AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT 1
                """
            ),
            {"vec": str(vector)},
        ).first()
    if row is None:
        return None
    return FaqMatch(id=row.id, question=row.question, answer=row.answer, score=float(row.score))


def _query_chunks(vector: list[float], top_k: int) -> list[ChunkHit]:
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                SELECT c.content, d.title,
                       1 - (c.embedding <=> CAST(:vec AS vector)) AS score
                FROM kb_chunks c
                JOIN kb_documents d ON d.id = c.document_id
                WHERE d.status = 'ready'
                ORDER BY c.embedding <=> CAST(:vec AS vector)
                LIMIT :k
                """
            ),
            {"vec": str(vector), "k": top_k},
        ).all()
    return [ChunkHit(content=r.content, title=r.title, score=float(r.score)) for r in rows]


async def _embed_cached(query: str, *, timeout: float | None, retries: int) -> list[float]:
    key = " ".join(query.lower().split())
    cached = _VECTOR_CACHE.get(key)
    if cached is not None:
        _VECTOR_CACHE.move_to_end(key)
        return cached

    vector = await embeddings.embed_one(query, timeout=timeout, retries=retries)
    _VECTOR_CACHE[key] = vector
    if len(_VECTOR_CACHE) > _VECTOR_CACHE_MAX:
        _VECTOR_CACHE.popitem(last=False)
    return vector


async def search(
    query: str,
    *,
    top_k: int | None = None,
    timeout: float | None = None,
    retries: int = 2,
) -> tuple[FaqMatch | None, list[ChunkHit]]:
    """Top FAQ and top chunks for a query. One embedding serves both."""
    vector = await _embed_cached(query, timeout=timeout, retries=retries)
    k = top_k if top_k is not None else settings.rag_top_k
    faq, chunks = await asyncio.gather(
        asyncio.to_thread(_query_faq, vector),
        asyncio.to_thread(_query_chunks, vector, k),
    )
    return faq, chunks


def _record_hit(faq_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        faq = db.get(Faq, faq_id)
        if faq is not None:
            faq.hit_count = (faq.hit_count or 0) + 1
            db.commit()


async def _lookup(text_in: str, profile: ProfileSnapshot) -> TurnKnowledge:
    # retries=0: on the call path a retry costs more than the LLM fallback it
    # is trying to avoid.
    faq, chunks = await search(text_in, top_k=profile.rag_top_k, retries=0)

    if faq is not None and faq.score >= profile.faq_threshold:
        # fire-and-forget: the caller is waiting to be spoken to, and a hit
        # counter is not worth a DB round trip on the critical path
        task = asyncio.create_task(asyncio.to_thread(_record_hit, faq.id))
        _background.add(task)
        task.add_done_callback(_background.discard)
        return TurnKnowledge(faq_answer=faq.answer, faq_id=str(faq.id), faq_score=faq.score)

    return TurnKnowledge(
        chunks=[c.content for c in chunks if c.score >= profile.rag_min_score],
    )


async def lookup_turn(text_in: str, *, profile: ProfileSnapshot) -> TurnKnowledge:
    """Call-path entry point. Fail-open and hard-capped.

    Never raises and never blocks longer than KB_TURN_TIMEOUT_SECONDS: an empty
    result just means the turn is answered by the LLM as it would have been
    before the knowledge base existed.
    """
    if not embeddings.is_configured():
        return TurnKnowledge()
    try:
        return await asyncio.wait_for(
            _lookup(text_in, profile), timeout=settings.kb_turn_timeout_seconds
        )
    except TimeoutError:
        logger.warning(
            f"Knowledge lookup exceeded {settings.kb_turn_timeout_seconds}s; "
            f"falling through to the LLM"
        )
        return TurnKnowledge()
    except Exception:
        logger.exception("Knowledge lookup failed; falling through to the LLM")
        return TurnKnowledge()


# --- FAQ embedding maintenance -------------------------------------------


async def embed_faq(faq_id: uuid.UUID) -> None:
    """(Re)embed one FAQ's question. Called after create and after an edit."""
    with SessionLocal() as db:
        faq = db.get(Faq, faq_id)
        question = faq.question if faq else None
    if not question:
        return

    vector = await embeddings.embed_one(question, retries=2)

    with SessionLocal() as db:
        faq = db.get(Faq, faq_id)
        if faq is not None:
            faq.embedding = vector
            db.commit()


def list_documents(db: Session) -> list[KbDocument]:
    return list(db.scalars(select(KbDocument).order_by(KbDocument.created_at.desc())))


def list_faqs(db: Session) -> list[Faq]:
    return list(db.scalars(select(Faq).order_by(Faq.created_at.desc())))
