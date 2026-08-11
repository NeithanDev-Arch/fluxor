"""Testes da biblioteca de actions.

O HTTP é testado com `respx`, que intercepta o transporte do httpx: nenhum teste
sai para a internet, e mesmo assim o caminho exercitado é o real.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from fluxor.actions.file import (
    CsvAppendInput,
    FileCsvAppend,
    FileRead,
    FileWrite,
    ReadInput,
    WriteInput,
)
from fluxor.actions.flow import AssertInput, FlowAssert, FlowFail, FlowSet, SetInput
from fluxor.actions.http import (
    GetInput,
    HttpGet,
    HttpPermanentError,
    HttpStatusError,
    RequestInput,
    perform_request,
)
from fluxor.actions.parse import CssInput, JsonInput, ParseCss, ParseJson, ParseRegex, RegexInput
from fluxor.actions.transform import (
    FilterInput,
    MapInput,
    TransformFilter,
    TransformMap,
    TransformSort,
    TransformUnique,
    UniqueInput,
)
from fluxor.context import RunContext
from fluxor.exceptions import ActionInputError, PermanentError
from fluxor.registry import all_actions, get_action, has_action

HTML = """
<html><body>
  <h1 class="titulo">Livro de Teste</h1>
  <p class="price_color">£51.77</p>
  <ul>
    <li class="item"><a href="/a">Primeiro</a></li>
    <li class="item"><a href="/b">Segundo</a></li>
  </ul>
