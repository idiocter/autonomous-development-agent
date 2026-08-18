from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings

# NullPool: asyncpg connections aren't safe to reuse across event loop
# instances, and pooled connections have been observed to come back from
# the pool in a broken "another operation is in progress" state under
# pytest-asyncio. A fresh connection per session avoids that whole class of
# bug; the cost (no connection reuse) is negligible for this project's
# scale (a handful of jobs at a time, not a high-throughput API).
engine = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
