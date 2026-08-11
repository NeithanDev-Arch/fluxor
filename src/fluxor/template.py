"""Camada de templates: o `{{ ... }}` que aparece no YAML.

Duas decisões que valem ser explicadas:

**1. Ambiente sandboxed.** O YAML pode vir de qualquer lugar (um PR, um usuário
do dashboard). `SandboxedEnvironment` bloqueia acesso a atributos internos, então
`{{ ''.__class__.__mro__ }}`, o caminho clássico para escapar de um template,
não funciona.

**2. Tipagem nativa.** Se a string é *exatamente* uma expressão, o resultado sai
com o tipo original em vez de virar texto::

    limite: "{{ vars.teto }}"        -> 2500      (int, dá para comparar)
    msg:    "Teto de {{ vars.teto }}" -> "Teto de 2500" (str, como esperado)

Sem isso, todo `when:` numérico viraria comparação de string e `"9" > "10"`
seria verdadeiro, e esse é o tipo de bug que custa uma tarde inteira.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from jinja2 import StrictUndefined, Undefined, nodes
from jinja2 import TemplateError as JinjaTemplateError
from jinja2.exceptions import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment

from fluxor.exceptions import TemplateError

# Detecta se há qualquer marcação Jinja na string
HAS_TEMPLATE_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)

_environment: SandboxedEnvironment | None = None
_expression_cache: dict[str, Any] = {}
_single_expression_cache: dict[str, str | None] = {}


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------
def to_number(value: Any) -> float:
    """Converte texto bagunçado em número, entendendo formato pt-BR.

    ``"R$ 2.499,90"`` -> ``2499.9`` · ``"1,234.56"`` -> ``1234.56`` · ``"42"`` -> ``42.0``
    """
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)

    text = re.sub(r"[^\d,.\-]", "", str(value).strip())
    if not text or text in {"-", ".", ","}:
        raise ValueError(f"não consegui extrair um número de {value!r}")

    if "," in text and "." in text:
        # o separador que aparece por último é o decimal
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")  # 1.234,56 (pt-BR)
        else:
            text = text.replace(",", "")  # 1,234.56 (en-US)
    elif "," in text:
        head, _, tail = text.rpartition(",")
        # 2 casas depois da vírgula => decimal; senão é separador de milhar
        text = f"{head}.{tail}" if len(tail) in (1, 2) else text.replace(",", "")

    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"não consegui extrair um número de {value!r}") from exc


def to_int(value: Any) -> int:
    return int(to_number(value))


def to_json(value: Any, indent: int | None = None) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent, default=str)


def from_json(value: str) -> Any:
    return json.loads(value)


def brl(value: Any) -> str:
    """Formata como moeda brasileira: ``2499.9`` -> ``"R$ 2.499,90"``."""
    number = to_number(value)
    inteiro, _, centavos = f"{number:,.2f}".partition(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def slugify(value: Any) -> str:
    text = re.sub(r"[^\w\s-]", "", str(value).lower(), flags=re.UNICODE).strip()
    return re.sub(r"[\s_-]+", "-", text)


def strip_html(value: Any) -> str:
    """Remove tags e devolve só o texto visível."""
    from bs4 import BeautifulSoup

    return BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)


def regex_search(value: Any, pattern: str, group: int = 0) -> str | None:
    match = re.search(pattern, str(value))
    return match.group(group) if match else None


def regex_replace(value: Any, pattern: str, replacement: str = "") -> str:
    return re.sub(pattern, replacement, str(value))


def path_get(value: Any, dotted: str, default: Any = None) -> Any:
    """Navega estruturas aninhadas com uma string: ``'data.items.0.name'``.

    Poupa o encadeamento de `if` quando a API pode devolver o caminho vazio.
    """
    current = value
    for part in dotted.split("."):
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(part, None)
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            current = getattr(current, part, None)
    return default if current is None else current


def b64encode(value: Any) -> str:
    return base64.b64encode(str(value).encode()).decode()


def sha256(value: Any) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def as_list(value: Any) -> list[Any]:
    """Garante uma lista. Útil quando a API devolve ora item, ora array."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


FILTERS = {
    "to_number": to_number,
    "to_int": to_int,
    "to_json": to_json,
    "from_json": from_json,
    "brl": brl,
    "slugify": slugify,
    "strip_html": strip_html,
    "regex_search": regex_search,
    "regex_replace": regex_replace,
    "path": path_get,
    "b64": b64encode,
    "sha256": sha256,
    "as_list": as_list,
}


# ---------------------------------------------------------------------------
# Ambiente
# ---------------------------------------------------------------------------
def build_environment() -> SandboxedEnvironment:
    env = SandboxedEnvironment(
        undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True
    )
    env.filters.update(FILTERS)
    env.globals.update(
        {
            "now": lambda: datetime.now(UTC),
            "today": lambda: datetime.now(UTC).date().isoformat(),
            "timestamp": lambda: int(datetime.now(UTC).timestamp()),
            "uuid4": lambda: str(uuid.uuid4()),
        }
    )
    return env


