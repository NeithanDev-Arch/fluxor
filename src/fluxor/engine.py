"""O motor: pega um :class:`Workflow` e o transforma em uma execução.

Responsabilidades, em ordem:

1. resolver `vars` e a allowlist de `env`;
2. para cada passo, avaliar `when`, expandir `foreach`, renderizar `with`,
   validar contra o schema da action, executar com retry e timeout;
3. registrar cada resultado no `RunContext` (o passo seguinte já enxerga a saída
   do anterior em `{{ steps.<id> }}`);
4. rodar `on_failure` quando o workflow quebra;
5. empurrar tudo para o `RunSink` (banco), sem que uma falha de persistência
   derrube a execução em si.

O motor não conhece SQLAlchemy nem FastAPI: ele fala com o protocolo
:class:`RunSink`. É por isso que dá para usá-lo como biblioteca pura, sem banco.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import structlog

from fluxor.config import Settings, get_settings
from fluxor.context import RunContext, RunStatus, StepResult, StepStatus, utcnow
from fluxor.exceptions import PermanentError, StepExecutionError
from fluxor.logging_setup import get_logger
from fluxor.models import OnError, Step, Workflow
from fluxor.registry import bootstrap, get_action
from fluxor.retry import with_retry
from fluxor.template import evaluate_condition, render_value, resolve_expression

StepObserver = Callable[[StepResult], None]
"""Callback chamado a cada passo concluído. A CLI usa para imprimir ao vivo."""


@runtime_checkable
class RunSink(Protocol):
    """Destino da telemetria de execução.

    O motor depende deste protocolo, não da implementação. Trocar SQLite por
    Postgres, Redis ou um arquivo JSONL não encosta em uma linha do engine.
    """

    async def start_run(self, record: RunRecord) -> None: ...

    async def save_step(self, run_id: str, index: int, result: StepResult) -> None: ...

    async def finish_run(self, record: RunRecord) -> None: ...


@dataclass
class RunRecord:
    """O resultado completo de uma execução."""

    id: str
    workflow: str
    status: RunStatus
    trigger: str
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    dry_run: bool = False
    vars: dict[str, Any] = field(default_factory=dict)
    results: list[StepResult] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        end = self.finished_at or utcnow()
        return max(0, int((end - self.started_at).total_seconds() * 1000))

    @property
    def ok(self) -> bool:
        return self.status is RunStatus.SUCCESS

    @property
    def counts(self) -> dict[str, int]:
        tally = {"success": 0, "failed": 0, "skipped": 0}
        for result in self.results:
            tally[result.status.value] += 1
        return tally

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workflow": self.workflow,
            "status": self.status.value,
            "trigger": self.trigger,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "dry_run": self.dry_run,
            "counts": self.counts,
            "steps": [result.to_dict() for result in self.results],
        }


class Engine:
    """Executa workflows. Reutilizável e seguro para várias execuções em paralelo."""

    def __init__(self, sink: RunSink | None = None, settings: Settings | None = None) -> None:
        bootstrap()
        self.sink = sink
        self.settings = settings or get_settings()
        self.log = get_logger("fluxor.engine")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    async def execute(
        self,
        workflow: Workflow,
        *,
        extra_vars: dict[str, Any] | None = None,
        trigger: str = "manual",
        dry_run: bool = False,
        on_step: StepObserver | None = None,
        run_id: str | None = None,
    ) -> RunRecord:
        """Roda o workflow inteiro e devolve o registro da execução.

        Nunca levanta por falha de passo: o erro vira `record.status = failed`
        e `record.error`. Quem chama decide o que fazer (a CLI vira exit code 1,
        o agendador só loga e segue).
        """
        run_id = run_id or uuid.uuid4().hex[:16]
        ctx = RunContext(
            run_id=run_id,
            workflow_name=workflow.name,
            env=self._collect_env(workflow),
            trigger=trigger,
            dry_run=dry_run,
        )

        structlog.contextvars.bind_contextvars(run_id=run_id, workflow=workflow.name)
        record = RunRecord(
            id=run_id,
            workflow=workflow.name,
            status=RunStatus.RUNNING,
            trigger=trigger,
            started_at=ctx.started_at,
            dry_run=dry_run,
        )

        try:
            ctx.vars = self._resolve_vars(workflow, extra_vars or {}, ctx)
            record.vars = ctx.vars
        except Exception as exc:
            record.status = RunStatus.FAILED
            record.error = f"falha ao resolver 'vars': {exc}"
            record.finished_at = utcnow()
            self.log.error("run_falhou", error=record.error)
            structlog.contextvars.clear_contextvars()
            return record

        self.log.info("run_iniciado", trigger=trigger, steps=len(workflow.steps), dry_run=dry_run)
        await self._sink_call("start_run", record)

        error: str | None = None
        try:
            runner = self._run_steps(workflow.steps, ctx, on_step)
            if workflow.timeout:
                await asyncio.wait_for(runner, timeout=workflow.timeout)
            else:
                await runner
        except TimeoutError:
            error = f"workflow excedeu o timeout de {workflow.timeout}s"
        except StepExecutionError as exc:
            error = str(exc)
        except Exception as exc:
            error = f"erro inesperado: {exc.__class__.__name__}: {exc}"
            self.log.exception("run_erro_inesperado")

        if error:
            record.status = RunStatus.FAILED
            record.error = error
            await self._run_failure_handlers(workflow, ctx, error, on_step)
        else:
            record.status = ctx.status

        record.results = list(ctx.results)
        record.finished_at = utcnow()

        self.log.info(
            "run_finalizado",
            status=record.status.value,
            duration_ms=record.duration_ms,
            **record.counts,
        )
        await self._sink_call("finish_run", record)
        structlog.contextvars.clear_contextvars()
        return record

    # ------------------------------------------------------------------
    # Preparação
    # ------------------------------------------------------------------
    def _collect_env(self, workflow: Workflow) -> dict[str, str]:
        """Só as variáveis declaradas em `env:` chegam ao template.

        Um workflow que não pede `AWS_SECRET_ACCESS_KEY` não consegue lê-la,
        mesmo que ela exista no processo. Allowlist explícita em vez de acesso
        irrestrito ao ambiente.
        """
        collected: dict[str, str] = {}
        for name in workflow.env:
            value = os.environ.get(name)
            if value is None:
                self.log.warning("env_ausente", variavel=name, workflow=workflow.name)
                continue
            collected[name] = value
        return collected

    def _resolve_vars(
        self, workflow: Workflow, overrides: dict[str, Any], ctx: RunContext
    ) -> dict[str, Any]:
        """Renderiza `vars` em ordem, já que uma var pode usar as anteriores."""
        resolved: dict[str, Any] = {}
        for key, value in workflow.vars.items():
            if key in overrides:
                resolved[key] = overrides[key]
            else:
                ctx.vars = resolved
                resolved[key] = render_value(value, ctx.snapshot())
        for key, value in overrides.items():
            resolved.setdefault(key, value)
        return resolved

    # ------------------------------------------------------------------
    # Execução
    # ------------------------------------------------------------------
    async def _run_steps(
        self, steps: list[Step], ctx: RunContext, on_step: StepObserver | None
    ) -> None:
        for index, step in enumerate(steps):
            result = await self._execute_step(step, ctx)
            ctx.record(result)
            await self._sink_call("save_step", ctx.run_id, index, result)
            if on_step:
                on_step(result)

            if result.status is StepStatus.FAILED and step.on_error is OnError.FAIL:
                raise StepExecutionError(step.id, result.error or "erro desconhecido")

    async def _execute_step(
        self, step: Step, ctx: RunContext, extra: dict[str, Any] | None = None
    ) -> StepResult:
        """Executa um passo e devolve o resultado; falha vira status, não exceção."""
        extra = extra or {}
        result = StepResult(
            step_id=step.id, action=step.use, status=StepStatus.SUCCESS, started_at=utcnow()
        )
        attempts = {"value": 1}

        try:
            base_context = ctx.snapshot(**extra)

            if step.when is not None and not evaluate_condition(step.when, base_context):
                result.status = StepStatus.SKIPPED
                result.skipped_reason = f"when: {step.when}"
                result.finished_at = utcnow()
                self.log.info("passo_pulado", step=step.id, when=step.when)
                return result

            action = get_action(step.use)()

            if step.foreach is not None:
                result.output = await self._run_foreach(step, action, ctx, extra, attempts)
            else:
                result.output = await self._run_once(step, action, ctx, base_context, attempts)

            result.attempts = attempts["value"]
            self.log.info(
                "passo_ok",
                step=step.id,
                action=step.use,
                ms=result.duration_ms,
                tentativas=result.attempts,
            )
        except Exception as exc:
            result.status = StepStatus.FAILED
            result.attempts = attempts["value"]
            result.error = str(exc) or exc.__class__.__name__
            self.log.error("passo_falhou", step=step.id, action=step.use, error=result.error)

        result.finished_at = utcnow()
        return result

    async def _run_once(
        self,
        step: Step,
        action: Any,
        ctx: RunContext,
        context: dict[str, Any],
        attempts: dict[str, int],
    ) -> Any:
        """Renderiza, valida e executa uma vez (com a política de retry do passo)."""
        action_cls = type(action)
        rendered = self._render_params(action_cls.raw_params, step.params, context)
        params = action_cls.parse_params(rendered)

        async def attempt() -> Any:
            if ctx.dry_run:
                return {
                    "dry_run": True,
                    "action": step.use,
                    "params": params.model_dump(mode="json", by_alias=True),
                }
            call = action.run(params, ctx)
            if step.timeout:
                return await asyncio.wait_for(call, timeout=step.timeout)
            return await call

        def on_attempt_failed(attempt_number: int, exc: BaseException, delay: float) -> None:
            attempts["value"] = attempt_number + 1
            self.log.warning(
                "tentativa_falhou",
                step=step.id,
                tentativa=attempt_number,
                proxima_em=delay,
                error=str(exc),
            )

        output, used = await with_retry(attempt, step.retry, on_attempt_failed=on_attempt_failed)
        attempts["value"] = used
        return output

    async def _run_foreach(
        self,
        step: Step,
        action: Any,
        ctx: RunContext,
        extra: dict[str, Any],
        attempts: dict[str, int],
    ) -> list[Any]:
        """Roda o passo uma vez por item, com paralelismo limitado.

        As actions não guardam estado na instância, então compartilhar `action`
        entre as tarefas é seguro. O semáforo evita transformar uma lista de
        500 itens em 500 conexões simultâneas.
        """
        assert step.foreach is not None
        items = resolve_expression(step.foreach, ctx.snapshot(**extra))

        if isinstance(items, (str, bytes)) or not isinstance(items, (list, tuple)):
            raise PermanentError(
                f"'foreach' precisa resolver numa lista; recebi {type(items).__name__}"
            )

        semaphore = asyncio.Semaphore(self.settings.foreach_concurrency)

        async def run_item(index: int, item: Any) -> Any:
            async with semaphore:
                context = ctx.snapshot(item=item, index=index, **extra)
                return await self._run_once(step, action, ctx, context, attempts)

        outputs = await asyncio.gather(
            *(run_item(index, item) for index, item in enumerate(items)),
            return_exceptions=True,
        )

        for output in outputs:
            if isinstance(output, BaseException):
                raise output
        return list(outputs)

    async def _run_failure_handlers(
        self, workflow: Workflow, ctx: RunContext, error: str, on_step: StepObserver | None
    ) -> None:
        """Roda `on_failure`, com a mensagem disponível em `{{ error }}`."""
        if not workflow.on_failure:
            return

        self.log.info("on_failure_iniciado", passos=len(workflow.on_failure))
        offset = len(ctx.results)

        for index, step in enumerate(workflow.on_failure, start=offset):
            result = await self._execute_step(step, ctx, extra={"error": error})
            ctx.record(result)
            await self._sink_call("save_step", ctx.run_id, index, result)
            if on_step:
                on_step(result)
            # Falha na compensação é logada, mas não gera cascata de erros.
            if result.status is StepStatus.FAILED:
                self.log.error("on_failure_falhou", step=step.id, error=result.error)

    # ------------------------------------------------------------------
    # Auxiliares
    # ------------------------------------------------------------------
    @staticmethod
    def _render_params(
        raw_fields: frozenset[str], params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Renderiza o `with:`, preservando os campos declarados como `raw_params`."""
        return {
            key: value if key in raw_fields else render_value(value, context)
            for key, value in params.items()
        }

    async def _sink_call(self, method: str, *args: Any) -> None:
        """Chama o sink sem deixar erro de persistência derrubar a execução."""
        if self.sink is None:
            return
        try:
            await getattr(self.sink, method)(*args)
        except Exception as exc:
            self.log.warning("sink_falhou", metodo=method, error=str(exc))
