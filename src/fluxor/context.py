"""Estado de uma execução.

O `RunContext` é o que atravessa o workflow inteiro: guarda as variáveis, o
ambiente liberado e a saída de cada passo já executado. É dele que sai o
dicionário entregue ao Jinja quando um `{{ ... }}` é renderizado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class StepStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    """Terminou, mas algum passo com `on_error: continue` falhou pelo caminho."""


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class StepResult:
    """Resultado de um passo: vira linha na tabela `step_runs` e card no dashboard."""

    step_id: str
    action: str
    status: StepStatus
    output: Any = None
    error: str | None = None
    attempts: int = 1
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    skipped_reason: str | None = None

    @property
    def duration_ms(self) -> int:
        end = self.finished_at or utcnow()
        return max(0, int((end - self.started_at).total_seconds() * 1000))

    @property
    def ok(self) -> bool:
        return self.status is not StepStatus.FAILED

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "attempts": self.attempts,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "skipped_reason": self.skipped_reason,
        }


@dataclass
class RunContext:
    """Contexto vivo de uma execução."""

    run_id: str
    workflow_name: str
    vars: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    trigger: str = "manual"
    started_at: datetime = field(default_factory=utcnow)
    dry_run: bool = False

    # id do passo -> saída da action
    steps: dict[str, Any] = field(default_factory=dict)
    results: list[StepResult] = field(default_factory=list)

    def record(self, result: StepResult) -> None:
        """Registra o resultado e publica a saída para os próximos passos."""
        self.results.append(result)
        if result.status is StepStatus.SUCCESS:
            self.steps[result.step_id] = result.output

    def snapshot(self, **extra: Any) -> dict[str, Any]:
        """Dicionário entregue ao Jinja.

        `extra` injeta escopos locais: `item`/`index` dentro de um `foreach`,
        `error` nos passos de `on_failure`.
        """
        return {
            "vars": self.vars,
            "env": self.env,
            "steps": self.steps,
            "run": {
                "id": self.run_id,
                "workflow": self.workflow_name,
                "trigger": self.trigger,
                "started_at": self.started_at.isoformat(),
            },
            **extra,
        }

    @property
    def failed_steps(self) -> list[StepResult]:
        return [r for r in self.results if r.status is StepStatus.FAILED]

    @property
    def status(self) -> RunStatus:
        """Status derivado dos passos já registrados."""
        if self.failed_steps:
            return RunStatus.PARTIAL
        return RunStatus.SUCCESS
