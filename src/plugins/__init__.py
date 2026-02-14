"""
Plugin system for the Operator Controller.

This package provides the plugin architecture for extensible inputs, actions,
and reconcilers.
"""

from plugins.base import ActionPhase, ActionResult, ActionContext
from plugins.reconcilers.base import (
    ReconcilerPlugin,
    ReconcilerContext,
    ReconcileResult,
)
from plugins.registry import PluginRegistry, get_registry

__all__ = [
    "ActionPhase",
    "ActionResult",
    "ActionContext",
    "ReconcilerPlugin",
    "ReconcilerContext",
    "ReconcileResult",
    "PluginRegistry",
    "get_registry",
]
