"""Agendador: roda sozinho os workflows com `trigger.type: schedule`.

Três ajustes que separam um agendador de brinquedo de um utilizável:

* ``max_instances=1``: se a execução das 9h ainda estiver rodando às 10h, a das
  10h não começa por cima. Sem isso, um workflow lento se multiplica até derrubar
  a máquina.
* ``coalesce=True``: máquina que ficou 3 horas desligada não dispara 3 execuções
  atrasadas de uma vez; dispara uma.
* ``misfire_grace_time``: atraso tolerado antes de simplesmente pular a janela.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from fluxor.config import get_settings
from fluxor.engine import Engine
from fluxor.loader import load_workflow_dir
from fluxor.logging_setup import get_logger
from fluxor.models import TriggerType, Workflow

log = get_logger("fluxor.scheduler")

DEFAULT_MISFIRE_GRACE = 300  # 5 minutos


class WorkflowScheduler:
    """Envolve o APScheduler com o carregamento dos workflows da pasta."""

    def __init__(
        self,
        engine: Engine,
        workflows_dir: str | Path | None = None,
        timezone: str | None = None,
    ) -> None:
        settings = get_settings()
        self.engine = engine
        self.workflows_dir = Path(workflows_dir or settings.workflows_dir)
        self.timezone = timezone or settings.timezone
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self._workflows: dict[str, Workflow] = {}

    # ------------------------------------------------------------------
    def load(self) -> list[Workflow]:
        """(Re)carrega a pasta e registra um job por workflow agendado."""
        self.scheduler.remove_all_jobs()
        self._workflows.clear()

        scheduled: list[Workflow] = []
        for workflow, path in load_workflow_dir(self.workflows_dir):
            self._workflows[workflow.name] = workflow
            if workflow.trigger.type is not TriggerType.SCHEDULE or not workflow.trigger.cron:
                continue

            trigger = CronTrigger.from_crontab(
                workflow.trigger.cron,
                timezone=workflow.trigger.timezone or self.timezone,
            )
            self.scheduler.add_job(
                self._run_workflow,
                trigger=trigger,
                args=[workflow.name],
                id=workflow.name,
                name=workflow.description or workflow.name,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=DEFAULT_MISFIRE_GRACE,
            )
            scheduled.append(workflow)
            log.info(
                "job_registrado",
                workflow=workflow.name,
                cron=workflow.trigger.cron,
                arquivo=path.name,
            )

        log.info("agendador_carregado", agendados=len(scheduled), total=len(self._workflows))
        return scheduled

    async def _run_workflow(self, name: str) -> None:
        """Alvo do job. Nunca levanta, porque o agendador precisa continuar de pé."""
        workflow = self._workflows.get(name)
        if workflow is None:
            log.warning("workflow_sumiu", workflow=name)
            return

        try:
            record = await self.engine.execute(workflow, trigger="schedule")
            log.info("execucao_agendada", workflow=name, status=record.status.value)
        except Exception as exc:
            log.exception("execucao_agendada_falhou", workflow=name, error=str(exc))

    # ------------------------------------------------------------------
    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
            log.info(
                "agendador_iniciado", timezone=self.timezone, jobs=len(self.scheduler.get_jobs())
            )

    def shutdown(self, wait: bool = False) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=wait)
            log.info("agendador_parado")

    @property
    def workflows(self) -> dict[str, Workflow]:
        return dict(self._workflows)

    def describe_jobs(self) -> list[dict[str, Any]]:
        """Lista os jobs com a próxima execução. Usado pela CLI e pela API."""
        jobs = []
        for job in self.scheduler.get_jobs():
            workflow = self._workflows.get(job.id)
            next_run: datetime | None = getattr(job, "next_run_time", None)
            if next_run is None:
                next_run = job.trigger.get_next_fire_time(None, datetime.now(job.trigger.timezone))
            jobs.append(
                {
                    "workflow": job.id,
                    "description": workflow.description if workflow else None,
                    "cron": workflow.trigger.cron if workflow else None,
                    "next_run": next_run.isoformat() if next_run else None,
                }
            )
        return sorted(jobs, key=lambda item: item["next_run"] or "")
