import hashlib
import json

import httpx
import numpy as np
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import EMBEDDING_DIM
from app.services import telephony

TEST_DB = "callcentre_test"
FAKE_EMBEDDING_URL = "http://embeddings.test/v1/embeddings"


async def _no_network(*args, **kwargs):
    raise AssertionError("a real Twilio request was attempted from the test suite")


@pytest.fixture(autouse=True)
def no_real_dialing(monkeypatch):
    """Make it structurally impossible for tests to place a real call.

    `.env` holds working Twilio credentials, so nothing here may rely on the
    system being "unconfigured". Every guard is pinned off, and the single
    Twilio HTTP seam is replaced with a landmine so that even a total
    regression of the mode checks fails loudly instead of dialing someone.
    """
    monkeypatch.setattr(settings, "dialer_mode", "simulated")
    monkeypatch.setattr(settings, "dialer_supervisor_enabled", False)
    monkeypatch.setattr(settings, "public_base_url", "")
    monkeypatch.setattr(settings, "outbound_allowlist", "")
    monkeypatch.setattr(telephony, "_post_twilio", _no_network)


def fake_vector(text_in: str) -> list[float]:
    """A deterministic pseudo-embedding with meaningful geometry.

    Identical strings score 1.0, strings sharing words score high, unrelated
    strings score near zero. That is what lets threshold behaviour be asserted
    without a real model: a bag-of-words vector projected into EMBEDDING_DIM by
    hashing each word to a fixed axis.
    """
    vector = np.zeros(EMBEDDING_DIM, dtype=np.float64)
    words = [w.strip(".,!?").lower() for w in text_in.split()]
    for word in words:
        if not word:
            continue
        seed = int(hashlib.sha256(word.encode()).hexdigest()[:8], 16)
        axis = seed % EMBEDDING_DIM
        vector[axis] += 1.0
        # a little spread so near-synonyms aren't perfectly orthogonal
        vector[(axis + 1) % EMBEDDING_DIM] += 0.35

    norm = np.linalg.norm(vector)
    if norm == 0:
        vector[0] = 1.0
        norm = 1.0
    return (vector / norm).tolist()


def _fake_embeddings_response(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    inputs = payload["input"]
    if isinstance(inputs, str):
        inputs = [inputs]
    return httpx.Response(
        200,
        json={
            "object": "list",
            "model": payload.get("model", "fake-embed"),
            "data": [
                {"object": "embedding", "index": i, "embedding": fake_vector(t)}
                for i, t in enumerate(inputs)
            ],
        },
    )


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    """Make it structurally impossible for tests to hit the real embeddings API.

    Same doctrine as `no_real_dialing`: `.env` holds a working key, so nothing
    here may rely on the system being unconfigured. The URL is repointed at a
    respx-mocked host, so even a total regression of the config checks fails
    loudly instead of sending call transcripts to a third party.
    """
    monkeypatch.setattr(settings, "embedding_api_url", FAKE_EMBEDDING_URL)
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_model_name", "fake-embed")

    with respx.mock(assert_all_called=False) as mock:
        mock.post(FAKE_EMBEDDING_URL).mock(side_effect=_fake_embeddings_response)
        yield mock


@pytest.fixture(autouse=True)
async def _reset_embedding_client():
    """Drop the module-level httpx client between tests.

    It is created once and cached, but each test installs a fresh respx mock;
    a client held across that boundary keeps the previous transport.
    """
    from app.services import embeddings, knowledge

    await embeddings.close()
    knowledge._VECTOR_CACHE.clear()
    yield
    await embeddings.close()
    knowledge._VECTOR_CACHE.clear()


@pytest.fixture(scope="session")
def test_engine():
    admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    url = settings.database_url.rsplit("/", 1)[0] + f"/{TEST_DB}"
    engine = create_engine(url)
    # Tests build their schema from the models, not from Alembic, so the
    # extension the vector() columns need has to be created here too.
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def db(test_engine):
    connection = test_engine.connect()
    transaction = connection.begin()
    # create_savepoint: router-level commit()/rollback() operate on savepoints,
    # leaving the outer transaction ours to roll back for isolation
    session = sessionmaker(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class _SharedSession:
    """`with SessionLocal() as s` that yields the test session and keeps it open."""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def shared_session(db, monkeypatch):
    """Point background-task session factories at the test transaction.

    Services that run off-request open their own `SessionLocal()`, which is a
    separate connection and therefore cannot see the fixture's uncommitted
    data. Binding them to the test session keeps rollback isolation intact.
    """
    import app.db as db_module
    from app.services import (
        call_session,
        campaign_runner,
        disposition,
        knowledge,
        knowledge_ingest,
    )

    factory = lambda: _SharedSession(db)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(call_session, "SessionLocal", factory)
    monkeypatch.setattr(campaign_runner, "SessionLocal", factory)
    # disposition was a standing gap: it has always opened its own session, but
    # nothing reached it until finish() started classifying every call.
    monkeypatch.setattr(disposition, "SessionLocal", factory)
    monkeypatch.setattr(knowledge, "SessionLocal", factory)
    monkeypatch.setattr(knowledge_ingest, "SessionLocal", factory)
    return db
