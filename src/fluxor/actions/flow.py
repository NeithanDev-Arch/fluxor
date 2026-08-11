"""Actions de controle de fluxo — pausar, falhar de propósito, guardar valores."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from pydantic import Field

from fluxor.actions.base import Action, ActionInput
from fluxor.context import RunContext
from fluxor.exceptions import FluxorError, PermanentError
from fluxor.registry import register


class SetInput(ActionInput):
    values: dict[str, Any] = Field(description="Pares chave/valor, já renderizados.")


@register("flow.set")
class FlowSet(Action):
    """Guarda valores calculados para os próximos passos.

    Evita repetir a mesma expressão em cinco lugares::

        - id: calculado
          use: flow.set
          with:
            values:
              total: "{{ steps.itens.json | length }}"
    """

    summary = "Define valores reaproveitáveis em {{ steps.<id>.<chave> }}"
    Input: ClassVar[type[ActionInput]] = SetInput

    async def run(self, params: SetInput, ctx: RunContext) -> dict[str, Any]:
        return params.values


class SleepInput(ActionInput):
    seconds: float = Field(gt=0, le=3600, description="Tempo de espera.")


@register("flow.sleep")
class FlowSleep(Action):
    """Pausa a execução — útil para respeitar rate limit de API."""

    summary = "Espera N segundos antes do próximo passo"
    Input: ClassVar[type[ActionInput]] = SleepInput

    async def run(self, params: SleepInput, ctx: RunContext) -> dict[str, float]:
        if not ctx.dry_run:
            await asyncio.sleep(params.seconds)
        return {"slept": params.seconds}


class FailInput(ActionInput):
    message: str = Field(default="falha provocada pelo workflow")
    permanent: bool = Field(default=True, description="Se false, a política de retry se aplica.")


@register("flow.fail")
class FlowFail(Action):
    """Falha de propósito — para testar `on_failure` e alertas."""

    summary = "Falha o passo deliberadamente"
    Input: ClassVar[type[ActionInput]] = FailInput

    async def run(self, params: FailInput, ctx: RunContext) -> Any:
        if params.permanent:
            raise PermanentError(params.message)
        raise FluxorError(params.message)


class AssertInput(ActionInput):
    that: Any = Field(description="Valor/condição que precisa ser verdadeiro.")
    message: str = Field(default="asserção falhou")


@register("flow.assert")
class FlowAssert(Action):
    """Barreira de qualidade: interrompe o fluxo se a condição não valer.

    Serve para não notificar em cima de dado quebrado — melhor falhar visível
    do que mandar "Preço atual: None" para o cliente.
    """

    summary = "Interrompe o workflow se a condição for falsa"
    Input: ClassVar[type[ActionInput]] = AssertInput

    async def run(self, params: AssertInput, ctx: RunContext) -> dict[str, bool]:
        if not params.that:
            raise PermanentError(params.message)
        return {"ok": True}
