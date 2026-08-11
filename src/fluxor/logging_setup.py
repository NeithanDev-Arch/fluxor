"""Logging estruturado com structlog.

Em desenvolvimento sai colorido e legível; em produção (`FLUXOR_LOG_FORMAT=json`)
sai como JSON de uma linha, pronto para Loki/Datadog/CloudWatch. Todo log de
execução carrega `run_id` e `workflow`, então dá para reconstruir uma execução
inteira com um único filtro.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_configured = False


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Configura structlog + logging padrão. Idempotente."""
    global _configured

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )
    # Bibliotecas de terceiro são barulhentas em DEBUG.
    for noisy in ("httpx", "httpcore", "apscheduler", "asyncio", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if fmt == "json":
        renderer: Any = structlog.processors.JSONRenderer()
        shared.append(structlog.processors.format_exc_info)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
        shared.append(structlog.dev.set_exc_info)

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str = "fluxor") -> Any:
    """Devolve um logger estruturado, configurando na primeira chamada."""
    if not _configured:
        configure_logging()
    return structlog.get_logger(name)
