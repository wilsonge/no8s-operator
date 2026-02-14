"""
Reconciler Plugin Base - Abstract interface for reconciler plugins.

Reconciler plugins are 3rd party pip packages that own the reconciliation
logic for one or more resource types. They are discovered via Python entry
points and run their own continuous reconciliation loops.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from db import DatabaseManager, ResourceStatus
from plugins.actions.base import ActionPlugin
from plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    """Result from a reconciler's reconcile() call."""

    success: bool = False
    message: str = ""
    requeue_after: Optional[int] = None


class ReconcilerContext:
    """
    Context provided to reconciler plugins by the operator.

    Gives reconcilers access to the resource cache, status reporting,
    and optionally the action plugin registry.
    """

    def __init__(
        self,
        db: DatabaseManager,
        registry: PluginRegistry,
        shutdown_event: asyncio.Event,
    ):
        self.db = db
        self.registry = registry
        self.shutdown_event = shutdown_event

    async def get_resources_needing_reconciliation(
        self,
        resource_type_names: List[str],
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get resources needing reconciliation, filtered by resource type.

        Args:
            resource_type_names: Resource type names to filter by.
            limit: Maximum number of resources to return.

        Returns:
            List of resource dicts needing reconciliation.
        """
        return await self.db.get_resources_needing_reconciliation_by_type(
            resource_type_names=resource_type_names,
            limit=limit,
        )

    async def update_status(
        self,
        resource_id: int,
        status: str,
        message: str = "",
        observed_generation: Optional[int] = None,
    ) -> None:
        """
        Update a resource's status.

        Args:
            resource_id: The resource ID.
            status: New status string (e.g. 'reconciling', 'ready', 'failed').
            message: Human-readable status message.
            observed_generation: Set the observed generation on success.
        """
        resource_status = ResourceStatus(status)
        await self.db.update_resource_status(
            resource_id=resource_id,
            status=resource_status,
            message=message,
            observed_generation=observed_generation,
        )

    async def get_action_plugin(self, name: str) -> ActionPlugin:
        """
        Get an initialized action plugin by name.

        Args:
            name: The action plugin name.

        Returns:
            An initialized ActionPlugin instance.
        """
        return await self.registry.get_action_plugin(name)

    async def record_reconciliation(
        self,
        resource_id: int,
        result: ReconcileResult,
        duration_seconds: Optional[float] = None,
        trigger_reason: Optional[str] = None,
        drift_detected: bool = False,
    ) -> None:
        """
        Record a reconciliation attempt in history.

        Args:
            resource_id: The resource ID.
            result: The ReconcileResult from reconciliation.
            duration_seconds: How long reconciliation took.
            trigger_reason: Why reconciliation was triggered.
            drift_detected: Whether drift was detected.
        """
        await self.db.record_reconciliation(
            resource_id=resource_id,
            success=result.success,
            phase="completed" if result.success else "failed",
            error_message=result.message if not result.success else None,
            duration_seconds=duration_seconds,
            trigger_reason=trigger_reason,
            drift_detected=drift_detected,
        )

    async def remove_finalizer(self, resource_id: int, finalizer: str) -> None:
        """
        Remove a finalizer from a resource.

        Args:
            resource_id: The resource ID.
            finalizer: Finalizer name to remove.
        """
        await self.db.remove_finalizer(resource_id, finalizer)

    async def get_finalizers(self, resource_id: int) -> List[str]:
        """
        Get the finalizers list for a resource.

        Args:
            resource_id: The resource ID.

        Returns:
            List of finalizer names.
        """
        return await self.db.get_finalizers(resource_id)

    async def hard_delete_resource(self, resource_id: int) -> bool:
        """
        Permanently delete a resource (only if soft-deleted and no finalizers).

        Args:
            resource_id: The resource ID.

        Returns:
            True if deleted, False otherwise.
        """
        return await self.db.hard_delete_resource(resource_id)


class ReconcilerPlugin(ABC):
    """
    Abstract base class for reconciler plugins.

    Reconciler plugins own the reconciliation logic for one or more
    resource types. They run their own continuous reconciliation loop,
    reading from the operator's resource cache and reporting status back.

    Reconcilers are discovered via Python entry points in the
    'no8s.reconcilers' group.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this reconciler."""
        pass

    @property
    @abstractmethod
    def resource_types(self) -> List[str]:
        """Resource type names this reconciler handles."""
        pass

    @abstractmethod
    async def start(self, ctx: ReconcilerContext) -> None:
        """
        Start the reconciliation loop.

        The reconciler should run its own loop, watching the cache for
        resources that need reconciliation. Use ctx.shutdown_event to
        detect when the operator is shutting down.

        Args:
            ctx: ReconcilerContext providing access to resources and status.
        """
        pass

    @abstractmethod
    async def reconcile(
        self, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> ReconcileResult:
        """
        Reconcile a single resource.

        Compare desired state against actual state and take action.
        Report status back via ctx.update_status().

        Args:
            resource: The resource dict from the cache.
            ctx: ReconcilerContext for status updates and action plugins.

        Returns:
            ReconcileResult indicating success/failure.
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown. Clean up any resources."""
        pass
