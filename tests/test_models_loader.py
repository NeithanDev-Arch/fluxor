"""Validação do schema e carregamento dos arquivos YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from fluxor.exceptions import WorkflowValidationError
from fluxor.loader import load_workflow, load_workflow_dir, parse_workflow, resolve_workflow
from fluxor.models import BackoffStrategy, OnError, TriggerType, Workflow


def minimal(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "teste",
        "steps": [{"id": "passo", "use": "notify.log", "with": {"message": "oi"}}],
    }
    base.update(overrides)
    return base


class TestSchema:
    def test_workflow_minimo_valida(self) -> None:
        workflow = Workflow.model_validate(minimal())
        assert workflow.name == "teste"
        assert workflow.trigger.type is TriggerType.MANUAL
        assert workflow.steps[0].on_error is OnError.FAIL

    def test_with_vira_params(self) -> None:
        workflow = Workflow.model_validate(minimal())
        assert workflow.steps[0].params == {"message": "oi"}

    def test_chave_desconhecida_e_rejeitada(self) -> None:
        """Erro de digitação no YAML precisa falhar, não ser ignorado."""
        with pytest.raises(ValidationError, match=r"unknown_field|extra"):
            Workflow.model_validate(minimal(unknown_field=1))

    def test_nome_invalido(self) -> None:
        with pytest.raises(ValidationError, match="inválido"):
            Workflow.model_validate(minimal(name="Nome Com Espaço"))

    def test_id_de_passo_reservado(self) -> None:
        data = minimal(steps=[{"id": "vars", "use": "notify.log"}])
        with pytest.raises(ValidationError, match="reservado"):
            Workflow.model_validate(data)

    def test_ids_duplicados(self) -> None:
        data = minimal(steps=[{"id": "a", "use": "notify.log"}, {"id": "a", "use": "notify.log"}])
        with pytest.raises(ValidationError, match="duplicado"):
            Workflow.model_validate(data)

    def test_sem_passos(self) -> None:
        with pytest.raises(ValidationError):
            Workflow.model_validate(minimal(steps=[]))

    def test_schedule_exige_cron(self) -> None:
        with pytest.raises(ValidationError, match="exige o campo 'cron'"):
            Workflow.model_validate(minimal(trigger={"type": "schedule"}))

    def test_cron_invalido(self) -> None:
        with pytest.raises(ValidationError, match="cron inválida"):
            Workflow.model_validate(minimal(trigger={"type": "schedule", "cron": "não é cron"}))

    def test_cron_valido(self) -> None:
        workflow = Workflow.model_validate(
            minimal(trigger={"type": "schedule", "cron": "0 9 * * 1-5"})
        )
        assert workflow.trigger.cron == "0 9 * * 1-5"

    def test_env_precisa_ser_maiusculo(self) -> None:
        with pytest.raises(ValidationError, match="variável de ambiente"):
            Workflow.model_validate(minimal(env=["token_minusculo"]))

    def test_retry_tem_padroes_sensatos(self) -> None:
        data = minimal(steps=[{"id": "a", "use": "notify.log", "retry": {"attempts": 5}}])
        step = Workflow.model_validate(data).steps[0]
        assert step.retry is not None
        assert step.retry.backoff is BackoffStrategy.EXPONENTIAL
        assert step.retry.jitter is True


class TestLoader:
    def test_action_inexistente_e_detectada(self) -> None:
        data = minimal(steps=[{"id": "a", "use": "nao.existe"}])
        with pytest.raises(WorkflowValidationError, match="desconhecida"):
            parse_workflow(data)

    def test_arquivo_inexistente(self) -> None:
        with pytest.raises(WorkflowValidationError, match="não encontrado"):
            load_workflow("/caminho/que/nao/existe.yaml")

    def test_yaml_malformado(self, workflows_dir: Path) -> None:
        path = workflows_dir / "quebrado.yaml"
        path.write_text("name: teste\n  steps: [\n", encoding="utf-8")
        with pytest.raises(WorkflowValidationError, match="YAML malformado"):
            load_workflow(path)

    def test_arquivo_vazio(self, workflows_dir: Path) -> None:
        path = workflows_dir / "vazio.yaml"
        path.write_text("", encoding="utf-8")
        with pytest.raises(WorkflowValidationError, match="vazio"):
            load_workflow(path)

    def test_erro_menciona_o_campo(self, workflows_dir: Path) -> None:
        path = workflows_dir / "ruim.yaml"
        path.write_text("name: ok\nsteps: []\n", encoding="utf-8")
        with pytest.raises(WorkflowValidationError) as exc:
            load_workflow(path)
        assert "steps" in str(exc.value)

    def test_carrega_pasta_ordenada(self, write_workflow, workflows_dir: Path) -> None:  # type: ignore[no-untyped-def]
        write_workflow(minimal(name="zebra"))
        write_workflow(minimal(name="alfa"))
        loaded = load_workflow_dir(workflows_dir)
        assert [workflow.name for workflow, _ in loaded] == ["alfa", "zebra"]

    def test_nomes_duplicados_em_arquivos_diferentes(
        self, write_workflow, workflows_dir: Path
    ) -> None:  # type: ignore[no-untyped-def]
        write_workflow(minimal(name="mesmo"), filename="a.yaml")
        write_workflow(minimal(name="mesmo"), filename="b.yaml")
        with pytest.raises(WorkflowValidationError, match="já usado"):
            load_workflow_dir(workflows_dir)

    def test_ignora_arquivos_com_underscore(self, write_workflow, workflows_dir: Path) -> None:  # type: ignore[no-untyped-def]
        write_workflow(minimal(name="visivel"))
        (workflows_dir / "_rascunho.yaml").write_text("lixo: [", encoding="utf-8")
        assert len(load_workflow_dir(workflows_dir)) == 1

    def test_resolve_por_nome_ou_caminho(self, write_workflow, workflows_dir: Path) -> None:  # type: ignore[no-untyped-def]
        path = write_workflow(minimal(name="achavel"))
        por_nome, _ = resolve_workflow("achavel", workflows_dir)
        por_caminho, _ = resolve_workflow(str(path), workflows_dir)
        assert por_nome.name == por_caminho.name == "achavel"


class TestExemplosDoRepositorio:
    """Os exemplos versionados precisam continuar válidos — isso roda no CI."""

    def test_todos_os_exemplos_sao_validos(self) -> None:
        examples = Path(__file__).resolve().parents[1] / "examples"
        loaded = load_workflow_dir(examples)
        assert len(loaded) >= 5
        for workflow, path in loaded:
            assert workflow.steps, f"{path.name} não tem passos"
