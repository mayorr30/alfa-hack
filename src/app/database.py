"""Подключение к БД: асинхронный движок SQLAlchemy и сессия на один запрос."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .settings import settings

engine = create_async_engine(settings.database_url, echo=settings.sql_echo, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: открывает сессию на время запроса и закрывает после."""
    async with SessionLocal() as session:
        yield session
