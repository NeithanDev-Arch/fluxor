"""Actions de transformação: moldar dados entre um passo e outro.

Estas actions usam `raw_params`: o motor entrega a expressão **sem renderizar**,
porque `{{ item }}` só existe dentro do laço que a própria action controla.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field

from fluxor.actions.base import Action, ActionInput
from fluxor.context import RunContext
from fluxor.registry import register
from fluxor.template import evaluate_condition, render_value


class MapInput(ActionInput):
    items: list[Any] = Field(description="Lista de entrada.")
    expr: str = Field(description="Expressão aplicada a cada item; use {{ item }} e {{ index }}.")


@register("transform.map")
class TransformMap(Action):
    """Aplica uma expressão a cada item de uma lista."""

    summary = "Transforma cada item de uma lista com uma expressão"
    Input: ClassVar[type[ActionInput]] = MapInput
    raw_params: ClassVar[frozenset[str]] = frozenset({"expr"})

    async def run(self, params: MapInput, ctx: RunContext) -> list[Any]:
        return [
            render_value(params.expr, ctx.snapshot(item=item, index=index))
            for index, item in enumerate(params.items)
        ]


class FilterInput(ActionInput):
    items: list[Any] = Field(description="Lista de entrada.")
    condition: str = Field(description="Condição booleana avaliada por item ({{ item }}).")


@register("transform.filter")
class TransformFilter(Action):
    """Mantém só os itens que satisfazem a condição."""

    summary = "Filtra uma lista por uma condição"
    Input: ClassVar[type[ActionInput]] = FilterInput
    raw_params: ClassVar[frozenset[str]] = frozenset({"condition"})

    async def run(self, params: FilterInput, ctx: RunContext) -> list[Any]:
        return [
            item
            for index, item in enumerate(params.items)
            if evaluate_condition(params.condition, ctx.snapshot(item=item, index=index))
        ]


class SortInput(ActionInput):
    items: list[Any] = Field(description="Lista de entrada.")
    key: str | None = Field(
        default=None, description="Expressão da chave de ordenação ({{ item }})."
    )
    reverse: bool = False


@register("transform.sort")
class TransformSort(Action):
    """Ordena uma lista, opcionalmente por uma chave calculada."""

    summary = "Ordena uma lista"
    Input: ClassVar[type[ActionInput]] = SortInput
    raw_params: ClassVar[frozenset[str]] = frozenset({"key"})

    async def run(self, params: SortInput, ctx: RunContext) -> list[Any]:
        if params.key is None:
            return sorted(params.items, reverse=params.reverse)

        def key_of(item: Any) -> Any:
            return render_value(params.key, ctx.snapshot(item=item))

        return sorted(params.items, key=key_of, reverse=params.reverse)


class UniqueInput(ActionInput):
    items: list[Any] = Field(description="Lista de entrada.")
    key: str | None = Field(default=None, description="Expressão que identifica o item.")


@register("transform.unique")
class TransformUnique(Action):
    """Remove duplicados preservando a ordem de aparição."""

    summary = "Remove itens duplicados de uma lista"
    Input: ClassVar[type[ActionInput]] = UniqueInput
    raw_params: ClassVar[frozenset[str]] = frozenset({"key"})

    async def run(self, params: UniqueInput, ctx: RunContext) -> list[Any]:
        seen: set[str] = set()
        result: list[Any] = []
        for item in params.items:
            identity = (
                item if params.key is None else render_value(params.key, ctx.snapshot(item=item))
            )
            fingerprint = repr(identity)
            if fingerprint not in seen:
                seen.add(fingerprint)
                result.append(item)
        return result


class MergeInput(ActionInput):
    sources: list[dict[str, Any]] = Field(
        description="Dicts combinados da esquerda para a direita."
    )


@register("transform.merge")
class TransformMerge(Action):
    """Combina vários dicionários num só."""

    summary = "Junta vários dicionários (o último vence)"
    Input: ClassVar[type[ActionInput]] = MergeInput

    async def run(self, params: MergeInput, ctx: RunContext) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for source in params.sources:
            merged.update(source)
        return merged


class TemplateInput(ActionInput):
    template: str = Field(description="Texto com {{ }}, renderizado com o contexto atual.")


@register("transform.template")
class TransformTemplate(Action):
    """Monta um texto livre a partir do contexto: mensagens, relatórios, e-mails."""

    summary = "Renderiza um texto usando o contexto da execução"
    Input: ClassVar[type[ActionInput]] = TemplateInput
    raw_params: ClassVar[frozenset[str]] = frozenset({"template"})

    async def run(self, params: TemplateInput, ctx: RunContext) -> Any:
        return render_value(params.template, ctx.snapshot())
