from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# Recycling well inside MySQL's wait_timeout (8h here) keeps the pool from
# handing out sockets the server has already closed.
#
# Deliberately NOT using pool_pre_ping: on SQLAlchemy 2.0.36 the mysql dialect
# calls dbapi_connection.ping() with no arguments, while the aiomysql async
# adapter still requires `reconnect`, so every checkout of an already-pooled
# connection raises TypeError. It only fires on reuse, so it looks fine until
# the pool warms up. Revisit if SQLAlchemy is upgraded.
#
# Both the API and the worker build this engine, so the cap is per process.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_recycle=3600,
    pool_size=10,
    max_overflow=10,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass
