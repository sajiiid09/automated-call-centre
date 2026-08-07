import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.services import telephony

TEST_DB = "callcentre_test"


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
    from app.services import call_session, campaign_runner

    factory = lambda: _SharedSession(db)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(call_session, "SessionLocal", factory)
    monkeypatch.setattr(campaign_runner, "SessionLocal", factory)
    return db
