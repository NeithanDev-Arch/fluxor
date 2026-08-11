"""Repositório de execuções — implementa o protocolo `RunSink` do motor.

Além de gravar, é daqui que saem as consultas do dashboard: histórico paginado,
detalhe de uma execução e as métricas agregadas.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, desc, func, select
from sqlalchemy.engine import CursorResult

from fluxor.config import get_settings
from fluxor.context import StepResult
from fluxor.engine import RunRecord
from fluxor.storage.database import Database
from fluxor.storage.models import RunRow, StepRow


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite devolve datetime sem fuso; reanexamos UTC para o ISO sair correto."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized else None


def dump_json(value: Any, max_bytes: int) -> str | None:
    """Serializa a saída de um passo, cortando o que for grande demais.

    Uma página HTML de 2 MB não deve virar linha de banco — o valor completo
    continua disponível para os passos seguintes durante a execução; o que é
    truncado é só o registro histórico.
    """
    if value is None:
        return None
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = json.dumps(str(value), ensure_ascii=False)

    if len(text) > max_bytes:
        cut = text[:max_bytes]
        return json.dumps({"_truncated": True, "_bytes": len(text), "preview": cut[:2000]})
    return text


def load_json(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


class RunRepository:
    """Persistência das execuções. Satisfaz `fluxor.engine.RunSink`."""

    def __init__(self, database: Database, max_output_bytes: int | None = None) -> None:
        self.db = database
        self.max_output_bytes = max_output_bytes or get_settings().max_output_bytes

    # -- protocolo RunSink ------------------------------------------------
    async def start_run(self, record: RunRecord) -> None:
        async with self.db.session() as session:
            session.add(
                RunRow(
                    id=record.id,
                    workflow=record.workflow,
                    status=record.status.value,
                    trigger=record.trigger,
                    started_at=record.started_at,
                    dry_run=record.dry_run,
                    vars_json=dump_json(record.vars, self.max_output_bytes),
                )
            )

    async def save_step(self, run_id: str, index: int, result: StepResult) -> None:
        async with self.db.session() as session:
            session.add(
                StepRow(
                    run_id=run_id,
                    position=index,
                    step_id=result.step_id,
                    action=result.action,
                    status=result.status.value,
                    attempts=result.attempts,
                    started_at=result.started_at,
                    finished_at=result.finished_at,
                    duration_ms=result.duration_ms,
                    error=result.error,
                    output_json=dump_json(result.output, self.max_output_bytes),
                    skipped_reason=result.skipped_reason,
                )
            )

    async def finish_run(self, record: RunRecord) -> None:
        async with self.db.session() as session:
            row = await session.get(RunRow, record.id)
            if row is None:  # execução iniciada sem sink (ex.: retomada) — cria agora
                row = RunRow(id=record.id, workflow=record.workflow, started_at=record.started_at)
                session.add(row)
            row.status = record.status.value
            row.finished_at = record.finished_at
            row.duration_ms = record.duration_ms
            row.error = record.error
            row.dry_run = record.dry_run

    # -- consultas --------------------------------------------------------
    async def list_runs(
        self,
        *,
        limit: int = 25,
        offset: int = 0,
        workflow: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Histórico paginado, mais recente primeiro."""
        filters = []
        if workflow:
            filters.append(RunRow.workflow == workflow)
        if status:
            filters.append(RunRow.status == status)

        async with self.db.session() as session:
            total = await session.scalar(select(func.count()).select_from(RunRow).where(*filters))
            rows = (
                await session.scalars(
                    select(RunRow)
                    .where(*filters)
                    .order_by(desc(RunRow.started_at))
                    .limit(limit)
                    .offset(offset)
                )
            ).all()

            items = [
                {
                    "id": row.id,
                    "workflow": row.workflow,
                    "status": row.status,
                    "trigger": row.trigger,
                    "started_at": _iso(row.started_at),
                    "finished_at": _iso(row.finished_at),
                    "duration_ms": row.duration_ms,
                    "error": row.error,
                    "dry_run": row.dry_run,
                    "steps_total": len(row.steps),
                    "steps_failed": sum(1 for step in row.steps if step.status == "failed"),
                }
                for row in rows
            ]

        return {"items": items, "total": total or 0, "limit": limit, "offset": offset}

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Execução completa, com todos os passos e suas saídas."""
        async with self.db.session() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                return None

            return {
                "id": row.id,
                "workflow": row.workflow,
                "status": row.status,
                "trigger": row.trigger,
                "started_at": _iso(row.started_at),
                "finished_at": _iso(row.finished_at),
                "duration_ms": row.duration_ms,
                "error": row.error,
                "dry_run": row.dry_run,
                "vars": load_json(row.vars_json),
                "steps": [
                    {
                        "position": step.position,
                        "step_id": step.step_id,
                        "action": step.action,
                        "status": step.status,
                        "attempts": step.attempts,
                        "started_at": _iso(step.started_at),
                        "finished_at": _iso(step.finished_at),
                        "duration_ms": step.duration_ms,
                        "error": step.error,
                        "output": load_json(step.output_json),
                        "skipped_reason": step.skipped_reason,
                    }
                    for step in row.steps
                ],
            }

    async def stats(self, days: int = 14) -> dict[str, Any]:
        """Métricas do dashboard: volume, taxa de sucesso, série diária e top workflows.

        A agregação por dia é feita em Python de propósito — `date()` tem sintaxe
        diferente em SQLite e Postgres, e a janela é pequena o bastante para que
        a diferença não apareça.
        """
        since = datetime.now(UTC) - timedelta(days=days)

        async with self.db.session() as session:
            rows = (
                await session.scalars(
                    select(RunRow).where(RunRow.started_at >= since).order_by(RunRow.started_at)
                )
            ).all()

            agrupado = await session.execute(
                select(RunRow.status, func.count())
                .where(RunRow.started_at >= since)
                .group_by(RunRow.status)
            )
            status_counts: dict[str, int] = dict(agrupado.tuples().all())

        total = len(rows)
        succeeded = status_counts.get("success", 0)
        durations = [row.duration_ms for row in rows if row.duration_ms]

        by_day: dict[str, dict[str, int]] = {}
        for offset in range(days - 1, -1, -1):
            day = (datetime.now(UTC) - timedelta(days=offset)).date().isoformat()
            by_day[day] = {"success": 0, "failed": 0, "partial": 0, "total": 0}

        by_workflow: dict[str, dict[str, int]] = {}
        for row in rows:
            day = (_as_utc(row.started_at) or datetime.now(UTC)).date().isoformat()
            if day in by_day:
                by_day[day]["total"] += 1
                if row.status in by_day[day]:
                    by_day[day][row.status] += 1

            bucket = by_workflow.setdefault(row.workflow, {"total": 0, "failed": 0, "duration": 0})
            bucket["total"] += 1
            bucket["duration"] += row.duration_ms
            if row.status == "failed":
                bucket["failed"] += 1

        resumo: list[dict[str, Any]] = [
            {
                "workflow": name,
                "total": data["total"],
                "failed": data["failed"],
                "avg_duration_ms": int(data["duration"] / data["total"]) if data["total"] else 0,
            }
            for name, data in by_workflow.items()
        ]
        resumo.sort(key=lambda item: int(item["total"]), reverse=True)

        return {
            "window_days": days,
            "total_runs": total,
            "success_rate": round(succeeded / total * 100, 1) if total else 0.0,
            "avg_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
            "by_status": status_counts,
            "by_day": [{"date": day, **counts} for day, counts in by_day.items()],
            "by_workflow": resumo[:10],
        }

    async def purge(self, older_than_days: int) -> int:
        """Apaga execuções antigas. Devolve quantas linhas saíram."""
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        async with self.db.session() as session:
            result = await session.execute(delete(RunRow).where(RunRow.started_at < cutoff))
            # `execute` é tipado como Result genérico; um DELETE sempre devolve
            # um CursorResult, que é quem tem o rowcount.
            return int(cast("CursorResult[Any]", result).rowcount or 0)
