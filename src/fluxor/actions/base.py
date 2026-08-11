"""Contrato que toda action cumpre.

Uma action é uma classe com três coisas: um nome (`http.get`), um schema de
entrada (`Input`, um modelo Pydantic) e um método `run` assíncrono. O schema não
é burocracia — é ele que produz mensagens de erro decentes quando alguém erra o
YAML, e é dele que o `fluxor actions` e o dashboard extraem a documentação.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from fluxor.context import RunContext
from fluxor.exceptions import ActionInputError


class ActionInput(BaseModel):
    """Base dos schemas de entrada. `extra="forbid"` pega chave digitada errado."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Action(ABC):
    """Classe base de todas as actions."""

    name: ClassVar[str] = ""
    summary: ClassVar[str] = ""
    Input: ClassVar[type[ActionInput]] = ActionInput

    raw_params: ClassVar[frozenset[str]] = frozenset()
    """Campos que o motor **não** deve renderizar antes de entregar.

    Serve para parâmetros que são, eles próprios, templates avaliados dentro da
    action — como o `expr` do `transform.map`, que precisa de `{{ item }}` e só
    a action sabe o valor de `item`.
    """

    @abstractmethod
    async def run(self, params: Any, ctx: RunContext) -> Any:
        """Executa a action e devolve a saída, exposta em `{{ steps.<id> }}`."""

    # -- utilitários ------------------------------------------------------
    @classmethod
    def parse_params(cls, raw: dict[str, Any]) -> ActionInput:
        """Valida o `with:` contra o schema, com erro legível."""
        try:
            return cls.Input.model_validate(raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or '(raiz)'}: {err['msg']}"
                for err in exc.errors()
            )
            raise ActionInputError(f"parâmetros inválidos para '{cls.name}' -> {problems}") from exc

    @classmethod
    def describe(cls) -> dict[str, Any]:
        """Metadados da action, usados pela CLI (`fluxor actions`) e pela API."""
        schema = cls.Input.model_json_schema()
        required = set(schema.get("required", []))
        properties = schema.get("properties", {})

        params = [
            {
                "name": key,
                "type": _readable_type(spec),
                "required": key in required,
                "default": spec.get("default"),
                "description": spec.get("description"),
            }
            for key, spec in properties.items()
        ]
        return {
            "name": cls.name,
            "summary": cls.summary,
            "namespace": cls.name.split(".")[0] if "." in cls.name else "geral",
            "params": sorted(params, key=lambda p: (not p["required"], p["name"])),
        }


def _readable_type(spec: dict[str, Any]) -> str:
    """Traduz um pedaço de JSON Schema para algo que cabe numa tabela."""
    if "anyOf" in spec:
        parts = [_readable_type(option) for option in spec["anyOf"] if option.get("type") != "null"]
        return " | ".join(dict.fromkeys(parts)) or "any"
    if "enum" in spec:
        return " | ".join(str(v) for v in spec["enum"])
    mapping = {
        "string": "str",
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "array": "list",
        "object": "dict",
        "null": "null",
    }
    return mapping.get(spec.get("type", ""), "any")
