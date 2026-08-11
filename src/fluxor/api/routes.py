"""Rotas da API.

Tudo que o dashboard mostra vem daqui, e a mesma API serve para integrar o
Fluxor com qualquer outra coisa: um botão no seu app, um webhook do GitHub,
um alerta do Grafana.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from fluxor.models import TriggerType
from fluxor.registry import all_actions

router = APIRouter()


class RunRequest(BaseModel):
    """Corpo do disparo manual pela API."""

    vars: dict[str, Any] = Field(default_factory=dict, description="Sobrescreve vars do workflow.")
    dry_run: bool = Field(default=False, description="Resolve tudo sem causar efeito colateral.")


def _state(request: Request) -> Any:
    return request.app.state


def _get_workflow(request: Request, name: str) -> Any:
    workflow = _state(request).workflows.get(name)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"workflow '{name}' não encontrado")
    return workflow


# ---------------------------------------------------------------------------
# Saúde e catálogo
# ---------------------------------------------------------------------------
@router.get("/health", summary="Estado do serviço")
async def health(request: Request) -> dict[str, Any]:
    from fluxor.api.app import describe_state

    return describe_state(request.app)


@router.get("/actions", summary="Catálogo de actions disponíveis")
async def list_actions() -> dict[str, Any]:
    catalog = [action.describe() for action in all_actions().values()]
    return {"total": len(catalog), "items": catalog}


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------
@router.get("/workflows", summary="Lista os workflows carregados")
async def list_workflows(request: Request) -> dict[str, Any]:
    state = _state(request)
    items = []
    for name, workflow in sorted(state.workflows.items()):
        items.append(
            {
                "name": name,
                "description": workflow.description,
                "version": workflow.version,
                "trigger": workflow.trigger.type.value,
                "cron": workflow.trigger.cron,
                "steps": len(workflow.steps),
                "file": state.workflow_paths[name].name,
            }
        )
    return {"total": len(items), "items": items, "error": state.workflows_error}


@router.get("/workflows/{name}", summary="Detalha um workflow")
async def get_workflow(request: Request, name: str) -> dict[str, Any]:
    workflow = _get_workflow(request, name)
    return {
        "name": workflow.name,
        "description": workflow.description,
        "version": workflow.version,
        "trigger": workflow.trigger.model_dump(),
        "vars": workflow.vars,
        "env": workflow.env,
        "timeout": workflow.timeout,
        "steps": [step.model_dump(by_alias=True, exclude_none=True) for step in workflow.steps],
        "on_failure": [
            step.model_dump(by_alias=True, exclude_none=True) for step in workflow.on_failure
        ],
        "file": _state(request).workflow_paths[name].name,
    }


@router.post("/workflows/reload", summary="Recarrega os arquivos YAML do disco")
async def reload_workflows_endpoint(request: Request) -> dict[str, Any]:
    from fluxor.api.app import reload_workflows

    total = reload_workflows(request.app)
    state = _state(request)
    if state.scheduler is not None:
        state.scheduler.load()
    return {"loaded": total, "error": state.workflows_error}


@router.post("/workflows/{name}/run", summary="Executa um workflow agora")
async def run_workflow(
    request: Request, name: str, payload: RunRequest | None = None
) -> dict[str, Any]:
    workflow = _get_workflow(request, name)
    body = payload or RunRequest()

    record = await _state(request).engine.execute(
        workflow, extra_vars=body.vars, trigger="api", dry_run=body.dry_run
    )
    return record.to_dict()


@router.post("/hooks/{name}", summary="Dispara um workflow por webhook")
async def trigger_webhook(
    request: Request,
    name: str,
    token: str | None = Query(default=None, description="Token declarado em trigger.token."),
) -> dict[str, Any]:
    """Ponto de entrada para gatilhos externos.

    O token é comparado com `secrets.compare_digest`, porque comparação com `==`
    vaza informação por tempo de resposta e permite adivinhar o segredo byte a
    byte. Workflow com token configurado e token errado devolve 403.
    """
    workflow = _get_workflow(request, name)

    if workflow.trigger.type is not TriggerType.WEBHOOK:
        raise HTTPException(
            status_code=400,
            detail=f"workflow '{name}' não é do tipo webhook (é '{workflow.trigger.type.value}')",
        )

    expected = workflow.trigger.token
    if expected and not (token and secrets.compare_digest(token, expected)):
        raise HTTPException(status_code=403, detail="token inválido")

    body: dict[str, Any] = {}
    try:
        raw = await request.json()
        if isinstance(raw, dict):
            body = raw
    except Exception:
        body = {}

    record = await _state(request).engine.execute(
        workflow, extra_vars={"payload": body}, trigger="webhook"
    )
    return {"run_id": record.id, "status": record.status.value}


# ---------------------------------------------------------------------------
# Execuções
# ---------------------------------------------------------------------------
@router.get("/runs", summary="Histórico de execuções")
async def list_runs(
    request: Request,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    workflow: str | None = None,
    status: str | None = Query(default=None, pattern="^(success|failed|partial|running)$"),
) -> dict[str, Any]:
    return await _state(request).repository.list_runs(
        limit=limit, offset=offset, workflow=workflow, status=status
    )


@router.get("/runs/{run_id}", summary="Detalhe de uma execução")
async def get_run(request: Request, run_id: str) -> dict[str, Any]:
    detail = await _state(request).repository.get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"execução '{run_id}' não encontrada")
    return detail


@router.get("/stats", summary="Métricas agregadas para o dashboard")
async def stats(request: Request, days: int = Query(default=14, ge=1, le=90)) -> dict[str, Any]:
    return await _state(request).repository.stats(days=days)


@router.get("/scheduler/jobs", summary="Jobs agendados e próxima execução")
async def scheduler_jobs(request: Request) -> dict[str, Any]:
    scheduler = _state(request).scheduler
    if scheduler is None:
        return {"enabled": False, "items": []}
    return {"enabled": True, "items": scheduler.describe_jobs()}
