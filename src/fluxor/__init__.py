"""Fluxor: motor de automações declarativas.

Você descreve o fluxo em YAML; o Fluxor resolve as dependências entre passos,
executa com retry e timeout, grava cada execução no banco e expõe tudo em uma
API + dashboard.

Uso típico como biblioteca::

    import asyncio
    from fluxor import Engine, load_workflow

    workflow = load_workflow("examples/hello-world.yaml")
    record = asyncio.run(Engine().execute(workflow))
    print(record.status)
"""

from fluxor.context import RunContext, RunStatus, StepResult, StepStatus
from fluxor.engine import Engine, RunRecord
from fluxor.exceptions import (
    ActionInputError,
    ActionNotFound,
    FluxorError,
    PermanentError,
    StepExecutionError,
    WorkflowValidationError,
)
from fluxor.loader import load_workflow, load_workflow_dir, parse_workflow
from fluxor.models import RetryConfig, Step, Trigger, Workflow
from fluxor.registry import register

__version__ = "0.1.0"

__all__ = [
    "ActionInputError",
    "ActionNotFound",
    "Engine",
    "FluxorError",
    "PermanentError",
    "RetryConfig",
    "RunContext",
    "RunRecord",
    "RunStatus",
    "Step",
    "StepExecutionError",
    "StepResult",
    "StepStatus",
    "Trigger",
    "Workflow",
    "WorkflowValidationError",
    "__version__",
    "load_workflow",
    "load_workflow_dir",
    "parse_workflow",
    "register",
]
