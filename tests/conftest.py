"""Fixtures compartilhadas.

Cada teste roda com o seu próprio banco e a sua própria pasta de workflows, em
`tmp_path`. Nenhum teste enxerga o estado do outro e nada escreve no projeto.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from fluxor.config import reset_settings_cache
from fluxor.engine import Engine
from fluxor.storage import Database, RunRepository
from fluxor.template import clear_caches


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Aponta banco e workflows para o tmp do teste."""
    workflows = tmp_path / "workflows"
    workflows.mkdir(exist_ok=True)

    monkeypatch.setenv(
        "FLUXOR_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"
    )
    monkeypatch.setenv("FLUXOR_WORKFLOWS_DIR", str(workflows))
    monkeypatch.setenv("FLUXOR_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("FLUXOR_ENABLE_SCHEDULER", "false")

    reset_settings_cache()
    clear_caches()
    yield workflows
    reset_settings_cache()
    clear_caches()


@pytest.fixture
def workflows_dir(isolated_settings: Path) -> Path:
    return isolated_settings


@pytest.fixture
def write_workflow(workflows_dir: Path):  # type: ignore[no-untyped-def]
    """Grava um dicionário como arquivo YAML e devolve o caminho."""

    def _write(data: dict[str, Any], filename: str | None = None) -> Path:
        name = filename or f"{data['name']}.yaml"
        path = workflows_dir / name
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return path

    return _write


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    from fluxor.config import get_settings

    db = Database(get_settings().database_url)
    await db.create_all()
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
async def repository(database: Database) -> RunRepository:
    return RunRepository(database)


@pytest.fixture
def engine() -> Engine:
    """Motor sem persistência — a maioria dos testes não precisa de banco."""
    return Engine()
