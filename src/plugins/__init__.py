"""
Plugin system for the Operator Controller.

This package provides the plugin architecture for extensible inputs and actions.
"""

from plugins.base import ActionPhase, ActionResult, ActionContext
from plugins.registry import PluginRegistry, get_registry

__all__ = [
    "ActionPhase",
    "ActionResult",
    "ActionContext",
    "PluginRegistry",
    "get_registry",
]
