"""Execução de comandos do sistema.

Nota de segurança, porque isso importa: um workflow com `shell.run` faz o que o
usuário do processo puder fazer. Por isso o padrão é a forma segura: uma lista
de argumentos, executada **sem** shell, sem interpolação do interpretador.
`shell: true` existe para quando você precisa de pipe ou redirecionamento, e
nesse modo você é responsável por não interpolar entrada não confiável.

Se você expõe o dashboard para outras pessoas, rode o Fluxor em container com
usuário sem privilégio (o Dockerfile do repositório já faz isso).
"""

from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any, ClassVar

from pydantic import Field, model_validator

from fluxor.actions.base import Action, ActionInput
from fluxor.context import RunContext
from fluxor.exceptions import FluxorError, PermanentError
from fluxor.registry import register


class RunInput(ActionInput):
    command: list[str] | str = Field(description="Lista de argumentos (recomendado) ou string.")
    shell: bool = Field(default=False, description="Interpreta via shell. Use com cuidado.")
    cwd: str | None = Field(default=None, description="Diretório de trabalho.")
    env: dict[str, str] = Field(default_factory=dict, description="Variáveis extras do processo.")
    inherit_env: bool = Field(default=True, description="Herda o ambiente do processo Fluxor.")
    timeout: float = Field(default=300.0, gt=0, le=3600)
    check: bool = Field(default=True, description="Falha o passo se o código de saída não for 0.")
    encoding: str = "utf-8"

    @model_validator(mode="after")
    def _validate_command(self) -> RunInput:
        if isinstance(self.command, list) and not self.command:
            raise ValueError("'command' não pode ser uma lista vazia")
        if isinstance(self.command, str) and not self.command.strip():
            raise ValueError("'command' não pode ser vazio")
        return self


@register("shell.run")
class ShellRun(Action):
    """Roda um comando e captura saída, erro e código de retorno."""

    summary = "Executa um comando do sistema e captura a saída"
    Input: ClassVar[type[ActionInput]] = RunInput

    async def run(self, params: RunInput, ctx: RunContext) -> dict[str, Any]:
        environment = {**os.environ, **params.env} if params.inherit_env else dict(params.env)

        if params.shell:
            command_line = (
                params.command if isinstance(params.command, str) else shlex.join(params.command)
            )
            process = await asyncio.create_subprocess_shell(
                command_line,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=params.cwd,
                env=environment,
            )
            printable = command_line
        else:
            argv = (
                params.command if isinstance(params.command, list) else shlex.split(params.command)
            )
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=params.cwd,
                    env=environment,
                )
            except FileNotFoundError as exc:
                raise PermanentError(f"comando não encontrado: {argv[0]}") from exc
            printable = shlex.join(argv)

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=params.timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise FluxorError(f"comando estourou {params.timeout}s: {printable}") from None

        saida = stdout.decode(params.encoding, errors="replace").strip()
        erro = stderr.decode(params.encoding, errors="replace").strip()
        result: dict[str, Any] = {
            "command": printable,
            "returncode": process.returncode,
            "stdout": saida,
            "stderr": erro,
        }

        if params.check and process.returncode != 0:
            detalhe = erro or saida or "(sem saída)"
            raise FluxorError(f"comando saiu com código {process.returncode}: {detalhe[:500]}")

        return result
