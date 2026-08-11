"""Biblioteca de actions embutidas.

Cada módulo desta pasta é importado automaticamente por
:func:`fluxor.registry.load_builtin_actions`, então basta criar um arquivo novo
com uma classe decorada com `@register(...)` para a action existir.
"""

from fluxor.actions.base import Action, ActionInput

__all__ = ["Action", "ActionInput"]
