"""Actions de notificação — o último passo de quase todo workflow útil.

Uma automação que roda e não avisa ninguém é um cron job. O que transforma isso
em produto é o alerta chegar onde a pessoa já está olhando.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Any, ClassVar, Literal

from pydantic import Field

from fluxor.actions.base import Action, ActionInput
from fluxor.actions.http import RequestInput, perform_request
from fluxor.context import RunContext
from fluxor.logging_setup import get_logger
from fluxor.registry import register

log = get_logger("fluxor.notify")


class LogInput(ActionInput):
    message: str
    level: Literal["debug", "info", "warning", "error"] = "info"
    data: dict[str, Any] = Field(
        default_factory=dict, description="Campos extras no log estruturado."
    )


@register("notify.log")
class NotifyLog(Action):
    """Emite uma linha no log estruturado. Ótimo para desenvolver e depurar."""

    summary = "Escreve uma mensagem no log da execução"
    Input: ClassVar[type[ActionInput]] = LogInput

    async def run(self, params: LogInput, ctx: RunContext) -> dict[str, Any]:
        getattr(log, params.level)(
            params.message, run_id=ctx.run_id, workflow=ctx.workflow_name, **params.data
        )
        return {"message": params.message, "level": params.level}


class TelegramInput(ActionInput):
    token: str = Field(description="Token do bot — use {{ env.TELEGRAM_BOT_TOKEN }}.")
    chat_id: str | int = Field(description="ID do chat/grupo de destino.")
    text: str
    parse_mode: Literal["HTML", "Markdown", "MarkdownV2", "none"] = "HTML"
    disable_notification: bool = False


@register("notify.telegram")
class NotifyTelegram(Action):
    """Manda mensagem por um bot do Telegram."""

    summary = "Envia uma mensagem no Telegram"
    Input: ClassVar[type[ActionInput]] = TelegramInput

    async def run(self, params: TelegramInput, ctx: RunContext) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": params.chat_id,
            "text": params.text,
            "disable_notification": params.disable_notification,
        }
        if params.parse_mode != "none":
            payload["parse_mode"] = params.parse_mode

        response = await perform_request(
            RequestInput(
                url=f"https://api.telegram.org/bot{params.token}/sendMessage",
                method="POST",
                json=payload,
            )
        )
        return {"ok": response["ok"], "status": response["status"], "response": response["json"]}


class DiscordInput(ActionInput):
    webhook_url: str
    content: str = Field(max_length=2000)
    username: str | None = None


@register("notify.discord")
class NotifyDiscord(Action):
    """Publica em um canal do Discord via webhook."""

    summary = "Envia uma mensagem no Discord"
    Input: ClassVar[type[ActionInput]] = DiscordInput

    async def run(self, params: DiscordInput, ctx: RunContext) -> dict[str, Any]:
        payload: dict[str, Any] = {"content": params.content}
        if params.username:
            payload["username"] = params.username

        response = await perform_request(
            RequestInput(url=params.webhook_url, method="POST", json=payload)
        )
        return {"ok": response["ok"], "status": response["status"]}


class WebhookInput(ActionInput):
    url: str
    payload: Any = Field(default=None, description="Corpo JSON enviado.")
    method: Literal["POST", "PUT", "PATCH"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)


@register("notify.webhook")
class NotifyWebhook(Action):
    """Dispara um webhook genérico — Slack, n8n, Zapier, seu próprio backend."""

    summary = "Envia um POST JSON para uma URL qualquer"
    Input: ClassVar[type[ActionInput]] = WebhookInput

    async def run(self, params: WebhookInput, ctx: RunContext) -> dict[str, Any]:
        response = await perform_request(
            RequestInput(
                url=params.url,
                method=params.method,
                json=params.payload,
                headers=params.headers,
            )
        )
        return {"ok": response["ok"], "status": response["status"], "response": response["json"]}


class EmailInput(ActionInput):
    host: str = Field(description="Servidor SMTP, ex.: smtp.gmail.com.")
    port: int = 587
    username: str | None = None
    password: str | None = None
    use_tls: bool = Field(default=True, description="STARTTLS (porta 587).")
    use_ssl: bool = Field(default=False, description="SSL direto (porta 465).")
    sender: str = Field(alias="from", description="Remetente.")
    to: list[str] | str
    subject: str
    body: str
    html: bool = Field(default=False, description="Envia o corpo como HTML.")


@register("notify.email")
class NotifyEmail(Action):
    """Envia e-mail por SMTP.

    `smtplib` é bloqueante, então a chamada inteira vai para uma thread —
    o event loop continua livre para os outros workflows.
    """

    summary = "Envia um e-mail via SMTP"
    Input: ClassVar[type[ActionInput]] = EmailInput

    async def run(self, params: EmailInput, ctx: RunContext) -> dict[str, Any]:
        recipients = [params.to] if isinstance(params.to, str) else list(params.to)

        message = EmailMessage()
        message["From"] = params.sender
        message["To"] = ", ".join(recipients)
        message["Subject"] = params.subject
        if params.html:
            message.set_content("Seu cliente de e-mail não suporta HTML.")
            message.add_alternative(params.body, subtype="html")
        else:
            message.set_content(params.body)

        def _send() -> None:
            factory = smtplib.SMTP_SSL if params.use_ssl else smtplib.SMTP
            with factory(params.host, params.port, timeout=30) as server:
                if params.use_tls and not params.use_ssl:
                    server.starttls()
                if params.username and params.password:
                    server.login(params.username, params.password)
                server.send_message(message)

        await asyncio.to_thread(_send)
        return {"sent": True, "recipients": recipients, "subject": params.subject}
