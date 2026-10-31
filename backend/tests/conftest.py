import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://medai:medai@localhost:5432/medai_test")

# Tests must be hermetic: never make a real call to a paid/rate-limited external LLM, even if
# the developer's local .env has a real key in it. Force these blank for the test process
# regardless of .env content — tests that want LLM behavior use a Fake*LLMProvider instead.
os.environ["GEMINI_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import models  # noqa: F401 — registers tables on Base.metadata
from app.db.session import Base, engine, get_db
from app.main import app


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema():
    async with engine.begin() as conn:
        # knowledge_chunks.embedding is a pgvector column (Base.metadata.create_all doesn't
        # run CREATE EXTENSION for us, unlike the Alembic migration used against the real DB).
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session():
    async with engine.connect() as conn:
        trans = await conn.begin()
        session_factory = async_sessionmaker(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        async with session_factory() as session:
            yield session
        await trans.rollback()


@pytest_asyncio.fixture
async def client(db_session):
    from app.providers.embeddings import get_embedding_provider
    from tests.fakes import FakeEmbeddingProvider

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