</body></html>
"""


@pytest.fixture
def ctx() -> RunContext:
    return RunContext(run_id="teste", workflow_name="teste")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class TestRegistry:
    def test_actions_embutidas_estao_registradas(self) -> None:
        catalogo = all_actions()
        for esperado in ("http.get", "parse.css", "notify.log", "flow.set", "file.write"):
            assert esperado in catalogo, f"{esperado} sumiu do registry"

    def test_describe_expoe_os_parametros(self) -> None:
        info = get_action("http.get").describe()
        nomes = {param["name"] for param in info["params"]}
        assert "url" in nomes
        assert info["namespace"] == "http"
        assert next(p for p in info["params"] if p["name"] == "url")["required"] is True

    def test_action_inexistente(self) -> None:
        assert has_action("nao.existe") is False

    def test_parametro_desconhecido_e_rejeitado(self) -> None:
        with pytest.raises(ActionInputError, match="parâmetros inválidos"):
            HttpGet.parse_params({"url": "https://x.com", "typo_aqui": 1})


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class TestHttp:
    @respx.mock
    async def test_get_devolve_status_texto_e_json(self, ctx: RunContext) -> None:
        respx.get("https://api.teste/dados").mock(
            return_value=httpx.Response(200, json={"total": 3})
        )

        output = await HttpGet().run(GetInput(url="https://api.teste/dados"), ctx)

        assert output["status"] == 200
        assert output["ok"] is True
        assert output["json"] == {"total": 3}
        assert "total" in output["text"]
        assert output["elapsed_ms"] >= 0

    @respx.mock
    async def test_404_e_erro_permanente(self, ctx: RunContext) -> None:
        """Insistir num 404 só desperdiça tempo — por isso PermanentError."""
        respx.get("https://api.teste/sumiu").mock(return_value=httpx.Response(404, text="nada"))

        with pytest.raises(HttpPermanentError):
            await HttpGet().run(GetInput(url="https://api.teste/sumiu"), ctx)

    @respx.mock
    async def test_500_e_retentavel(self, ctx: RunContext) -> None:
        respx.get("https://api.teste/instavel").mock(return_value=httpx.Response(503, text="ops"))

        with pytest.raises(HttpStatusError) as exc:
            await HttpGet().run(GetInput(url="https://api.teste/instavel"), ctx)
        assert not isinstance(exc.value, PermanentError)

    @respx.mock
    async def test_429_e_retentavel_apesar_de_4xx(self, ctx: RunContext) -> None:
        """Rate limit é a exceção da regra: passa daqui a pouco."""
        respx.get("https://api.teste/limite").mock(return_value=httpx.Response(429))

        with pytest.raises(HttpStatusError) as exc:
            await HttpGet().run(GetInput(url="https://api.teste/limite"), ctx)
        assert not isinstance(exc.value, PermanentError)

    @respx.mock
    async def test_raise_for_status_desligado_devolve_o_erro(self, ctx: RunContext) -> None:
        respx.get("https://api.teste/404").mock(return_value=httpx.Response(404))

        output = await HttpGet().run(
            GetInput(url="https://api.teste/404", raise_for_status=False), ctx
        )
        assert output["status"] == 404
        assert output["ok"] is False

    @respx.mock
    async def test_envia_headers_e_query(self, ctx: RunContext) -> None:
        rota = respx.get("https://api.teste/busca").mock(return_value=httpx.Response(200, json={}))

        await HttpGet().run(
            GetInput(
                url="https://api.teste/busca",
                headers={"Authorization": "Bearer x"},
                params={"q": "fluxor"},
            ),
            ctx,
        )

        request = rota.calls.last.request
        assert request.headers["authorization"] == "Bearer x"
        assert "q=fluxor" in str(request.url)
        assert "Fluxor" in request.headers["user-agent"]

    @respx.mock
    async def test_post_envia_json(self, ctx: RunContext) -> None:
        rota = respx.post("https://api.teste/criar").mock(return_value=httpx.Response(201, json={}))

        await perform_request(
            RequestInput(url="https://api.teste/criar", method="POST", json={"nome": "teste"})
        )
        assert b'"nome"' in rota.calls.last.request.content


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------
class TestParse:
    async def test_css_extrai_texto(self, ctx: RunContext) -> None:
        output = await ParseCss().run(
            CssInput(html=HTML, selector="p.price_color", first=True), ctx
        )
        assert output == "£51.77"

    async def test_css_extrai_lista(self, ctx: RunContext) -> None:
        output = await ParseCss().run(CssInput(html=HTML, selector="li.item a"), ctx)
        assert output == ["Primeiro", "Segundo"]

    async def test_css_extrai_atributo(self, ctx: RunContext) -> None:
        output = await ParseCss().run(CssInput(html=HTML, selector="li.item a", attr="href"), ctx)
        assert output == ["/a", "/b"]

    async def test_css_sem_resultado_devolve_vazio(self, ctx: RunContext) -> None:
        assert await ParseCss().run(CssInput(html=HTML, selector=".nao-existe"), ctx) == []

    async def test_css_required_falha_alto(self, ctx: RunContext) -> None:
        """Site mudou de layout: melhor quebrar do que notificar valor vazio."""
        with pytest.raises(PermanentError, match="não encontrou nada"):
            await ParseCss().run(CssInput(html=HTML, selector=".nao-existe", required=True), ctx)

    async def test_json_navega_caminho(self, ctx: RunContext) -> None:
        data = {"USDBRL": {"bid": "5.43"}}
        assert await ParseJson().run(JsonInput(data=data, path="USDBRL.bid"), ctx) == "5.43"

    async def test_json_aceita_string(self, ctx: RunContext) -> None:
        assert await ParseJson().run(JsonInput(data='{"a": [10, 20]}', path="a.1"), ctx) == 20

    async def test_json_caminho_ausente_usa_default(self, ctx: RunContext) -> None:
        output = await ParseJson().run(JsonInput(data={}, path="x.y", default="vazio"), ctx)
        assert output == "vazio"

    async def test_json_required_falha(self, ctx: RunContext) -> None:
        with pytest.raises(PermanentError, match="não existe"):
            await ParseJson().run(JsonInput(data={}, path="x.y", required=True), ctx)

    async def test_regex_captura_grupo(self, ctx: RunContext) -> None:
        output = await ParseRegex().run(
            RegexInput(text="versão 2.14.3 estável", pattern=r"(\d+\.\d+\.\d+)", group=1), ctx
        )
        assert output == "2.14.3"

    async def test_regex_todas_as_ocorrencias(self, ctx: RunContext) -> None:
        output = await ParseRegex().run(
            RegexInput(text="a1 b2 c3", pattern=r"\d", **{"all": True}), ctx
        )
        assert output == ["1", "2", "3"]

    async def test_regex_invalida_e_permanente(self, ctx: RunContext) -> None:
        with pytest.raises(PermanentError, match="regex inválida"):
            await ParseRegex().run(RegexInput(text="x", pattern="[sem fechar"), ctx)


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------
class TestTransform:
    async def test_map_usa_item_e_index(self, ctx: RunContext) -> None:
        output = await TransformMap().run(
            MapInput(items=["a", "b"], expr="{{ index }}-{{ item | upper }}"), ctx
        )
        assert output == ["0-A", "1-B"]

    async def test_map_produz_dicionarios(self, ctx: RunContext) -> None:
        output = await TransformMap().run(
            MapInput(items=[{"n": 1}], expr='{{ {"dobro": item.n * 2} }}'), ctx
        )
        assert output == [{"dobro": 2}]

    async def test_filter(self, ctx: RunContext) -> None:
        output = await TransformFilter().run(
            FilterInput(items=[1, 5, 10, 20], condition="item > 5"), ctx
        )
        assert output == [10, 20]

    async def test_sort_por_chave(self, ctx: RunContext) -> None:
        from fluxor.actions.transform import SortInput

        itens = [{"n": 3}, {"n": 1}, {"n": 2}]
        output = await TransformSort().run(SortInput(items=itens, key="{{ item.n }}"), ctx)
        assert [item["n"] for item in output] == [1, 2, 3]

    async def test_unique_preserva_ordem(self, ctx: RunContext) -> None:
        output = await TransformUnique().run(UniqueInput(items=["b", "a", "b", "c", "a"]), ctx)
        assert output == ["b", "a", "c"]


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------
class TestFlow:
    async def test_set_devolve_os_valores(self, ctx: RunContext) -> None:
        output = await FlowSet().run(SetInput(values={"a": 1, "b": "x"}), ctx)
        assert output == {"a": 1, "b": "x"}

    async def test_assert_passa(self, ctx: RunContext) -> None:
        assert await FlowAssert().run(AssertInput(that=True), ctx) == {"ok": True}

    async def test_assert_falha_com_mensagem(self, ctx: RunContext) -> None:
        with pytest.raises(PermanentError, match="preço inválido"):
            await FlowAssert().run(AssertInput(that=0, message="preço inválido"), ctx)

    async def test_fail_e_permanente_por_padrao(self, ctx: RunContext) -> None:
        from fluxor.actions.flow import FailInput

        with pytest.raises(PermanentError):
            await FlowFail().run(FailInput(message="parei aqui"), ctx)


# ---------------------------------------------------------------------------
# Arquivos
# ---------------------------------------------------------------------------
class TestArquivos:
    async def test_escreve_e_le(self, ctx: RunContext, tmp_path: Path) -> None:
        alvo = tmp_path / "sub" / "saida.txt"

        resultado = await FileWrite().run(WriteInput(path=str(alvo), content="olá"), ctx)
        assert alvo.exists()
        assert resultado["bytes"] > 0

        lido = await FileRead().run(ReadInput(path=str(alvo)), ctx)
        assert lido.strip() == "olá"

    async def test_escreve_estrutura_como_json(self, ctx: RunContext, tmp_path: Path) -> None:
        alvo = tmp_path / "dados.json"
        await FileWrite().run(WriteInput(path=str(alvo), content={"a": [1, 2]}), ctx)

        lido = await FileRead().run(ReadInput(path=str(alvo), as_json=True), ctx)
        assert lido == {"a": [1, 2]}

    async def test_ler_inexistente_falha(self, ctx: RunContext, tmp_path: Path) -> None:
        with pytest.raises(PermanentError, match="não encontrado"):
            await FileRead().run(ReadInput(path=str(tmp_path / "fantasma.txt")), ctx)

    async def test_missing_ok_devolve_none(self, ctx: RunContext, tmp_path: Path) -> None:
        output = await FileRead().run(
            ReadInput(path=str(tmp_path / "fantasma.txt"), missing_ok=True), ctx
        )
        assert output is None

    async def test_csv_cria_cabecalho_uma_vez(self, ctx: RunContext, tmp_path: Path) -> None:
        alvo = tmp_path / "historico.csv"
        action = FileCsvAppend()

        primeiro = await action.run(
            CsvAppendInput(path=str(alvo), rows=[{"data": "2026-01-01", "valor": 10}]), ctx
        )
        segundo = await action.run(
            CsvAppendInput(path=str(alvo), rows=[{"data": "2026-01-02", "valor": 20}]), ctx
        )

        assert primeiro["header_created"] is True
        assert segundo["header_created"] is False

        with alvo.open(encoding="utf-8") as handle:
            linhas = list(csv.DictReader(handle))
        assert [linha["valor"] for linha in linhas] == ["10", "20"]

    async def test_csv_vazio_nao_cria_arquivo(self, ctx: RunContext, tmp_path: Path) -> None:
        alvo = tmp_path / "vazio.csv"
        resultado = await FileCsvAppend().run(CsvAppendInput(path=str(alvo), rows=[]), ctx)
        assert resultado["written"] == 0
        assert not alvo.exists()


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------
class TestShell:
    async def test_executa_e_captura_saida(self, ctx: RunContext) -> None:
        import sys

        from fluxor.actions.shell import RunInput, ShellRun

        output = await ShellRun().run(
            RunInput(command=[sys.executable, "-c", "print('oi do subprocesso')"]), ctx
        )
        assert output["returncode"] == 0
        assert "oi do subprocesso" in output["stdout"]

    async def test_comando_inexistente_e_permanente(self, ctx: RunContext) -> None:
        from fluxor.actions.shell import RunInput, ShellRun

        with pytest.raises(PermanentError, match="não encontrado"):
            await ShellRun().run(RunInput(command=["binario-que-nao-existe-xyz"]), ctx)

    async def test_codigo_de_saida_diferente_de_zero_falha(self, ctx: RunContext) -> None:
        import sys

        from fluxor.actions.shell import RunInput, ShellRun

        with pytest.raises(Exception, match="código 3"):
            await ShellRun().run(
                RunInput(command=[sys.executable, "-c", "import sys; sys.exit(3)"]), ctx
            )


# ---------------------------------------------------------------------------
# Notificações
# ---------------------------------------------------------------------------
class TestNotificacoes:
    @respx.mock
    async def test_telegram_monta_a_chamada_certa(self, ctx: RunContext) -> None:
        from fluxor.actions.notify import NotifyTelegram, TelegramInput

        rota = respx.post("https://api.telegram.org/botTOKEN123/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        output = await NotifyTelegram().run(
            TelegramInput(token="TOKEN123", chat_id="-100", text="olá"), ctx
        )

        assert output["ok"] is True
        corpo: Any = rota.calls.last.request.content
        assert b'"chat_id"' in corpo
        assert b'"parse_mode"' in corpo

    async def test_log_registra_e_devolve(self, ctx: RunContext) -> None:
        from fluxor.actions.notify import LogInput, NotifyLog

        output = await NotifyLog().run(LogInput(message="teste", level="warning"), ctx)
        assert output == {"message": "teste", "level": "warning"}
