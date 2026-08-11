# Contribuindo

Obrigado pelo interesse. Este guia é curto de propósito.

## Ambiente

```bash
git clone https://github.com/NeithanDev-Arch/fluxor.git
cd fluxor
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

## Antes de abrir um PR

```bash
ruff check src tests      # lint
ruff format src tests     # formatação
mypy                      # tipos
pytest                    # testes
```

O CI roda exatamente esses quatro comandos em Python 3.11, 3.12 e 3.13. Se
passar local, passa lá.

## Criando uma action nova

Toda action tem três partes: um schema de entrada, um nome e um `run`
assíncrono. Crie um arquivo em `src/fluxor/actions/` — ele é descoberto
automaticamente, não precisa registrar em lugar nenhum.

```python
from typing import ClassVar

from pydantic import Field

from fluxor.actions.base import Action, ActionInput
from fluxor.context import RunContext
from fluxor.registry import register


class SlackInput(ActionInput):
    webhook_url: str = Field(description="URL do webhook do canal.")
    text: str = Field(description="Mensagem a enviar.")


@register("slack.post")
class SlackPost(Action):
    """Publica uma mensagem em um canal do Slack."""

    summary = "Envia uma mensagem no Slack"
    Input: ClassVar[type[ActionInput]] = SlackInput

    async def run(self, params: SlackInput, ctx: RunContext) -> dict[str, bool]:
        ...
        return {"ok": True}
```

Checklist da action:

- [ ] `summary` e `description` em cada campo — é o que aparece em
      `fluxor actions <nome>` e no `/api/actions`.
- [ ] Erro que não melhora com nova tentativa levanta `PermanentError`;
      erro transitório levanta qualquer outra exceção.
- [ ] I/O bloqueante (disco, `smtplib`, bibliotecas síncronas) vai para
      `asyncio.to_thread`.
- [ ] Um teste em `tests/test_actions.py`. Para HTTP, use `respx` —
      nenhum teste sai para a internet.

## Distribuindo actions como pacote separado

Você não precisa de um fork. Publique um pacote com um entry point:

```toml
[project.entry-points."fluxor.actions"]
minhas-actions = "meu_pacote.actions"
```

Quem instalar o seu pacote passa a ter as actions disponíveis no próximo
`fluxor run`.

## Estilo

- Nomes de código em inglês, comentários e docstrings em português.
- Comentário explica **por quê**, não o quê. Se o código precisa de comentário
  para dizer o que faz, normalmente o código é que precisa mudar.
- Linha de até 100 colunas (o `ruff format` cuida disso).
