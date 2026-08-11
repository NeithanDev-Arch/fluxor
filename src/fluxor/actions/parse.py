"""Actions de extração: HTML, JSON e regex.

O par `http.get` + `parse.css` cobre praticamente todo scraping simples sem
escrever uma linha de Python.
"""

from __future__ import annotations

import json as jsonlib
import re
from typing import Any, ClassVar

from pydantic import Field

from fluxor.actions.base import Action, ActionInput
from fluxor.context import RunContext
from fluxor.exceptions import PermanentError
from fluxor.registry import register
from fluxor.template import path_get


class CssInput(ActionInput):
    html: str = Field(description="HTML de origem, normalmente {{ steps.<id>.text }}.")
    selector: str = Field(description="Seletor CSS, ex.: '.price > span'.")
    attr: str | None = Field(
        default=None,
        description="Atributo a extrair (ex.: 'href'). Sem isso, extrai o texto.",
    )
    first: bool = Field(default=False, description="Devolve só o primeiro em vez da lista.")
    limit: int | None = Field(default=None, gt=0, description="Máximo de elementos retornados.")
    strip: bool = Field(default=True, description="Remove espaços nas pontas do texto.")
    required: bool = Field(default=False, description="Falha o passo se nada casar.")


@register("parse.css")
class ParseCss(Action):
    """Extrai texto ou atributos de um HTML usando seletor CSS."""

    summary = "Extrai valores de um HTML por seletor CSS"
    Input: ClassVar[type[ActionInput]] = CssInput

    async def run(self, params: CssInput, ctx: RunContext) -> Any:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(params.html, "html.parser")
        elements: list[Any] = list(soup.select(params.selector))
        if params.limit:
            elements = elements[: params.limit]

        values: list[str | None] = []
        for element in elements:
            if params.attr:
                raw = element.get(params.attr)
                value = " ".join(raw) if isinstance(raw, list) else raw
            else:
                value = element.get_text(" ", strip=params.strip)
            values.append(value)

        if params.required and not values:
            raise PermanentError(f"seletor '{params.selector}' não encontrou nada no HTML")

        if params.first:
            return values[0] if values else None
        return values


class JsonInput(ActionInput):
    data: Any = Field(description="Dict/lista já parseado, ou uma string JSON.")
    path: str | None = Field(
        default=None,
        description="Caminho pontilhado: 'items.0.name'. Sem isso devolve tudo.",
    )
    default: Any = Field(default=None, description="Valor quando o caminho não existe.")
    required: bool = Field(default=False, description="Falha o passo se o caminho não existir.")


@register("parse.json")
class ParseJson(Action):
    """Navega um JSON por caminho pontilhado, sem encadear ifs."""

    summary = "Lê um caminho dentro de um JSON ('data.items.0.price')"
    Input: ClassVar[type[ActionInput]] = JsonInput

    async def run(self, params: JsonInput, ctx: RunContext) -> Any:
        data = params.data
        if isinstance(data, str):
            try:
                data = jsonlib.loads(data)
            except ValueError as exc:
                raise PermanentError(f"conteúdo não é JSON válido: {exc}") from exc

        if params.path is None:
            return data

        value = path_get(data, params.path, params.default)
        if params.required and value is None:
            raise PermanentError(f"caminho '{params.path}' não existe no JSON")
        return value


class RegexInput(ActionInput):
    text: str
    pattern: str = Field(description="Expressão regular Python.")
    group: int | str = Field(default=0, description="Índice ou nome do grupo de captura.")
    find_all: bool = Field(default=False, alias="all", description="Devolve todas as ocorrências.")
    ignore_case: bool = False
    required: bool = Field(default=False, description="Falha o passo se não casar.")


@register("parse.regex")
class ParseRegex(Action):
    """Extrai valores com expressão regular."""

    summary = "Extrai trechos de um texto por regex"
    Input: ClassVar[type[ActionInput]] = RegexInput

    async def run(self, params: RegexInput, ctx: RunContext) -> Any:
        flags = re.IGNORECASE if params.ignore_case else 0
        try:
            regex = re.compile(params.pattern, flags)
        except re.error as exc:
            raise PermanentError(f"regex inválida {params.pattern!r}: {exc}") from exc

        if params.find_all:
            matches = [m.group(params.group) for m in regex.finditer(params.text)]
            if params.required and not matches:
                raise PermanentError(f"regex {params.pattern!r} não encontrou nada")
            return matches

        match = regex.search(params.text)
        if match is None:
            if params.required:
                raise PermanentError(f"regex {params.pattern!r} não encontrou nada")
            return None
        return match.group(params.group)
