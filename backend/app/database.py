from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import get_settings

_engine = None
_AsyncSessionLocal = None

Base = declarative_base()


def get_engine():
    """Lazily create async engine to avoid creating it before event loop exists.

    Creating an async engine at module import time breaks SQLAlchemy's greenlet
    integration when the module is imported by Celery workers (before an event
    loop is running).
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            future=True,
        )
    return _engine


def get_async_session_maker():
    """Lazily create async session maker bound to the engine."""
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _AsyncSessionLocal = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _AsyncSessionLocal


async def get_db():
    async with get_async_session_maker()() as session:
        try:
            yield session
        finally:
            await session.close()
