"""Testes da camada de templates."""

from __future__ import annotations

import pytest

from fluxor.exceptions import TemplateError
from fluxor.template import (
    brl,
    evaluate_condition,
    path_get,
    render_value,
    resolve_expression,
    to_number,
)


class TestTipagemNativa:
    """Uma string que é só uma expressão preserva o tipo original."""

    def test_expressao_pura_devolve_int(self) -> None:
        assert render_value("{{ vars.limite }}", {"vars": {"limite": 2500}}) == 2500
        assert isinstance(render_value("{{ vars.limite }}", {"vars": {"limite": 2500}}), int)

    def test_expressao_pura_devolve_lista(self) -> None:
        context = {"vars": {"itens": [1, 2, 3]}}
        assert render_value("{{ vars.itens }}", context) == [1, 2, 3]

    def test_expressao_pura_devolve_dict(self) -> None:
        result = render_value('{{ {"a": 1} }}', {})
        assert result == {"a": 1}

    def test_interpolacao_parcial_vira_texto(self) -> None:
        result = render_value("Teto: {{ vars.limite }}", {"vars": {"limite": 2500}})
        assert result == "Teto: 2500"
        assert isinstance(result, str)

    def test_string_sem_template_passa_intacta(self) -> None:
        assert render_value("apenas texto", {}) == "apenas texto"

    def test_duas_expressoes_coladas_sao_texto(self) -> None:
        """Regressão: `{{ a }}:{{ b }}` já foi confundido com uma expressão só."""
        assert render_value("{{ a }}:{{ b }}", {"a": 0, "b": "x"}) == "0:x"
        assert render_value("{{ a }}{{ b }}", {"a": 1, "b": 2}) == "12"

    def test_dict_aninhado_continua_expressao_unica(self) -> None:
        """As chaves finais de `}}` do dict não podem confundir a detecção."""
        result = render_value('{{ {"a": {"b": 1}} }}', {})
        assert result == {"a": {"b": 1}}

    def test_expressao_com_espacos_ao_redor(self) -> None:
        assert render_value("  {{ n }}  ", {"n": 5}) == 5

    def test_bloco_de_controle_vira_texto(self) -> None:
        result = render_value("{% for i in itens %}{{ i }},{% endfor %}", {"itens": [1, 2]})
        assert result == "1,2,"

    def test_renderiza_estruturas_aninhadas(self) -> None:
        context = {"vars": {"url": "https://exemplo.com", "n": 3}}
        raw = {"url": "{{ vars.url }}", "opcoes": ["{{ vars.n }}", "fixo"]}
        assert render_value(raw, context) == {
            "url": "https://exemplo.com",
            "opcoes": [3, "fixo"],
        }


class TestFiltros:
    @pytest.mark.parametrize(
        ("entrada", "esperado"),
        [
            ("R$ 2.499,90", 2499.90),  # pt-BR
            ("1,234.56", 1234.56),  # en-US
            ("£51.77", 51.77),
            ("42", 42.0),
            ("2499,90", 2499.90),
            (17, 17.0),
        ],
    )
    def test_to_number_entende_varios_formatos(self, entrada: object, esperado: float) -> None:
        assert to_number(entrada) == pytest.approx(esperado)

    def test_to_number_falha_em_texto_sem_numero(self) -> None:
        with pytest.raises(ValueError, match="não consegui extrair"):
            to_number("indisponível")

    def test_brl_formata_moeda(self) -> None:
        assert brl(2499.9) == "R$ 2.499,90"
        assert brl("5.43") == "R$ 5,43"

    def test_path_get_navega_estrutura(self) -> None:
        data = {"data": {"itens": [{"nome": "primeiro"}]}}
        assert path_get(data, "data.itens.0.nome") == "primeiro"
        assert path_get(data, "data.nao.existe", "padrão") == "padrão"

    def test_filtro_no_template(self) -> None:
        assert render_value("{{ preco | to_number }}", {"preco": "R$ 10,50"}) == 10.5


class TestCondicoes:
    def test_aceita_com_e_sem_chaves(self) -> None:
        context = {"vars": {"n": 10}}
        assert evaluate_condition("vars.n > 5", context) is True
        assert evaluate_condition("{{ vars.n > 5 }}", context) is True

    def test_compara_numeros_como_numeros(self) -> None:
        """Regressão: "9" > "10" é verdadeiro em string e falso em número."""
        assert evaluate_condition("vars.n > vars.teto", {"vars": {"n": 9, "teto": 10}}) is False

    @pytest.mark.parametrize("valor", ["", "false", "no", "0", "none"])
    def test_strings_falsas_de_api(self, valor: str) -> None:
        assert evaluate_condition("vars.flag", {"vars": {"flag": valor}}) is False


class TestSeguranca:
    def test_sandbox_bloqueia_acesso_a_internos(self) -> None:
        """Caminho clássico de fuga de template: chegar em __class__ e subir o MRO."""
        with pytest.raises(TemplateError):
            resolve_expression("''.__class__.__mro__", {})

    def test_variavel_inexistente_falha_alto(self) -> None:
        """Melhor um erro claro do que um passo recebendo string vazia."""
        with pytest.raises(TemplateError):
            render_value("{{ vars.nao_existe }}", {"vars": {}})

    def test_filtro_default_cobre_ausente(self) -> None:
        assert render_value("{{ vars.ausente | default('padrão') }}", {"vars": {}}) == "padrão"
