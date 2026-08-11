"""Conexão com o banco.

Padrão é SQLite (arquivo único, zero setup: você clona e roda). Como a URL vem
da configuração, trocar para Postgres é mudar uma variável de ambiente::

    FLUXOR_DATABASE_URL=postgresql+asyncpg://user:senha@host/fluxor
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fluxor.logging_setup import get_logger
from fluxor.storage.models import Base

log = get_logger("fluxor.db")


class Database:
    """Engine + fábrica de sessões, com o ciclo de vida explícito."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        self._ensure_parent_dir(url)

        self.engine: AsyncEngine = create_async_engine(
            url, echo=echo, pool_pre_ping=True, future=True
        )
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

        if url.startswith("sqlite"):
            self._configure_sqlite()

    @staticmethod
    def _ensure_parent_dir(url: str) -> None:
        """Cria a pasta do arquivo .db se o caminho apontar para um subdiretório."""
        if not url.startswith("sqlite"):
            return
        _, _, location = url.partition(":///")
        if location and location != ":memory:":
            Path(location).expanduser().parent.mkdir(parents=True, exist_ok=True)

    def _configure_sqlite(self) -> None:
        """WAL + foreign keys.

        Sem WAL, um `fluxor run` no terminal e o dashboard lendo ao mesmo tempo
        disputam o mesmo lock e um dos dois recebe "database is locked".
        """

        @event.listens_for(self.engine.sync_engine, "connect")
        def _set_pragmas(connection: Any, _record: Any) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    async def create_all(self) -> None:
        """Cria o schema se ainda não existir."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        log.debug("schema_pronto", url=self.url)

    async def dispose(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Sessão transacional: commit no sucesso, rollback em qualquer erro."""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
