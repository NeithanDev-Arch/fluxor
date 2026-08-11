"""Testes da política de retry."""

from __future__ import annotations

import pytest

from fluxor.exceptions import PermanentError
from fluxor.models import BackoffStrategy, RetryConfig
from fluxor.retry import compute_delay, with_retry


async def no_sleep(_seconds: float) -> None:
    """Substitui asyncio.sleep para os testes rodarem instantaneamente."""


class TestBackoff:
    def test_fixo_nao_cresce(self) -> None:
        policy = RetryConfig(delay=2, backoff=BackoffStrategy.FIXED, jitter=False)
        assert [compute_delay(policy, n) for n in (1, 2, 3)] == [2.0, 2.0, 2.0]

    def test_linear_cresce_proporcional(self) -> None:
        policy = RetryConfig(delay=2, backoff=BackoffStrategy.LINEAR, jitter=False)
        assert [compute_delay(policy, n) for n in (1, 2, 3)] == [2.0, 4.0, 6.0]

    def test_exponencial_dobra(self) -> None:
        policy = RetryConfig(delay=1, backoff=BackoffStrategy.EXPONENTIAL, jitter=False)
        assert [compute_delay(policy, n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]

    def test_max_delay_limita(self) -> None:
        policy = RetryConfig(
            delay=10, backoff=BackoffStrategy.EXPONENTIAL, max_delay=15, jitter=False
        )
        assert compute_delay(policy, 5) == 15.0

    def test_jitter_fica_dentro_da_faixa(self) -> None:
        policy = RetryConfig(delay=10, backoff=BackoffStrategy.FIXED, max_delay=100, jitter=True)
        amostras = [compute_delay(policy, 1) for _ in range(50)]
        assert all(7.5 <= valor <= 12.5 for valor in amostras)
        assert len(set(amostras)) > 1  # tem variação de verdade


class TestWithRetry:
    async def test_sucesso_de_primeira_nao_repete(self) -> None:
        chamadas = 0

        async def operacao() -> str:
            nonlocal chamadas
            chamadas += 1
            return "ok"

        resultado, tentativas = await with_retry(operacao, RetryConfig(attempts=3), sleep=no_sleep)
        assert (resultado, tentativas, chamadas) == ("ok", 1, 1)

    async def test_repete_ate_dar_certo(self) -> None:
        chamadas = 0

        async def operacao() -> str:
            nonlocal chamadas
            chamadas += 1
            if chamadas < 3:
                raise ConnectionError("rede instável")
            return "ok"

        resultado, tentativas = await with_retry(
            operacao, RetryConfig(attempts=5, delay=0, jitter=False), sleep=no_sleep
        )
        assert (resultado, tentativas, chamadas) == ("ok", 3, 3)

    async def test_esgota_tentativas_e_propaga(self) -> None:
        chamadas = 0

        async def operacao() -> str:
            nonlocal chamadas
            chamadas += 1
            raise ConnectionError("sempre falha")

        with pytest.raises(ConnectionError, match="sempre falha"):
            await with_retry(operacao, RetryConfig(attempts=3, delay=0), sleep=no_sleep)
        assert chamadas == 3

    async def test_erro_permanente_nao_e_retentado(self) -> None:
        """O ponto central da política: 404 não melhora na terceira tentativa."""
        chamadas = 0

        async def operacao() -> str:
            nonlocal chamadas
            chamadas += 1
            raise PermanentError("recurso não existe")

        with pytest.raises(PermanentError):
            await with_retry(operacao, RetryConfig(attempts=5, delay=0), sleep=no_sleep)
        assert chamadas == 1

    async def test_sem_politica_roda_uma_vez(self) -> None:
        chamadas = 0

        async def operacao() -> str:
            nonlocal chamadas
            chamadas += 1
            raise ValueError("falhou")

        with pytest.raises(ValueError, match="falhou"):
            await with_retry(operacao, None, sleep=no_sleep)
        assert chamadas == 1

    async def test_callback_recebe_o_atraso(self) -> None:
        registros: list[tuple[int, float]] = []

        async def operacao() -> str:
            raise ConnectionError("falha")

        with pytest.raises(ConnectionError):
            await with_retry(
                operacao,
                RetryConfig(attempts=3, delay=1, backoff=BackoffStrategy.EXPONENTIAL, jitter=False),
                on_attempt_failed=lambda n, _e, d: registros.append((n, d)),
                sleep=no_sleep,
            )
        assert registros == [(1, 1.0), (2, 2.0)]
