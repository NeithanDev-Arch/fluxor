"""Actions HTTP — a porta de entrada da maioria das automações.

Detalhe que muda a vida em produção: **4xx não é retentado, 5xx é.** Um 404
continuará 404 na terceira tentativa; um 503 costuma passar. A separação vem da
classe do erro (`PermanentError` vs. erro comum), então a política de retry do
passo não precisa saber nada sobre HTTP.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar, Literal

import httpx
from pydantic import Field

from fluxor.actions.base import Action, ActionInput
from fluxor.config import get_settings
from fluxor.context import RunContext
from fluxor.exceptions import FluxorError, PermanentError
from fluxor.registry import register

# Códigos 4xx que valem uma nova tentativa: timeout de request, too early, rate limit.
RETRYABLE_CLIENT_ERRORS = frozenset({408, 425, 429})


class HttpStatusError(FluxorError):
    """Resposta com status de erro que pode melhorar numa nova tentativa (5xx, 429)."""


class HttpPermanentError(PermanentError):
    """Resposta 4xx — insistir não muda nada."""


class RequestInput(ActionInput):
    url: str = Field(description="URL completa, com esquema.")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict, description="Query string.")
    json_body: Any = Field(default=None, alias="json", description="Corpo enviado como JSON.")
    data: Any = Field(default=None, description="Corpo form-urlencoded ou texto puro.")
    timeout: float | None = Field(
        default=None, description="Segundos; default = FLUXOR_HTTP_TIMEOUT."
    )
    follow_redirects: bool = True
    raise_for_status: bool = Field(
        default=True,
        description="Se false, status de erro vira saída normal em vez de exceção.",
    )
    user_agent: str = "Fluxor/0.1 (+https://github.com/NeithanDev-Arch/fluxor)"


async def perform_request(params: RequestInput) -> dict[str, Any]:
    """Executa a requisição e devolve a saída padronizada do passo."""
    settings = get_settings()
    timeout = params.timeout or settings.http_timeout
    headers = {"User-Agent": params.user_agent, **params.headers}

    started = time.perf_counter()
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=params.follow_redirects
    ) as client:
        response = await client.request(
            params.method,
            params.url,
            headers=headers,
            params=params.params or None,
            json=params.json_body,
            data=params.data,
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    payload: Any = None
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            payload = response.json()
        except ValueError:
            payload = None

    result = {
        "status": response.status_code,
        "ok": response.is_success,
        "url": str(response.url),
        "headers": dict(response.headers),
        "text": response.text,
        "json": payload,
        "elapsed_ms": elapsed_ms,
    }

    if params.raise_for_status and not response.is_success:
        preview = response.text[:300].replace("\n", " ")
        message = f"{params.method} {params.url} respondeu {response.status_code}: {preview}"
        if (
            400 <= response.status_code < 500
            and response.status_code not in RETRYABLE_CLIENT_ERRORS
        ):
            raise HttpPermanentError(message)
        raise HttpStatusError(message)

    return result


@register("http.request")
class HttpRequest(Action):
    """Requisição HTTP com método livre."""

    summary = "Faz uma requisição HTTP com o método que você escolher"
    Input: ClassVar[type[ActionInput]] = RequestInput

    async def run(self, params: RequestInput, ctx: RunContext) -> dict[str, Any]:
        return await perform_request(params)


class GetInput(RequestInput):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "GET"


@register("http.get")
class HttpGet(Action):
    """Atalho para GET — o caso de 80% dos workflows."""

    summary = "Busca uma URL (GET) e devolve status, texto e JSON já parseado"
    Input: ClassVar[type[ActionInput]] = GetInput

    async def run(self, params: GetInput, ctx: RunContext) -> dict[str, Any]:
        params.method = "GET"
        return await perform_request(params)


class PostInput(RequestInput):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "POST"


@register("http.post")
class HttpPost(Action):
    """Atalho para POST."""

    summary = "Envia um POST (JSON por padrão) e devolve a resposta"
    Input: ClassVar[type[ActionInput]] = PostInput

    async def run(self, params: PostInput, ctx: RunContext) -> dict[str, Any]:
        params.method = "POST"
        return await perform_request(params)
