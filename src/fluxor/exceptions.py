"""Hierarquia de erros do Fluxor.

A distinção que importa para o motor é entre erro **transitório** (vale tentar
de novo: timeout de rede, 503, conexão recusada) e erro **permanente** (tentar
de novo dá exatamente o mesmo resultado: YAML inválido, campo obrigatório
faltando, 404). Só o primeiro grupo passa pela política de retry.
"""

from __future__ import annotations


class FluxorError(Exception):
    """Base de todos os erros do projeto."""


class PermanentError(FluxorError):
    """Erro que não deve ser retentado — tentar de novo daria o mesmo resultado."""


class WorkflowValidationError(PermanentError):
    """O arquivo YAML não descreve um workflow válido."""

    def __init__(self, message: str, *, path: str | None = None) -> None:
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


class ActionNotFound(PermanentError):
    """O `use:` do passo aponta para uma action que não existe no registry."""

    def __init__(self, name: str, available: list[str] | None = None) -> None:
        self.name = name
        self.available = available or []
        message = f"action '{name}' não encontrada"
        if self.available:
            hint = ", ".join(sorted(self.available)[:12])
            message += f" (disponíveis: {hint}...)"
        super().__init__(message)


class ActionInputError(PermanentError):
    """Os parâmetros em `with:` não batem com o schema da action."""


class TemplateError(PermanentError):
    """Falha ao renderizar uma expressão `{{ ... }}`."""


class StepExecutionError(FluxorError):
    """A action rodou mas falhou. Carrega o passo para o log e o dashboard."""

    def __init__(self, step_id: str, message: str, *, cause: BaseException | None = None) -> None:
        self.step_id = step_id
        self.cause = cause
        super().__init__(f"passo '{step_id}' falhou: {message}")


class WorkflowTimeout(FluxorError):
    """O workflow inteiro estourou o `timeout` declarado."""
