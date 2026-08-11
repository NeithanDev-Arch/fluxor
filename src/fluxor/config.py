"""Configuração global, lida de variáveis de ambiente e/ou `.env`.

Toda variável usa o prefixo `FLUXOR_` para não colidir com nada do sistema.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração do processo — CLI, API e agendador leem daqui."""

    model_config = SettingsConfigDict(
        env_prefix="FLUXOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    workflows_dir: Path = Field(
        default=Path("examples"),
        description="Pasta varrida em busca de arquivos .yaml de workflow.",
    )
    database_url: str = Field(
        default="sqlite+aiosqlite:///./fluxor.db",
        description="URL SQLAlchemy async. Troque por postgresql+asyncpg://... em produção.",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"

    timezone: str = "America/Sao_Paulo"
    enable_scheduler: bool = False

    http_timeout: float = Field(default=30.0, gt=0, description="Timeout padrão das actions HTTP.")
    foreach_concurrency: int = Field(
        default=5, ge=1, le=100, description="Itens processados em paralelo num `foreach`."
    )
    max_output_bytes: int = Field(
        default=64_000,
        ge=1_000,
        description="Corte aplicado ao gravar a saída de um passo no banco.",
    )

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retorna a configuração do processo (cacheada — leia sempre por aqui)."""
    return Settings()


def reset_settings_cache() -> None:
    """Limpa o cache. Usado nos testes, que trocam env vars entre casos."""
    get_settings.cache_clear()
