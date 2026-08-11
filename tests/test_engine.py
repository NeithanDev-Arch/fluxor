"""Testes do motor de execução.

As actions `t.*` daqui existem só para os testes: elas permitem controlar
exatamente quantas vezes uma chamada falha, quanto tempo demora e o que devolve.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, ClassVar

import pytest

from fluxor.actions.base import Action, ActionInput
from fluxor.context import RunStatus, StepStatus
from fluxor.engine import Engine
from fluxor.exceptions import PermanentError
from fluxor.models import Workflow
from fluxor.registry import register

CHAMADAS: dict[str, int] = defaultdict(int)


# ---------------------------------------------------------------------------
# Actions de teste
# ---------------------------------------------------------------------------
class EchoInput(ActionInput):
    value: Any = None


@register("t.echo")
class EchoAction(Action):
    summary = "devolve o que recebeu"
    Input: ClassVar[type[ActionInput]] = EchoInput

    async def run(self, params: EchoInput, ctx: Any) -> Any:
        CHAMADAS["echo"] += 1
        return params.value


class BoomInput(ActionInput):
    key: str = "padrao"
    fail_times: int = 999
    permanent: bool = False


@register("t.boom")
class BoomAction(Action):
    summary = "falha um número controlado de vezes"
    Input: ClassVar[type[ActionInput]] = BoomInput

    async def run(self, params: BoomInput, ctx: Any) -> Any:
        CHAMADAS[params.key] += 1
        if CHAMADAS[params.key] <= params.fail_times:
            if params.permanent:
                raise PermanentError("erro permanente")
            raise RuntimeError(f"falha número {CHAMADAS[params.key]}")
        return {"tentativas": CHAMADAS[params.key]}


class SlowInput(ActionInput):
    seconds: float = 1.0


@register("t.slow")
class SlowAction(Action):
    summary = "demora de propósito"
    Input: ClassVar[type[ActionInput]] = SlowInput

    async def run(self, params: SlowInput, ctx: Any) -> str:
        await asyncio.sleep(params.seconds)
        return "acordou"


@register("t.env")
class EnvAction(Action):
    summary = "devolve o ambiente visível para o workflow"
    Input: ClassVar[type[ActionInput]] = ActionInput

    async def run(self, params: ActionInput, ctx: Any) -> dict[str, str]:
        return dict(ctx.env)


@pytest.fixture(autouse=True)
def limpar_chamadas() -> None:
    CHAMADAS.clear()


def build(steps: list[dict[str, Any]], **extra: Any) -> Workflow:
    return Workflow.model_validate({"name": "teste", "steps": steps, **extra})


# ---------------------------------------------------------------------------
# Fluxo básico
# ---------------------------------------------------------------------------
class TestFluxoBasico:
    async def test_execucao_bem_sucedida(self, engine: Engine) -> None:
        workflow = build([{"id": "a", "use": "t.echo", "with": {"value": 42}}])
        record = await engine.execute(workflow)

        assert record.status is RunStatus.SUCCESS
        assert record.ok
        assert record.results[0].output == 42
        assert record.counts == {"success": 1, "failed": 0, "skipped": 0}
        assert record.duration_ms >= 0

    async def test_saida_alimenta_o_proximo_passo(self, engine: Engine) -> None:
        workflow = build(
            [
                {"id": "primeiro", "use": "t.echo", "with": {"value": {"total": 10}}},
                {
                    "id": "segundo",
                    "use": "t.echo",
                    "with": {"value": "{{ steps.primeiro.total * 2 }}"},
                },
            ]
        )
        record = await engine.execute(workflow)
        assert record.results[1].output == 20  # inteiro, não a string "20"

    async def test_vars_podem_referenciar_vars_anteriores(self, engine: Engine) -> None:
        workflow = build(
            [{"id": "a", "use": "t.echo", "with": {"value": "{{ vars.completo }}"}}],
            vars={"base": "https://api.exemplo.com", "completo": "{{ vars.base }}/v1"},
        )
        record = await engine.execute(workflow)
        assert record.results[0].output == "https://api.exemplo.com/v1"

    async def test_extra_vars_sobrescreve(self, engine: Engine) -> None:
        workflow = build(
            [{"id": "a", "use": "t.echo", "with": {"value": "{{ vars.teto }}"}}],
            vars={"teto": 100},
        )
        record = await engine.execute(workflow, extra_vars={"teto": 7})
        assert record.results[0].output == 7

    async def test_callback_recebe_cada_passo(self, engine: Engine) -> None:
        vistos: list[str] = []
        workflow = build(
            [
                {"id": "a", "use": "t.echo", "with": {"value": 1}},
                {"id": "b", "use": "t.echo", "with": {"value": 2}},
            ]
        )
        await engine.execute(workflow, on_step=lambda result: vistos.append(result.step_id))
        assert vistos == ["a", "b"]


# ---------------------------------------------------------------------------
# Condições
# ---------------------------------------------------------------------------
class TestCondicoes:
    async def test_when_falso_pula_sem_falhar(self, engine: Engine) -> None:
        workflow = build(
            [{"id": "a", "use": "t.echo", "when": "1 > 2", "with": {"value": "nunca"}}]
        )
        record = await engine.execute(workflow)

        assert record.status is RunStatus.SUCCESS
        assert record.results[0].status is StepStatus.SKIPPED
        assert CHAMADAS["echo"] == 0

    async def test_when_verdadeiro_executa(self, engine: Engine) -> None:
        workflow = build(
            [{"id": "a", "use": "t.echo", "when": "vars.n > 5", "with": {"value": "ok"}}],
            vars={"n": 10},
        )
        record = await engine.execute(workflow)
        assert record.results[0].status is StepStatus.SUCCESS

    async def test_passo_pulado_nao_publica_saida(self, engine: Engine) -> None:
        """Depender da saída de um passo pulado precisa falhar de forma clara."""
        workflow = build(
            [
                {"id": "pulado", "use": "t.echo", "when": "false", "with": {"value": 1}},
                {"id": "usa", "use": "t.echo", "with": {"value": "{{ steps.pulado }}"}},
            ]
        )
        record = await engine.execute(workflow)
        assert record.status is RunStatus.FAILED
        assert "pulado" in (record.error or "")


# ---------------------------------------------------------------------------
# Falhas e retry
# ---------------------------------------------------------------------------
class TestFalhas:
    async def test_falha_aborta_por_padrao(self, engine: Engine) -> None:
        workflow = build(
            [
                {"id": "quebra", "use": "t.boom", "with": {"key": "k1"}},
                {"id": "nunca", "use": "t.echo", "with": {"value": 1}},
            ]
        )
        record = await engine.execute(workflow)

        assert record.status is RunStatus.FAILED
        assert len(record.results) == 1  # o segundo passo não chegou a rodar
        assert CHAMADAS["echo"] == 0

    async def test_on_error_continue_segue_e_marca_parcial(self, engine: Engine) -> None:
        workflow = build(
            [
                {"id": "quebra", "use": "t.boom", "with": {"key": "k2"}, "on_error": "continue"},
                {"id": "segue", "use": "t.echo", "with": {"value": "rodei"}},
            ]
        )
        record = await engine.execute(workflow)

        assert record.status is RunStatus.PARTIAL
        assert record.results[0].status is StepStatus.FAILED
        assert record.results[1].output == "rodei"

    async def test_retry_ate_o_sucesso(self, engine: Engine) -> None:
        workflow = build(
            [
                {
                    "id": "instavel",
                    "use": "t.boom",
                    "with": {"key": "k3", "fail_times": 2},
                    "retry": {"attempts": 5, "delay": 0, "jitter": False},
                }
            ]
        )
        record = await engine.execute(workflow)

        assert record.status is RunStatus.SUCCESS
        assert record.results[0].attempts == 3
        assert record.results[0].output == {"tentativas": 3}

    async def test_erro_permanente_ignora_retry(self, engine: Engine) -> None:
        workflow = build(
            [
                {
                    "id": "permanente",
                    "use": "t.boom",
                    "with": {"key": "k4", "permanent": True},
                    "retry": {"attempts": 5, "delay": 0},
                }
            ]
        )
        record = await engine.execute(workflow)

        assert record.status is RunStatus.FAILED
        assert CHAMADAS["k4"] == 1  # uma única chamada, sem repetição

    async def test_parametro_invalido_e_erro_de_schema(self, engine: Engine) -> None:
        workflow = build([{"id": "a", "use": "t.boom", "with": {"campo_inexistente": 1}}])
        record = await engine.execute(workflow)

        assert record.status is RunStatus.FAILED
        assert "parâmetros inválidos" in (record.results[0].error or "")

    async def test_timeout_de_passo(self, engine: Engine) -> None:
        workflow = build(
            [{"id": "lento", "use": "t.slow", "with": {"seconds": 5}, "timeout": 0.05}]
        )
        record = await engine.execute(workflow)
        assert record.status is RunStatus.FAILED

    async def test_timeout_de_workflow(self, engine: Engine) -> None:
        workflow = build([{"id": "lento", "use": "t.slow", "with": {"seconds": 5}}], timeout=0.05)
        record = await engine.execute(workflow)

        assert record.status is RunStatus.FAILED
        assert "timeout" in (record.error or "")

    async def test_vars_quebradas_falham_antes_de_executar(self, engine: Engine) -> None:
        workflow = build(
            [{"id": "a", "use": "t.echo", "with": {"value": 1}}],
            vars={"ruim": "{{ nao.existe }}"},
        )
        record = await engine.execute(workflow)

        assert record.status is RunStatus.FAILED
        assert "vars" in (record.error or "")
        assert CHAMADAS["echo"] == 0


# ---------------------------------------------------------------------------
# on_failure
# ---------------------------------------------------------------------------
class TestOnFailure:
    async def test_compensacao_roda_com_a_mensagem_de_erro(self, engine: Engine) -> None:
        workflow = build(
            [{"id": "quebra", "use": "t.boom", "with": {"key": "k5"}}],
            on_failure=[{"id": "avisa", "use": "t.echo", "with": {"value": "erro: {{ error }}"}}],
        )
        record = await engine.execute(workflow)

        assert record.status is RunStatus.FAILED
        compensacao = record.results[-1]
        assert compensacao.step_id == "avisa"
        assert "falha número 1" in compensacao.output

    async def test_compensacao_nao_roda_no_sucesso(self, engine: Engine) -> None:
        workflow = build(
            [{"id": "ok", "use": "t.echo", "with": {"value": 1}}],
            on_failure=[{"id": "avisa", "use": "t.echo", "with": {"value": "não deveria"}}],
        )
        record = await engine.execute(workflow)
        assert [result.step_id for result in record.results] == ["ok"]


# ---------------------------------------------------------------------------
# foreach
# ---------------------------------------------------------------------------
class TestForeach:
    async def test_roda_uma_vez_por_item(self, engine: Engine) -> None:
        workflow = build(
            [
                {
                    "id": "loop",
                    "use": "t.echo",
                    "foreach": "{{ vars.itens }}",
                    "with": {"value": "{{ index }}:{{ item }}"},
                }
            ],
            vars={"itens": ["a", "b", "c"]},
        )
        record = await engine.execute(workflow)

        assert record.results[0].output == ["0:a", "1:b", "2:c"]
        assert CHAMADAS["echo"] == 3

    async def test_preserva_a_ordem_de_entrada(self, engine: Engine) -> None:
        """Execução é concorrente, mas a saída sai na ordem da lista original."""
        workflow = build(
            [
                {
                    "id": "loop",
                    "use": "t.echo",
                    "foreach": "{{ range(10) | list }}",
                    "with": {"value": "{{ item }}"},
                }
            ]
        )
        record = await engine.execute(workflow)
        assert record.results[0].output == list(range(10))

    async def test_lista_vazia_produz_lista_vazia(self, engine: Engine) -> None:
        workflow = build(
            [{"id": "loop", "use": "t.echo", "foreach": "{{ vars.itens }}", "with": {"value": 1}}],
            vars={"itens": []},
        )
        record = await engine.execute(workflow)
        assert record.results[0].output == []

    async def test_foreach_que_nao_e_lista_falha(self, engine: Engine) -> None:
        workflow = build(
            [{"id": "loop", "use": "t.echo", "foreach": "{{ vars.n }}", "with": {"value": 1}}],
            vars={"n": 42},
        )
        record = await engine.execute(workflow)

        assert record.status is RunStatus.FAILED
        assert "lista" in (record.results[0].error or "")

    async def test_falha_em_um_item_falha_o_passo(self, engine: Engine) -> None:
        workflow = build(
            [
                {
                    "id": "loop",
                    "use": "t.boom",
                    "foreach": "{{ [1, 2] }}",
                    "with": {"key": "loop-{{ item }}"},
                }
            ]
        )
        record = await engine.execute(workflow)
        assert record.status is RunStatus.FAILED


# ---------------------------------------------------------------------------
# Ambiente e dry-run
# ---------------------------------------------------------------------------
class TestAmbienteEDryRun:
    async def test_so_o_env_declarado_fica_visivel(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FLUXOR_TESTE_LIBERADO", "visivel")
        monkeypatch.setenv("FLUXOR_TESTE_SEGREDO", "invisivel")

        workflow = build([{"id": "a", "use": "t.env"}], env=["FLUXOR_TESTE_LIBERADO"])
        record = await engine.execute(workflow)

        assert record.results[0].output == {"FLUXOR_TESTE_LIBERADO": "visivel"}

    async def test_env_ausente_nao_derruba_a_carga(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FLUXOR_NAO_DEFINIDA", raising=False)
        workflow = build([{"id": "a", "use": "t.env"}], env=["FLUXOR_NAO_DEFINIDA"])
        record = await engine.execute(workflow)
        assert record.results[0].output == {}

    async def test_dry_run_nao_executa_a_action(self, engine: Engine) -> None:
        workflow = build(
            [{"id": "a", "use": "t.echo", "with": {"value": "{{ vars.x }}"}}], vars={"x": 9}
        )
        record = await engine.execute(workflow, dry_run=True)

        assert record.status is RunStatus.SUCCESS
        assert CHAMADAS["echo"] == 0
        assert record.results[0].output["dry_run"] is True
        assert record.results[0].output["params"] == {"value": 9}  # já renderizado

    async def test_dry_run_ainda_valida_parametros(self, engine: Engine) -> None:
        workflow = build([{"id": "a", "use": "t.boom", "with": {"nao_existe": 1}}])
        record = await engine.execute(workflow, dry_run=True)
        assert record.status is RunStatus.FAILED


# ---------------------------------------------------------------------------
# Integração com o sink
# ---------------------------------------------------------------------------
class TestSink:
    async def test_sink_recebe_inicio_passos_e_fim(self) -> None:
        eventos: list[str] = []

        class SinkFake:
            async def start_run(self, record: Any) -> None:
                eventos.append("start")

            async def save_step(self, run_id: str, index: int, result: Any) -> None:
                eventos.append(f"step:{result.step_id}")

            async def finish_run(self, record: Any) -> None:
                eventos.append(f"finish:{record.status.value}")

        engine = Engine(sink=SinkFake())
        workflow = build([{"id": "a", "use": "t.echo", "with": {"value": 1}}])
        await engine.execute(workflow)

        assert eventos == ["start", "step:a", "finish:success"]

    async def test_sink_quebrado_nao_derruba_a_execucao(self) -> None:
        """Telemetria é best-effort: banco fora do ar não pode matar a automação."""

        class SinkQuebrado:
            async def start_run(self, record: Any) -> None:
                raise ConnectionError("banco fora do ar")

            async def save_step(self, run_id: str, index: int, result: Any) -> None:
                raise ConnectionError("banco fora do ar")

            async def finish_run(self, record: Any) -> None:
                raise ConnectionError("banco fora do ar")

        engine = Engine(sink=SinkQuebrado())
        record = await engine.execute(build([{"id": "a", "use": "t.echo", "with": {"value": 1}}]))

        assert record.status is RunStatus.SUCCESS
        assert record.results[0].output == 1
