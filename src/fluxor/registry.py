"""Registry de actions.

Uma action se registra com um decorator e passa a estar disponível para
qualquer workflow. Além das actions embutidas, o registry varre os
*entry points* do grupo ``fluxor.actions``: qualquer pacote instalado com

.. code-block:: toml

    [project.entry-points."fluxor.actions"]
    minhas = "meu_pacote.actions"

é carregado automaticamente. É assim que o Fluxor cresce sem precisar de fork.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

from fluxor.exceptions import ActionNotFound
from fluxor.logging_setup import get_logger

if TYPE_CHECKING:
    from fluxor.actions.base import Action

log = get_logger(__name__)

_REGISTRY: dict[str, type[Action]] = {}
_builtins_loaded = False
_plugins_loaded = False


def register(name: str | None = None):  # type: ignore[no-untyped-def]
    """Decorator que registra uma classe de action.

    .. code-block:: python

        @register("slack.post")
        class SlackPost(Action):
            summary = "Publica uma mensagem no Slack"
            ...
    """

    def decorator(cls: type[Action]) -> type[Action]:
        action_name = name or getattr(cls, "name", "")
        if not action_name:
            raise ValueError(
                f"{cls.__name__} precisa de um nome (no decorator ou no atributo 'name')"
            )

        existing = _REGISTRY.get(action_name)
        if existing is not None and existing is not cls:
            dono = f"{existing.__module__}.{existing.__name__}"
            raise ValueError(f"action '{action_name}' já registrada por {dono}")

        cls.name = action_name
        _REGISTRY[action_name] = cls
        return cls

    return decorator


def load_builtin_actions() -> None:
    """Importa todos os módulos de `fluxor.actions` (dispara os decorators)."""
    global _builtins_loaded
    if _builtins_loaded:
        return

    import fluxor.actions as actions_package

    for module in pkgutil.iter_modules(actions_package.__path__):
        if module.name.startswith("_") or module.name == "base":
            continue
        importlib.import_module(f"{actions_package.__name__}.{module.name}")

    _builtins_loaded = True


def load_plugin_actions() -> None:
    """Carrega actions de pacotes de terceiros via entry points."""
    global _plugins_loaded
    if _plugins_loaded:
        return

    from importlib.metadata import entry_points

    for entry in entry_points(group="fluxor.actions"):
        try:
            entry.load()
            log.info("plugin_carregado", plugin=entry.name, target=entry.value)
        except Exception as exc:
            log.warning("plugin_falhou", plugin=entry.name, error=str(exc))

    _plugins_loaded = True


def bootstrap() -> None:
    """Garante que o registry esteja completo. Chamado antes de qualquer execução."""
    load_builtin_actions()
    load_plugin_actions()


def get_action(name: str) -> type[Action]:
    """Busca uma action pelo nome, ou levanta :class:`ActionNotFound`."""
    bootstrap()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ActionNotFound(name, list(_REGISTRY)) from None


def has_action(name: str) -> bool:
    bootstrap()
    return name in _REGISTRY


def all_actions() -> dict[str, type[Action]]:
    """Todas as actions registradas, em ordem alfabética."""
    bootstrap()
    return dict(sorted(_REGISTRY.items()))


def action_names() -> list[str]:
    return sorted(all_actions())


def clear_registry() -> None:
    """Zera o registry. Usado nos testes que registram actions falsas."""
    global _builtins_loaded, _plugins_loaded
    _REGISTRY.clear()
    _builtins_loaded = False
    _plugins_loaded = False