def get_environment() -> SandboxedEnvironment:
    global _environment
    if _environment is None:
        _environment = build_environment()
    return _environment


# ---------------------------------------------------------------------------
# Renderização
# ---------------------------------------------------------------------------
def single_expression_source(value: str) -> str | None:
    """Se `value` for *apenas* uma expressão, devolve o código dela; senão, None.

    A checagem é feita na árvore sintática do Jinja, não por regex. Uma tentativa
    anterior com `^\\s*\\{\\{(.+?)\\}\\}\\s*$` parecia funcionar e casava
    ``"{{ a }}:{{ b }}"`` inteiro por causa do backtracking: o par de chaves do
    meio era engolido e a interpolação virava expressão inválida. Perguntar ao
    parser quantos nós de saída existem não tem esse tipo de canto escuro.
    """
    if value in _single_expression_cache:
        return _single_expression_cache[value]

    source = value.strip()
    result: str | None = None

    try:
        tree = get_environment().parse(source)
    except TemplateSyntaxError:
        tree = None

    if tree is not None and len(tree.body) == 1 and isinstance(tree.body[0], nodes.Output):
        outputs = tree.body[0].nodes
        # Um único nó que não é texto literal => "{{ ... }}" e nada mais.
        if len(outputs) == 1 and not isinstance(outputs[0], nodes.TemplateData):
            start, end = source.find("{{"), source.rfind("}}")
            if start != -1 and end > start:
                result = source[start + 2 : end]

    _single_expression_cache[value] = result
    return result


def _compile_expression(source: str) -> Any:
    cached = _expression_cache.get(source)
    if cached is None:
        cached = get_environment().compile_expression(source, undefined_to_none=False)
        _expression_cache[source] = cached
    return cached


def render_expression(source: str, context: dict[str, Any]) -> Any:
    """Avalia uma expressão Jinja e devolve o valor com o tipo nativo."""
    try:
        result = _compile_expression(source)(**context)
    except JinjaTemplateError as exc:
        raise TemplateError(f"erro ao avaliar '{{{{{source}}}}}': {exc}") from exc
    except Exception as exc:  # erros vindos dos próprios filtros
        raise TemplateError(f"erro ao avaliar '{{{{{source}}}}}': {exc}") from exc

    if isinstance(result, Undefined):
        raise TemplateError(f"'{{{{{source}}}}}' não existe no contexto")
    return result


def render_string(source: str, context: dict[str, Any]) -> str:
    """Renderiza uma string como template, sempre devolvendo texto."""
    try:
        return get_environment().from_string(source).render(**context)
    except JinjaTemplateError as exc:
        raise TemplateError(f"erro ao renderizar {source!r}: {exc}") from exc
    except Exception as exc:
        raise TemplateError(f"erro ao renderizar {source!r}: {exc}") from exc


def render_value(value: Any, context: dict[str, Any]) -> Any:
    """Renderiza qualquer valor do YAML, recursivamente.

    Strings viram texto interpolado, *exceto* quando são uma única expressão;
    aí o tipo original é preservado. Dicts e listas são percorridos inteiros.
    """
    if isinstance(value, str):
        if not HAS_TEMPLATE_RE.search(value):
            return value
        expression = single_expression_source(value)
        if expression is not None:
            return render_expression(expression, context)
        return render_string(value, context)

    if isinstance(value, dict):
        return {key: render_value(item, context) for key, item in value.items()}

    if isinstance(value, list):
        return [render_value(item, context) for item in value]

    return value


def resolve_expression(source: str, context: dict[str, Any]) -> Any:
    """Avalia uma expressão escrita com ou sem as chaves.

    Campos como `when:` e `foreach:` já são expressões por natureza, então tanto
    ``steps.itens.json`` quanto ``{{ steps.itens.json }}`` funcionam, e ninguém
    precisa lembrar de qual dos dois é o certo.
    """
    return render_expression(single_expression_source(source) or source.strip(), context)


def evaluate_condition(source: str, context: dict[str, Any]) -> bool:
    """Avalia um `when:`. Aceita tanto ``a > b`` quanto ``{{ a > b }}``."""
    result = resolve_expression(source, context)
    if isinstance(result, str):
        # "false"/"no"/"0" vindos de uma API são falsos por conveniência
        return result.strip().lower() not in {"", "false", "no", "0", "none", "null"}
    return bool(result)


def clear_caches() -> None:
    """Zera o ambiente e o cache de expressões (usado nos testes)."""
    global _environment
    _environment = None
    _expression_cache.clear()
    _single_expression_cache.clear()
