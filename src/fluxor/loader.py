"""Carregamento e validação dos arquivos YAML.

Erro de validação aqui vira mensagem em português apontando o campo exato. É de
propósito: a maior parte do tempo perdido com ferramenta declarativa é entender
*onde* o arquivo está errado.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from fluxor.exceptions import WorkflowValidationError
from fluxor.models import Workflow
from fluxor.registry import action_names, has_action

WORKFLOW_SUFFIXES = (".yaml", ".yml")


def _format_validation_error(error: ValidationError) -> str:
    problems = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "(raiz)"
        problems.append(f"  • {location}: {item['msg']}")
    return "workflow inválido:\n" + "\n".join(problems)


def parse_workflow(
    data: dict[str, Any],
    *,
    path: str | None = None,
    validate_actions: bool = True,
) -> Workflow:
    """Valida um dicionário já carregado e devolve o :class:`Workflow`."""
    if not isinstance(data, dict):
        raise WorkflowValidationError("o arquivo precisa conter um objeto YAML no topo", path=path)

    try:
        workflow = Workflow.model_validate(data)
    except ValidationError as exc:
        raise WorkflowValidationError(_format_validation_error(exc), path=path) from exc

    if validate_actions:
        unknown = sorted({step.use for step in workflow.all_steps if not has_action(step.use)})
        if unknown:
            available = ", ".join(action_names())
            raise WorkflowValidationError(
                f"action(s) desconhecida(s): {', '.join(unknown)}.\n  Disponíveis: {available}",
                path=path,
            )

    return workflow


def load_workflow(path: str | Path, *, validate_actions: bool = True) -> Workflow:
    """Lê um arquivo YAML e devolve o workflow validado."""
    file_path = Path(path).expanduser()

    if not file_path.exists():
        raise WorkflowValidationError("arquivo não encontrado", path=str(file_path))

    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise WorkflowValidationError(f"YAML malformado: {exc}", path=str(file_path)) from exc

    if raw is None:
        raise WorkflowValidationError("arquivo vazio", path=str(file_path))

    return parse_workflow(raw, path=str(file_path), validate_actions=validate_actions)


def load_workflow_dir(
    directory: str | Path, *, validate_actions: bool = True
) -> list[tuple[Workflow, Path]]:
    """Carrega todos os workflows de uma pasta, ordenados por nome.

    Levanta se dois arquivos declararem o mesmo `name`, porque nomes duplicados
    quebrariam o agendador e o histórico silenciosamente.
    """
    base = Path(directory).expanduser()
    if not base.is_dir():
        raise WorkflowValidationError("diretório de workflows não encontrado", path=str(base))

    loaded: list[tuple[Workflow, Path]] = []
    seen: dict[str, Path] = {}

    for file_path in sorted(base.iterdir()):
        if file_path.suffix.lower() not in WORKFLOW_SUFFIXES or file_path.name.startswith("_"):
            continue

        workflow = load_workflow(file_path, validate_actions=validate_actions)
        if workflow.name in seen:
            raise WorkflowValidationError(
                f"nome '{workflow.name}' já usado por {seen[workflow.name].name}",
                path=str(file_path),
            )
        seen[workflow.name] = file_path
        loaded.append((workflow, file_path))

    return sorted(loaded, key=lambda pair: pair[0].name)


def resolve_workflow(
    reference: str, workflows_dir: str | Path, *, validate_actions: bool = True
) -> tuple[Workflow, Path]:
    """Aceita tanto um caminho de arquivo quanto o `name` de um workflow da pasta."""
    candidate = Path(reference).expanduser()
    if candidate.is_file():
        return load_workflow(candidate, validate_actions=validate_actions), candidate

    for workflow, path in load_workflow_dir(workflows_dir, validate_actions=validate_actions):
        if workflow.name == reference or path.stem == reference:
            return workflow, path

    raise WorkflowValidationError(
        f"não achei o workflow '{reference}' (nem como arquivo, nem em {workflows_dir})"
    )
