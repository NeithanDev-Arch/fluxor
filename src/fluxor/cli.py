"""Interface de linha de comando do Fluxor.

Regra de ouro daqui: qualquer comando que executa algo devolve exit code
coerente (0 = sucesso, 1 = falha). É isso que permite usar o Fluxor dentro de
um cron, de um GitHub Action ou de um `&&` sem surpresa.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from fluxor import __version__
from fluxor.config import get_settings
from fluxor.context import StepResult, StepStatus
from fluxor.engine import Engine, RunRecord
from fluxor.exceptions import FluxorError
from fluxor.loader import load_workflow, load_workflow_dir, resolve_workflow
from fluxor.logging_setup import configure_logging
from fluxor.registry import all_actions
from fluxor.storage import Database, RunRepository

console = Console()
app = typer.Typer(
    name="fluxor",
    help="Motor de automações declarativas. Você escreve o YAML, o Fluxor executa.",
    no_args_is_help=True,
    add_completion=False,
)

STATUS_STYLE = {
    "success": ("[green]✔[/green]", "green"),
    "failed": ("[red]✘[/red]", "red"),
    "skipped": ("[yellow]⊘[/yellow]", "yellow"),
    "partial": ("[yellow]▲[/yellow]", "yellow"),
    "running": ("[cyan]●[/cyan]", "cyan"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _icon(status: str) -> str:
    return STATUS_STYLE.get(status, ("•", "white"))[0]


def _style(status: str) -> str:
    return STATUS_STYLE.get(status, ("•", "white"))[1]


def _parse_vars(pairs: list[str]) -> dict[str, Any]:
    """Converte `--var chave=valor`. O valor passa por JSON, então tipos funcionam.

    `--var limite=2500` vira int, `--var ativo=true` vira bool, e qualquer coisa
    que não seja JSON válido continua string.
    """
    parsed: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise typer.BadParameter(f"esperado 'chave=valor', recebi {pair!r}")
        key, _, raw = pair.partition("=")
        try:
            parsed[key.strip()] = json.loads(raw)
        except ValueError:
            parsed[key.strip()] = raw
    return parsed


async def _make_repository(enabled: bool) -> tuple[RunRepository | None, Database | None]:
    if not enabled:
        return None, None
    database = Database(get_settings().database_url)
    await database.create_all()
    return RunRepository(database), database


def _fail(message: str) -> None:
    console.print(f"[bold red]erro:[/bold red] {message}")
    raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
# invoke_without_command: sem isto, `fluxor --version` morreria com "Missing
# command" antes de o callback rodar. Com `no_args_is_help`, o `fluxor` puro
# continua mostrando a ajuda.
@app.callback(invoke_without_command=True)
def main_callback(
    version: Annotated[bool, typer.Option("--version", help="Mostra a versão e sai.")] = False,
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    if version:
        console.print(f"fluxor {__version__}")
        raise typer.Exit()


@app.command("validate")
def validate_command(
    targets: Annotated[
        list[str] | None,
        typer.Argument(help="Arquivos .yaml. Sem argumento, valida a pasta configurada."),
    ] = None,
) -> None:
    """Valida workflows sem executar nada."""
    settings = get_settings()

    try:
        if targets:
            pairs = [(load_workflow(target), Path(target)) for target in targets]
        else:
            pairs = load_workflow_dir(settings.workflows_dir)
    except FluxorError as exc:
        _fail(str(exc))
        return

    if not pairs:
        console.print("[yellow]nenhum workflow encontrado[/yellow]")
        raise typer.Exit(code=1)

    table = Table(title="Workflows válidos", header_style="bold cyan", box=None, padding=(0, 2))
    table.add_column("nome", style="bold")
    table.add_column("passos", justify="right")
    table.add_column("gatilho")
    table.add_column("arquivo", style="dim")

    for workflow, path in pairs:
        gatilho = workflow.trigger.type.value
        if workflow.trigger.cron:
            gatilho += f" ({workflow.trigger.cron})"
        table.add_row(workflow.name, str(len(workflow.steps)), gatilho, path.name)

    console.print(table)
    console.print(f"\n[green]✔ {len(pairs)} workflow(s) sem erros[/green]")


@app.command("list")
def list_command() -> None:
    """Lista os workflows da pasta configurada."""
    settings = get_settings()
    try:
        pairs = load_workflow_dir(settings.workflows_dir)
    except FluxorError as exc:
        _fail(str(exc))
        return

    table = Table(
        title=f"Workflows em {settings.workflows_dir}",
        header_style="bold cyan",
        box=None,
        padding=(0, 2),
    )
    table.add_column("nome", style="bold")
    table.add_column("descrição")
    table.add_column("gatilho", style="magenta")
    table.add_column("passos", justify="right")

    for workflow, _ in pairs:
        gatilho = workflow.trigger.cron or workflow.trigger.type.value
        table.add_row(workflow.name, workflow.description or "—", gatilho, str(len(workflow.steps)))

    console.print(table)


@app.command("actions")
def actions_command(
    name: Annotated[str | None, typer.Argument(help="Detalha uma action específica.")] = None,
) -> None:
    """Mostra o catálogo de actions disponíveis e seus parâmetros."""
    catalog = all_actions()

    if name:
        action = catalog.get(name)
        if action is None:
            _fail(f"action '{name}' não existe. Rode `fluxor actions` para ver a lista.")
            return

        info = action.describe()
        console.print(Panel(f"[bold]{info['name']}[/bold]\n{info['summary']}", border_style="cyan"))

        table = Table(header_style="bold", box=None, padding=(0, 2))
        table.add_column("parâmetro", style="bold")
        table.add_column("tipo", style="magenta")
        table.add_column("obrig.", justify="center")
        table.add_column("padrão", style="dim")
        table.add_column("descrição")

        for param in info["params"]:
            table.add_row(
                param["name"],
                param["type"],
                "[red]sim[/red]" if param["required"] else "não",
                "—" if param["default"] is None else str(param["default"]),
                param["description"] or "",
            )
        console.print(table)
        return

    grouped: dict[str, list[dict[str, Any]]] = {}
    for action in catalog.values():
        info = action.describe()
        grouped.setdefault(info["namespace"], []).append(info)

    for namespace in sorted(grouped):
        table = Table(title=f"{namespace}.*", header_style="bold cyan", box=None, padding=(0, 2))
        table.add_column("action", style="bold")
        table.add_column("o que faz")
        for info in sorted(grouped[namespace], key=lambda item: item["name"]):
            table.add_row(info["name"], info["summary"])
        console.print(table)
        console.print()

    console.print(f"[dim]{len(catalog)} actions · detalhe com `fluxor actions <nome>`[/dim]")


@app.command("run")
def run_command(
    workflow_ref: Annotated[str, typer.Argument(help="Caminho do .yaml ou nome do workflow.")],
    var: Annotated[
        list[str] | None, typer.Option("--var", "-v", help="Sobrescreve uma var: chave=valor.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Resolve tudo mas não executa efeito nenhum.")
    ] = False,
    no_db: Annotated[
        bool, typer.Option("--no-db", help="Não grava esta execução no banco.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Imprime o resultado como JSON.")] = False,
) -> None:
    """Executa um workflow agora."""
    settings = get_settings()

    try:
        workflow, path = resolve_workflow(workflow_ref, settings.workflows_dir)
    except FluxorError as exc:
        _fail(str(exc))
        return

    overrides = _parse_vars(var or [])
    record = asyncio.run(
        _run_async(workflow_ref, workflow, overrides, dry_run, not no_db, as_json, path)
    )

    if as_json:
        console.print_json(json.dumps(record.to_dict(), ensure_ascii=False))
    if not record.ok:
        raise typer.Exit(code=1)


async def _run_async(
    reference: str,
    workflow: Any,
    overrides: dict[str, Any],
    dry_run: bool,
    persist: bool,
    quiet: bool,
    path: Path,
) -> RunRecord:
    repository, database = await _make_repository(persist)
    engine = Engine(sink=repository)

    if not quiet:
        header = f"[bold]{workflow.name}[/bold]"
        if workflow.description:
            header += f"\n[dim]{workflow.description}[/dim]"
        header += f"\n[dim]{path}[/dim]"
        if dry_run:
            header += "\n[yellow]modo dry-run: nenhum efeito colateral será executado[/yellow]"
        console.print(Panel(header, border_style="cyan", title="fluxor run"))

    def on_step(result: StepResult) -> None:
        if quiet:
            return
        detail = ""
        if result.status is StepStatus.FAILED:
            detail = f" [red]{result.error}[/red]"
        elif result.status is StepStatus.SKIPPED:
            detail = f" [dim]({result.skipped_reason})[/dim]"
        elif result.attempts > 1:
            detail = f" [yellow](após {result.attempts} tentativas)[/yellow]"

        console.print(
            f"  {_icon(result.status.value)} [bold]{result.step_id}[/bold] "
            f"[dim]{result.action}[/dim] [dim]{result.duration_ms}ms[/dim]{detail}"
        )

    try:
        record = await engine.execute(
            workflow, extra_vars=overrides, dry_run=dry_run, on_step=on_step
        )
    finally:
        if database is not None:
            await database.dispose()

    if not quiet:
        counts = record.counts
        summary = Text()
        summary.append(
            f"{record.status.value.upper()}  ", style=f"bold {_style(record.status.value)}"
        )
        summary.append(
            f"{counts['success']} ok · {counts['failed']} falhas · "
            f"{counts['skipped']} pulados · {record.duration_ms}ms",
            style="dim",
        )
        console.print()
        console.print(summary)
        if record.error:
            console.print(f"[red]{record.error}[/red]")
        console.print(f"[dim]run id: {record.id}[/dim]")

    return record


@app.command("runs")
def runs_command(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Quantas execuções listar.")] = 15,
    workflow: Annotated[
        str | None, typer.Option("--workflow", "-w", help="Filtra por nome.")
    ] = None,
    status: Annotated[
        str | None, typer.Option("--status", "-s", help="success|failed|partial.")
    ] = None,
) -> None:
    """Mostra o histórico de execuções gravado no banco."""

    async def _list() -> dict[str, Any]:
        repository, database = await _make_repository(True)
        assert repository is not None and database is not None
        try:
            return await repository.list_runs(limit=limit, workflow=workflow, status=status)
        finally:
            await database.dispose()

    page = asyncio.run(_list())

    if not page["items"]:
        console.print("[yellow]nenhuma execução registrada ainda[/yellow]")
        return

    table = Table(title="Últimas execuções", header_style="bold cyan", box=None, padding=(0, 2))
    table.add_column("")
    table.add_column("id", style="dim")
    table.add_column("workflow", style="bold")
    table.add_column("gatilho", style="magenta")
    table.add_column("início", style="dim")
    table.add_column("duração", justify="right")
    table.add_column("passos", justify="right")

    for item in page["items"]:
        started = (item["started_at"] or "")[:19].replace("T", " ")
        passos = f"{item['steps_total'] - item['steps_failed']}/{item['steps_total']}"
        table.add_row(
            _icon(item["status"]),
            item["id"][:8],
            item["workflow"],
            item["trigger"],
            started,
            f"{item['duration_ms']}ms",
            passos,
        )

    console.print(table)
    console.print(f"[dim]{len(page['items'])} de {page['total']} execuções[/dim]")


@app.command("show")
def show_command(
    run_id: Annotated[
        str, typer.Argument(help="ID da execução (aceita o prefixo mostrado em `runs`).")
    ],
) -> None:
    """Detalha uma execução: cada passo, saída e erro."""

    async def _get() -> dict[str, Any] | None:
        repository, database = await _make_repository(True)
        assert repository is not None and database is not None
        try:
            detail = await repository.get_run(run_id)
            if detail is None:  # tenta casar pelo prefixo
                page = await repository.list_runs(limit=200)
                match = next((i for i in page["items"] if i["id"].startswith(run_id)), None)
                if match:
                    detail = await repository.get_run(match["id"])
            return detail
        finally:
            await database.dispose()

    detail = asyncio.run(_get())
    if detail is None:
        _fail(f"execução '{run_id}' não encontrada")
        return

    console.print(
        Panel(
            f"[bold]{detail['workflow']}[/bold]  "
            f"[{_style(detail['status'])}]{detail['status']}[/{_style(detail['status'])}]\n"
            f"[dim]{detail['id']} · {detail['trigger']} · {detail['duration_ms']}ms · "
            f"{(detail['started_at'] or '')[:19].replace('T', ' ')}[/dim]",
            border_style=_style(detail["status"]),
        )
    )

    if detail.get("error"):
        console.print(f"[red]{detail['error']}[/red]\n")

    for step in detail["steps"]:
        console.print(
            f"{_icon(step['status'])} [bold]{step['step_id']}[/bold] "
            f"[dim]{step['action']} · {step['duration_ms']}ms · "
            f"{step['attempts']} tentativa(s)[/dim]"
        )
        if step.get("error"):
            console.print(f"   [red]{step['error']}[/red]")
        elif step.get("output") is not None:
            preview = json.dumps(step["output"], ensure_ascii=False, indent=2, default=str)
            if len(preview) > 600:
                preview = preview[:600] + "\n… (truncado)"
            console.print(Syntax(preview, "json", theme="ansi_dark", background_color="default"))


@app.command("serve")
def serve_command(
    host: Annotated[str | None, typer.Option("--host")] = None,
    port: Annotated[int | None, typer.Option("--port", "-p")] = None,
    reload: Annotated[
        bool, typer.Option("--reload", help="Recarrega ao salvar (desenvolvimento).")
    ] = False,
    scheduler: Annotated[
        bool, typer.Option("--scheduler", help="Sobe o agendador junto com a API.")
    ] = False,
) -> None:
    """Sobe a API e o dashboard web."""
    import os

    import uvicorn

    settings = get_settings()
    if scheduler:
        os.environ["FLUXOR_ENABLE_SCHEDULER"] = "true"

    bind_host = host or settings.host
    bind_port = port or settings.port

    console.print(
        Panel(
            f"[bold]dashboard[/bold]  http://{bind_host}:{bind_port}\n"
            f"[bold]api docs [/bold]  http://{bind_host}:{bind_port}/docs\n"
            f"[dim]workflows: {settings.workflows_dir} · banco: {settings.database_url}[/dim]"
            + ("\n[green]agendador ativo[/green]" if scheduler else ""),
            title="fluxor serve",
            border_style="cyan",
        )
    )

    uvicorn.run(
        "fluxor.api.app:app",
        host=bind_host,
        port=bind_port,
        reload=reload,
        log_level=settings.log_level.lower(),
    )


@app.command("scheduler")
def scheduler_command() -> None:
    """Roda apenas o agendador em primeiro plano (sem API)."""
    from fluxor.scheduler import WorkflowScheduler

    async def _run() -> None:
        repository, database = await _make_repository(True)
        engine = Engine(sink=repository)
        runner = WorkflowScheduler(engine)

        try:
            scheduled = runner.load()
        except FluxorError as exc:
            _fail(str(exc))
            return

        if not scheduled:
            console.print(
                "[yellow]nenhum workflow com `trigger.type: schedule`. Nada para agendar.[/yellow]"
            )
            return

        runner.start()

        table = Table(title="Jobs agendados", header_style="bold cyan", box=None, padding=(0, 2))
        table.add_column("workflow", style="bold")
        table.add_column("cron", style="magenta")
        table.add_column("próxima execução")
        for job in runner.describe_jobs():
            table.add_row(
                job["workflow"], job["cron"] or "—", (job["next_run"] or "—")[:19].replace("T", " ")
            )
        console.print(table)
        console.print("\n[dim]Ctrl+C para parar[/dim]")

        try:
            await asyncio.Event().wait()  # roda até receber sinal
        finally:
            runner.shutdown()
            if database is not None:
                await database.dispose()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[dim]agendador encerrado[/dim]")


@app.command("purge")
def purge_command(
    days: Annotated[
        int, typer.Option("--days", "-d", help="Apaga execuções mais antigas que N dias.")
    ] = 30,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Não pergunta antes de apagar.")] = False,
) -> None:
    """Limpa o histórico antigo do banco."""
    if not yes:
        typer.confirm(f"Apagar execuções com mais de {days} dias?", abort=True)

    async def _purge() -> int:
        repository, database = await _make_repository(True)
        assert repository is not None and database is not None
        try:
            return await repository.purge(days)
        finally:
            await database.dispose()

    removed = asyncio.run(_purge())
    console.print(f"[green]✔[/green] {removed} execução(ões) removida(s)")


@app.command("init")
def init_command(
    directory: Annotated[str, typer.Argument(help="Pasta onde criar o exemplo.")] = "workflows",
) -> None:
    """Cria uma pasta de workflows com um exemplo comentado pronto para rodar."""
    target = Path(directory).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    sample = target / "meu-primeiro-workflow.yaml"

    if sample.exists():
        _fail(f"{sample} já existe — não vou sobrescrever")
        return

    sample.write_text(SAMPLE_WORKFLOW, encoding="utf-8")
    console.print(f"[green]✔[/green] criado: {sample}")
    console.print(f"\nPróximo passo:\n  [bold cyan]fluxor run {sample}[/bold cyan]")


SAMPLE_WORKFLOW = """# Workflow de exemplo — rode com: fluxor run meu-primeiro-workflow.yaml
name: meu-primeiro-workflow
description: Busca a cotação do dólar e mostra no log

# manual = só roda quando você mandar. Troque para schedule + cron para automatizar.
trigger:
  type: manual

vars:
  moeda: USD-BRL

steps:
  - id: cotacao
    use: http.get
    with:
      url: "https://economia.awesomeapi.com.br/json/last/{{ vars.moeda }}"
    retry:
      attempts: 3
      backoff: exponential

  - id: valor
    use: parse.json
    with:
      data: "{{ steps.cotacao.json }}"
      path: "USDBRL.bid"

  - id: avisar
    use: notify.log
    with:
      message: "Dólar hoje: {{ steps.valor | brl }}"
"""


def main() -> None:
    """Ponto de entrada do executável `fluxor`."""
    app()


if __name__ == "__main__":
    main()
