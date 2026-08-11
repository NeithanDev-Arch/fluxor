"""Schema do workflow — o contrato entre o YAML e o motor.

Tudo aqui é Pydantic com `extra="forbid"`: uma chave digitada errado no YAML
vira erro de validação com a linha do problema, e não um campo silenciosamente
ignorado que só aparece como bug três dias depois em produção.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# `id` de passo precisa ser um identificador utilizável em template: {{ steps.meu_passo }}
STEP_ID_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
# nome de workflow vira nome de arquivo, rota de webhook e chave de agendamento
WORKFLOW_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# Nomes que o motor injeta no contexto — um passo não pode sequestrá-los.
RESERVED_STEP_IDS = frozenset({"vars", "env", "run", "steps", "item", "index", "error"})


class BackoffStrategy(StrEnum):
    """Como o intervalo entre tentativas cresce."""

    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


class OnError(StrEnum):
    """O que fazer quando um passo falha depois de esgotar as tentativas."""

    FAIL = "fail"
    """Aborta o workflow (padrão)."""
    CONTINUE = "continue"
    """Registra a falha e segue para o próximo passo."""


class TriggerType(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"


class RetryConfig(BaseModel):
    """Política de retry de um passo.

    O delay da tentativa N sai de :func:`fluxor.retry.compute_delay`.
    """

    model_config = ConfigDict(extra="forbid")

    attempts: int = Field(
        default=3, ge=1, le=20, description="Total de tentativas, incluindo a 1ª."
    )
    delay: float = Field(default=1.0, ge=0, le=600, description="Intervalo base em segundos.")
    backoff: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    max_delay: float = Field(default=60.0, ge=0, description="Teto do intervalo entre tentativas.")
    jitter: bool = Field(
        default=True,
        description="Adiciona ruído ao intervalo para evitar thundering herd.",
    )


class Trigger(BaseModel):
    """O que dispara o workflow."""

    model_config = ConfigDict(extra="forbid")

    type: TriggerType = TriggerType.MANUAL
    cron: str | None = Field(
        default=None, description="Cron de 5 campos. Obrigatório em `schedule`."
    )
    timezone: str | None = Field(
        default=None, description="Fuso do cron. Default: FLUXOR_TIMEZONE."
    )
    token: str | None = Field(default=None, description="Segredo exigido no POST, em `webhook`.")

    @field_validator("cron")
    @classmethod
    def _validate_cron(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Import tardio: APScheduler é pesado e models.py é importado em todo lugar.
        from apscheduler.triggers.cron import CronTrigger

        try:
            CronTrigger.from_crontab(value)
        except Exception as exc:
            raise ValueError(f"expressão cron inválida ({value!r}): {exc}") from exc
        return value

    @model_validator(mode="after")
    def _cron_required_for_schedule(self) -> Trigger:
        if self.type is TriggerType.SCHEDULE and not self.cron:
            raise ValueError("trigger do tipo 'schedule' exige o campo 'cron'")
        if self.type is not TriggerType.SCHEDULE and self.cron:
            raise ValueError("'cron' só faz sentido quando type = 'schedule'")
        return self


class Step(BaseModel):
    """Uma unidade de trabalho: chama uma action com parâmetros renderizados."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(description="Identificador único; expõe a saída em {{ steps.<id> }}.")
    use: str = Field(description="Nome da action registrada, ex.: 'http.get'.")
    description: str | None = None

    # `with` é palavra reservada do Python, então o campo se chama `params`
    # e usa alias para continuar sendo `with:` no YAML.
    params: dict[str, Any] = Field(default_factory=dict, alias="with")

    when: str | None = Field(
        default=None,
        description="Condição booleana; se falsa o passo é pulado (status 'skipped').",
    )
    foreach: str | None = Field(
        default=None,
        description="Expressão que resolve numa lista; roda o passo por item ({{ item }}).",
    )
    retry: RetryConfig | None = None
    timeout: float | None = Field(default=None, gt=0, description="Timeout do passo, em segundos.")
    on_error: OnError = OnError.FAIL

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not STEP_ID_RE.match(value):
            raise ValueError(
                f"id '{value}' inválido: use letras, números e underscore, começando por letra"
            )
        if value in RESERVED_STEP_IDS:
            raise ValueError(f"id '{value}' é reservado pelo motor")
        return value

    @field_validator("use")
    @classmethod
    def _validate_use(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("'use' não pode ser vazio")
        return value.strip()


class Workflow(BaseModel):
    """Um workflow completo, como aparece no arquivo YAML."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Identificador único do workflow.")
    description: str | None = None
    version: int = Field(default=1, ge=1)

    trigger: Trigger = Field(default_factory=Trigger)

    vars: dict[str, Any] = Field(
        default_factory=dict,
        description="Valores fixos do workflow; sobrescrevíveis via --var na CLI.",
    )
    env: list[str] = Field(
        default_factory=list,
        description=(
            "Allowlist de variáveis de ambiente visíveis em {{ env.NOME }}. "
            "O que não estiver aqui é invisível para o workflow."
        ),
    )

    timeout: float | None = Field(default=None, gt=0, description="Teto de tempo do workflow todo.")

    steps: list[Step] = Field(min_length=1)
    on_failure: list[Step] = Field(
        default_factory=list,
        description="Passos de compensação, rodados quando o workflow falha ({{ error }}).",
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not WORKFLOW_NAME_RE.match(value):
            raise ValueError(
                f"name '{value}' inválido: use minúsculas, números, '.', '-' ou '_' "
                "(ex.: 'monitor-precos')"
            )
        return value

    @field_validator("env")
    @classmethod
    def _validate_env_names(cls, value: list[str]) -> list[str]:
        for name in value:
            if not re.match(r"^[A-Z_][A-Z0-9_]*$", name):
                raise ValueError(
                    f"'{name}' não parece nome de variável de ambiente (use MAIÚSCULAS)"
                )
        return value

    @model_validator(mode="after")
    def _validate_unique_step_ids(self) -> Workflow:
        seen: set[str] = set()
        for step in [*self.steps, *self.on_failure]:
            if step.id in seen:
                raise ValueError(f"id de passo duplicado: '{step.id}'")
            seen.add(step.id)
        return self

    @property
    def all_steps(self) -> list[Step]:
        """Passos principais + compensação, útil para validar o `use:` de todos."""
        return [*self.steps, *self.on_failure]

    def step_by_id(self, step_id: str) -> Step | None:
        return next((s for s in self.all_steps if s.id == step_id), None)
