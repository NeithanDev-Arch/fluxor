"""Política de retry com backoff.

Regra do motor: erro que herda de :class:`~fluxor.exceptions.PermanentError`
nunca é retentado. Não adianta pedir de novo um campo obrigatório que não existe
ou insistir num 404 — só atrasa a falha e polui o log.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from fluxor.exceptions import PermanentError
from fluxor.models import BackoffStrategy, RetryConfig

T = TypeVar("T")

# Callback (tentativa, erro, segundos até a próxima) — usado para logar.
AttemptCallback = Callable[[int, BaseException, float], None]


def compute_delay(policy: RetryConfig, attempt: int) -> float:
    """Segundos de espera depois da tentativa `attempt` (1-indexada).

    ``fixed``       -> delay
    ``linear``      -> delay xtentativa
    ``exponential`` -> delay x2^(tentativa-1)

    O resultado é limitado por ``max_delay`` e, com ``jitter``, recebe ±25% de
    ruído para que N workers que falharam juntos não voltem juntos.
    """
    if policy.backoff is BackoffStrategy.FIXED:
        delay = policy.delay
    elif policy.backoff is BackoffStrategy.LINEAR:
        delay = policy.delay * attempt
    else:
        delay = policy.delay * (2 ** (attempt - 1))

    delay = min(delay, policy.max_delay)

    if policy.jitter and delay > 0:
        delay *= random.uniform(0.75, 1.25)
        delay = min(delay, policy.max_delay)

    return round(max(0.0, delay), 3)


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    policy: RetryConfig | None = None,
    *,
    on_attempt_failed: AttemptCallback | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[T, int]:
    """Executa `operation` respeitando a política e devolve `(resultado, tentativas)`.

    `sleep` é injetável para que os testes rodem em milissegundos em vez de
    esperar de verdade.
    """
    attempts = policy.attempts if policy else 1
    last_error: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            return await operation(), attempt
        except (PermanentError, asyncio.CancelledError):
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            delay = compute_delay(policy, attempt) if policy else 0.0
            if on_attempt_failed:
                on_attempt_failed(attempt, exc, delay)
            if delay:
                await sleep(delay)

    assert last_error is not None
    raise last_error
