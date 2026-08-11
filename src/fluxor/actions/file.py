"""Actions de arquivo: ler entrada, gravar saída, acumular histórico em CSV.

Todo I/O de disco vai para `asyncio.to_thread`: são chamadas bloqueantes e, num
motor assíncrono, travar o event loop significa travar todos os workflows que
estão rodando ao mesmo tempo.

A única exceção é `Path.expanduser()`, que não toca no disco: resolve `~` lendo
variáveis de ambiente. O linter marca qualquer método de `pathlib` dentro de
função assíncrona, então essas três linhas levam `# noqa` explícito.
"""

from __future__ import annotations

import asyncio
import csv
import json as jsonlib
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import Field

from fluxor.actions.base import Action, ActionInput
from fluxor.context import RunContext
from fluxor.exceptions import PermanentError
from fluxor.registry import register


class ReadInput(ActionInput):
    path: str = Field(description="Caminho do arquivo.")
    encoding: str = "utf-8"
    as_json: bool = Field(default=False, description="Faz o parse do conteúdo como JSON.")
    missing_ok: bool = Field(default=False, description="Devolve None em vez de falhar.")


@register("file.read")
class FileRead(Action):
    """Lê um arquivo de texto (opcionalmente já parseando JSON)."""

    summary = "Lê o conteúdo de um arquivo"
    Input: ClassVar[type[ActionInput]] = ReadInput

    async def run(self, params: ReadInput, ctx: RunContext) -> Any:
        path = Path(params.path).expanduser()  # noqa: ASYNC240 - não toca no disco

        def _read() -> str | None:
            # `exists()` também toca o disco, então vai junto para a thread.
            if not path.exists():
                return None
            return path.read_text(encoding=params.encoding)

        content = await asyncio.to_thread(_read)

        if content is None:
            if params.missing_ok:
                return None
            raise PermanentError(f"arquivo não encontrado: {path}")

        if params.as_json:
            try:
                return jsonlib.loads(content)
            except ValueError as exc:
                raise PermanentError(f"{path} não contém JSON válido: {exc}") from exc
        return content


class WriteInput(ActionInput):
    path: str
    content: Any = Field(description="Texto a gravar. Estruturas viram JSON automaticamente.")
    mode: Literal["w", "a"] = Field(default="w", description="'w' sobrescreve, 'a' acrescenta.")
    encoding: str = "utf-8"
    create_dirs: bool = Field(default=True, description="Cria as pastas que faltarem.")
    newline: bool = Field(default=True, description="Garante quebra de linha no fim.")


@register("file.write")
class FileWrite(Action):
    """Grava conteúdo em disco."""

    summary = "Escreve (ou acrescenta) conteúdo num arquivo"
    Input: ClassVar[type[ActionInput]] = WriteInput

    async def run(self, params: WriteInput, ctx: RunContext) -> dict[str, Any]:
        path = Path(params.path).expanduser()  # noqa: ASYNC240 - não toca no disco

        content = params.content
        if not isinstance(content, str):
            content = jsonlib.dumps(content, ensure_ascii=False, indent=2, default=str)
        if params.newline and not content.endswith("\n"):
            content += "\n"

        def _write() -> None:
            if params.create_dirs:
                path.parent.mkdir(parents=True, exist_ok=True)
            with path.open(params.mode, encoding=params.encoding) as handle:
                handle.write(content)

        await asyncio.to_thread(_write)
        return {"path": str(path), "bytes": len(content.encode(params.encoding))}


class CsvAppendInput(ActionInput):
    path: str
    rows: list[dict[str, Any]] = Field(description="Linhas a acrescentar.")
    headers: list[str] | None = Field(
        default=None, description="Ordem das colunas. Sem isso, usa as chaves da 1ª linha."
    )
    encoding: str = "utf-8"
    delimiter: str = ","


@register("file.csv_append")
class FileCsvAppend(Action):
    """Acrescenta linhas a um CSV, criando o cabeçalho na primeira vez.

    É o jeito mais simples de acumular histórico: rode o workflow todo dia e o
    CSV vira sua série temporal, sem banco nenhum.
    """

    summary = "Acrescenta linhas a um arquivo CSV (cria cabeçalho se preciso)"
    Input: ClassVar[type[ActionInput]] = CsvAppendInput

    async def run(self, params: CsvAppendInput, ctx: RunContext) -> dict[str, Any]:
        if not params.rows:
            return {"path": params.path, "written": 0}

        path = Path(params.path).expanduser()  # noqa: ASYNC240 - não toca no disco
        headers = params.headers or list(params.rows[0])

        def _append() -> bool:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Decidir sobre o cabeçalho dentro da thread evita a janela entre
            # "verifiquei que não existe" e "abri para escrever".
            write_header = not path.exists() or path.stat().st_size == 0
            with path.open("a", encoding=params.encoding, newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=headers, delimiter=params.delimiter, extrasaction="ignore"
                )
                if write_header:
                    writer.writeheader()
                writer.writerows(params.rows)
            return write_header

        header_created = await asyncio.to_thread(_append)
        return {"path": str(path), "written": len(params.rows), "header_created": header_created}
