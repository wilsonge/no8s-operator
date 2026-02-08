"""
Core plugin types and dataclasses.

This module contains shared types used across the plugin system.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class ActionPhase(Enum):
    """Standard phases for action execution."""

    PENDING = "pending"
    INITIALIZING = "initializing"
    PLANNING = "planning"
    APPLYING = "applying"
    DESTROYING = "destroying"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ActionResult:
    """Standard result from action plugin execution."""

    success: bool = False
    phase: ActionPhase = ActionPhase.PENDING
    plan_output: str = ""
    apply_output: str = ""
    error_message: Optional[str] = None
    resources_created: int = 0
    resources_updated: int = 0
    resources_deleted: int = 0
    outputs: Dict[str, Any] = field(default_factory=dict)
    has_changes: bool = False


@dataclass
class ActionContext:
    """Context passed to action plugins during execution."""

    resource_id: int
    resource_name: str
    generation: int
    spec: Dict[str, Any]
    spec_hash: str
    plugin_config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ResourceSpec:
    """Standard resource specification from any input source."""

    name: str
    action_plugin: str
    spec: Dict[str, Any]
    plugin_config: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class DriftResult:
    """Result from drift detection."""

    has_drift: bool = False
    drift_details: str = ""
    resources_drifted: int = 0
    error_message: Optional[str] = None
