"""Aplicação FastAPI: API REST + dashboard.

O ciclo de vida (`lifespan`) é onde tudo se conecta: banco, repositório, motor,
carga dos workflows e, se ligado, o agendador. Ao desligar, tudo é encerrado
na ordem inversa. Nada de estado global solto pelo módulo.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from fluxor import __version__
from fluxor.api.routes import router
from fluxor.config import get_settings
from fluxor.engine import Engine
from fluxor.loader import load_workflow_dir
from fluxor.logging_setup import configure_logging, get_logger
from fluxor.storage import Database, RunRepository

log = get_logger("fluxor.api")
STATIC_DIR = Path(__file__).parent / "static"


def reload_workflows(application: FastAPI) -> int:
    """(Re)lê a pasta de workflows para dentro do `app.state`.

    Erro de YAML aqui não derruba a API: os workflows que já estavam carregados
    continuam servindo e o problema aparece no log e no campo `error` do /health.
    """
    settings = get_settings()
    try:
        pairs = load_workflow_dir(settings.workflows_dir)
    except Exception as exc:
        log.error("carga_workflows_falhou", error=str(exc))
        application.state.workflows_error = str(exc)
        return len(getattr(application.state, "workflows", {}))

    application.state.workflows = {workflow.name: workflow for workflow, _ in pairs}
    application.state.workflow_paths = {workflow.name: path for workflow, path in pairs}
    application.state.workflows_error = None
    log.info("workflows_carregados", total=len(pairs))
    return len(pairs)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    database = Database(settings.database_url)
    await database.create_all()
    repository = RunRepository(database)

    application.state.settings = settings
    application.state.database = database
    application.state.repository = repository
    application.state.engine = Engine(sink=repository)
    application.state.workflows = {}
    application.state.workflow_paths = {}
    application.state.workflows_error = None
    application.state.scheduler = None

    reload_workflows(application)

    if settings.enable_scheduler:
        from fluxor.scheduler import WorkflowScheduler

        scheduler = WorkflowScheduler(application.state.engine)
        try:
            scheduler.load()
            scheduler.start()
            application.state.scheduler = scheduler
        except Exception as exc:
            log.error("agendador_nao_iniciou", error=str(exc))

    log.info("api_pronta", workflows=len(application.state.workflows), porta=settings.port)

    try:
        yield
    finally:
        if application.state.scheduler is not None:
            application.state.scheduler.shutdown()
        await database.dispose()
        log.info("api_encerrada")


def create_app() -> FastAPI:
    """Fábrica da aplicação, usada pelo uvicorn e pelos testes."""
    application = FastAPI(
        title="Fluxor",
        version=__version__,
        description=(
            "Motor de automações declarativas. Workflows em YAML, execução com "
            "retry e timeout, histórico observável."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    application.include_router(router, prefix="/api", tags=["fluxor"])

    if STATIC_DIR.is_dir():
        application.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")

    return application


def describe_state(application: FastAPI) -> dict[str, Any]:
    """Resumo do estado atual, que alimenta o /api/health."""
    scheduler = getattr(application.state, "scheduler", None)
    return {
        "status": "ok" if not application.state.workflows_error else "degraded",
        "version": __version__,
        "workflows": len(getattr(application.state, "workflows", {})),
        "scheduler": scheduler is not None,
        "scheduled_jobs": len(scheduler.describe_jobs()) if scheduler else 0,
        "error": getattr(application.state, "workflows_error", None),
    }


app = create_app()
