"""Testes de integração: banco, API HTTP e CLI.

Aqui os componentes são exercitados juntos: um workflow real é executado, o
resultado é gravado, lido pela API e mostrado pela CLI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from fluxor.cli import app as cli_app
from fluxor.engine import Engine
from fluxor.models import Workflow
from fluxor.storage import Database, RunRepository

WORKFLOW_SIMPLES: dict[str, Any] = {
    "name": "exemplo",
    "description": "workflow de teste",
    "vars": {"nome": "mundo"},
    "steps": [
        {"id": "montar", "use": "flow.set", "with": {"values": {"texto": "olá {{ vars.nome }}"}}},
        {"id": "registrar", "use": "notify.log", "with": {"message": "{{ steps.montar.texto }}"}},
    ],
}

WORKFLOW_QUEBRADO: dict[str, Any] = {
    "name": "quebrado",
    "steps": [{"id": "falha", "use": "flow.fail", "with": {"message": "erro proposital"}}],
}


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------
class TestRepositorio:
    async def test_grava_e_recupera_uma_execucao(self, repository: RunRepository) -> None:
        engine = Engine(sink=repository)
        record = await engine.execute(Workflow.model_validate(WORKFLOW_SIMPLES))

        detalhe = await repository.get_run(record.id)
        assert detalhe is not None
        assert detalhe["workflow"] == "exemplo"
        assert detalhe["status"] == "success"
        assert [passo["step_id"] for passo in detalhe["steps"]] == ["montar", "registrar"]
        assert detalhe["steps"][0]["output"] == {"texto": "olá mundo"}

    async def test_lista_com_paginacao_e_filtro(self, repository: RunRepository) -> None:
        engine = Engine(sink=repository)
        for _ in range(3):
            await engine.execute(Workflow.model_validate(WORKFLOW_SIMPLES))
        await engine.execute(Workflow.model_validate(WORKFLOW_QUEBRADO))

        todas = await repository.list_runs(limit=10)
        assert todas["total"] == 4

        falhas = await repository.list_runs(status="failed")
        assert falhas["total"] == 1
        assert falhas["items"][0]["workflow"] == "quebrado"

        por_nome = await repository.list_runs(workflow="exemplo")
        assert por_nome["total"] == 3

        pagina = await repository.list_runs(limit=2, offset=2)
        assert len(pagina["items"]) == 2

    async def test_execucao_inexistente(self, repository: RunRepository) -> None:
        assert await repository.get_run("nao-existe") is None

    async def test_stats_agrega_o_periodo(self, repository: RunRepository) -> None:
        engine = Engine(sink=repository)
        await engine.execute(Workflow.model_validate(WORKFLOW_SIMPLES))
        await engine.execute(Workflow.model_validate(WORKFLOW_QUEBRADO))

        stats = await repository.stats(days=7)
        assert stats["total_runs"] == 2
        assert stats["success_rate"] == 50.0
        assert len(stats["by_day"]) == 7
        assert sum(dia["total"] for dia in stats["by_day"]) == 2
        assert {item["workflow"] for item in stats["by_workflow"]} == {"exemplo", "quebrado"}

    async def test_saida_gigante_e_truncada(self, database: Database) -> None:
        """Uma página HTML de 2 MB não pode virar linha de banco."""
        repository = RunRepository(database, max_output_bytes=1000)
        gigante = "x" * 50_000
        workflow = Workflow.model_validate(
            {
                "name": "grande",
                "steps": [{"id": "a", "use": "flow.set", "with": {"values": {"html": gigante}}}],
            }
        )
        record = await Engine(sink=repository).execute(workflow)

        # Durante a execução o valor completo circula normalmente...
        assert len(record.results[0].output["html"]) == 50_000
        # ...mas o que foi persistido está cortado.
        detalhe = await repository.get_run(record.id)
        assert detalhe is not None
        assert detalhe["steps"][0]["output"]["_truncated"] is True

    async def test_purge_remove_antigas(self, repository: RunRepository) -> None:
        await Engine(sink=repository).execute(Workflow.model_validate(WORKFLOW_SIMPLES))

        assert await repository.purge(older_than_days=30) == 0  # é recente, fica
        assert await repository.purge(older_than_days=0) == 1  # tudo antes de agora, sai
        assert (await repository.list_runs())["total"] == 0


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@pytest.fixture
def client(workflows_dir: Path):  # type: ignore[no-untyped-def]
    """TestClient com dois workflows na pasta e o lifespan ativo."""
    for data in (WORKFLOW_SIMPLES, WORKFLOW_QUEBRADO):
        (workflows_dir / f"{data['name']}.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    from fluxor.api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


class TestApi:
    def test_health(self, client: TestClient) -> None:
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["workflows"] == 2
        assert body["scheduler"] is False

    def test_lista_workflows(self, client: TestClient) -> None:
        body = client.get("/api/workflows").json()
        assert body["total"] == 2
        assert {item["name"] for item in body["items"]} == {"exemplo", "quebrado"}

    def test_detalha_workflow(self, client: TestClient) -> None:
        body = client.get("/api/workflows/exemplo").json()
        assert body["description"] == "workflow de teste"
        assert len(body["steps"]) == 2
        assert body["steps"][0]["with"] == {"values": {"texto": "olá {{ vars.nome }}"}}

    def test_workflow_inexistente_da_404(self, client: TestClient) -> None:
        assert client.get("/api/workflows/fantasma").status_code == 404

    def test_executa_pela_api(self, client: TestClient) -> None:
        response = client.post("/api/workflows/exemplo/run", json={"vars": {"nome": "fluxor"}})
        body = response.json()

        assert response.status_code == 200
        assert body["status"] == "success"
        assert body["steps"][0]["output"] == {"texto": "olá fluxor"}
        assert body["trigger"] == "api"

    def test_execucao_com_falha_retorna_200_com_status_failed(self, client: TestClient) -> None:
        """O disparo funcionou; quem falhou foi o workflow. São coisas diferentes."""
        response = client.post("/api/workflows/quebrado/run", json={})
        assert response.status_code == 200
        assert response.json()["status"] == "failed"

    def test_dry_run_pela_api(self, client: TestClient) -> None:
        body = client.post("/api/workflows/exemplo/run", json={"dry_run": True}).json()
        assert body["dry_run"] is True
        assert body["steps"][0]["output"]["dry_run"] is True

    def test_historico_aparece_apos_execucao(self, client: TestClient) -> None:
        client.post("/api/workflows/exemplo/run", json={})
        body = client.get("/api/runs?limit=5").json()

        assert body["total"] >= 1
        run_id = body["items"][0]["id"]
        detalhe = client.get(f"/api/runs/{run_id}").json()
        assert detalhe["workflow"] == "exemplo"
        assert len(detalhe["steps"]) == 2

    def test_run_inexistente_da_404(self, client: TestClient) -> None:
        assert client.get("/api/runs/inexistente").status_code == 404

    def test_stats(self, client: TestClient) -> None:
        client.post("/api/workflows/exemplo/run", json={})
        body = client.get("/api/stats?days=7").json()
        assert body["total_runs"] >= 1
        assert body["success_rate"] > 0

    def test_catalogo_de_actions(self, client: TestClient) -> None:
        body = client.get("/api/actions").json()
        assert body["total"] > 10
        assert any(item["name"] == "http.get" for item in body["items"])

    def test_reload_relê_o_disco(self, client: TestClient, workflows_dir: Path) -> None:
        (workflows_dir / "novo.yaml").write_text(
            yaml.safe_dump({**WORKFLOW_SIMPLES, "name": "recem-criado"}, allow_unicode=True),
            encoding="utf-8",
        )
        assert client.post("/api/workflows/reload").json()["loaded"] == 3
        assert client.get("/api/workflows/recem-criado").status_code == 200

    def test_webhook_exige_token(self, client: TestClient, workflows_dir: Path) -> None:
        (workflows_dir / "hook.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "com-hook",
                    "trigger": {"type": "webhook", "token": "segredo"},
                    "steps": [
                        {
                            "id": "eco",
                            "use": "flow.set",
                            "with": {"values": {"recebido": "{{ vars.payload.x | default('') }}"}},
                        }
                    ],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        client.post("/api/workflows/reload")

        assert client.post("/api/hooks/com-hook?token=errado", json={}).status_code == 403
        assert client.post("/api/hooks/com-hook", json={}).status_code == 403

        ok = client.post("/api/hooks/com-hook?token=segredo", json={"x": "veio"})
        assert ok.status_code == 200
        assert ok.json()["status"] == "success"

    def test_webhook_recusa_workflow_que_nao_e_webhook(self, client: TestClient) -> None:
        assert client.post("/api/hooks/exemplo", json={}).status_code == 400

    def test_dashboard_e_servido(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert "Fluxor" in response.text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestCli:
    def test_versao(self, runner: CliRunner) -> None:
        result = runner.invoke(cli_app, ["--version"])
        assert result.exit_code == 0
        assert "fluxor" in result.stdout

    def test_validate_aprova_workflow_bom(self, runner: CliRunner, write_workflow) -> None:  # type: ignore[no-untyped-def]
        path = write_workflow(WORKFLOW_SIMPLES)
        result = runner.invoke(cli_app, ["validate", str(path)])
        assert result.exit_code == 0
        assert "sem erros" in result.stdout

    def test_validate_reprova_workflow_ruim(self, runner: CliRunner, workflows_dir: Path) -> None:
        ruim = workflows_dir / "ruim.yaml"
        ruim.write_text("name: x\nsteps:\n  - id: a\n    use: nao.existe\n", encoding="utf-8")

        result = runner.invoke(cli_app, ["validate", str(ruim)])
        assert result.exit_code == 1
        assert "desconhecida" in result.stdout

    def test_list_mostra_a_pasta(self, runner: CliRunner, write_workflow) -> None:  # type: ignore[no-untyped-def]
        write_workflow(WORKFLOW_SIMPLES)
        result = runner.invoke(cli_app, ["list"])
        assert result.exit_code == 0
        assert "exemplo" in result.stdout

    def test_actions_lista_o_catalogo(self, runner: CliRunner) -> None:
        result = runner.invoke(cli_app, ["actions"])
        assert result.exit_code == 0
        assert "http.get" in result.stdout

    def test_actions_detalha_uma(self, runner: CliRunner) -> None:
        result = runner.invoke(cli_app, ["actions", "http.get"])
        assert result.exit_code == 0
        assert "url" in result.stdout

    def test_run_executa_e_sai_com_zero(self, runner: CliRunner, write_workflow) -> None:  # type: ignore[no-untyped-def]
        write_workflow(WORKFLOW_SIMPLES)
        result = runner.invoke(cli_app, ["run", "exemplo"])
        assert result.exit_code == 0
        assert "SUCCESS" in result.stdout

    def test_run_com_var_sobrescreve(self, runner: CliRunner, write_workflow) -> None:  # type: ignore[no-untyped-def]
        write_workflow(WORKFLOW_SIMPLES)
        result = runner.invoke(cli_app, ["run", "exemplo", "--var", 'nome="fluxor"', "--json"])
        assert result.exit_code == 0
        assert "fluxor" in result.stdout

    def test_run_falho_sai_com_um(self, runner: CliRunner, write_workflow) -> None:  # type: ignore[no-untyped-def]
        """Exit code correto é o que permite usar o Fluxor dentro de um cron/CI."""
        write_workflow(WORKFLOW_QUEBRADO)
        result = runner.invoke(cli_app, ["run", "quebrado"])
        assert result.exit_code == 1
        assert "FAILED" in result.stdout

    def test_run_workflow_inexistente(self, runner: CliRunner) -> None:
        result = runner.invoke(cli_app, ["run", "nao-existe"])
        assert result.exit_code == 1

    def test_runs_e_show(self, runner: CliRunner, write_workflow) -> None:  # type: ignore[no-untyped-def]
        write_workflow(WORKFLOW_SIMPLES)
        runner.invoke(cli_app, ["run", "exemplo"])

        listagem = runner.invoke(cli_app, ["runs"])
        assert listagem.exit_code == 0
        assert "exemplo" in listagem.stdout

    def test_init_cria_exemplo(self, runner: CliRunner, tmp_path: Path) -> None:
        destino = tmp_path / "novos"
        result = runner.invoke(cli_app, ["init", str(destino)])

        assert result.exit_code == 0
        assert (destino / "meu-primeiro-workflow.yaml").exists()

    def test_init_nao_sobrescreve(self, runner: CliRunner, tmp_path: Path) -> None:
        destino = tmp_path / "novos"
        runner.invoke(cli_app, ["init", str(destino)])
        segunda = runner.invoke(cli_app, ["init", str(destino)])
        assert segunda.exit_code == 1
