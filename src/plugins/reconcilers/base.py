"""
Reconciler Plugin Base - Abstract interface for reconciler plugins.

Reconciler plugins are 3rd party pip packages that own the reconciliation
logic for one or more resource types. They are discovered via Python entry
points and run their own continuous reconciliation loops.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

from db import DatabaseManager, ResourceStatus
from events import EventBus
from plugins.actions.base import ActionPlugin
from plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)


class ResourceRecord(TypedDict, total=False):
    """
    Typed representation of a resource dict returned by
    ReconcilerContext.get_resources_needing_reconciliation().

    All fields are always present; `total=False` is used only so that
    subsets can be constructed in tests without filling every key.
    """

    id: int
    name: str
    resource_type_name: str
    resource_type_version: str
    # The action plugin name linked to this resource (empty string if none).
    action_plugin: str
    # Desired end-state declared by the resource owner.
    spec: Dict[str, Any]
    # Backend-specific configuration for the action plugin (credentials,
    # repository name, workflow ID, etc.).  Reconcilers that call the
    # provider API directly rather than via an action plugin will
    # typically ignore this field.
    plugin_config: Dict[str, Any]
    metadata: Dict[str, Any]
    # Current phase — one of the ResourceStatus string values:
    # "pending", "reconciling", "ready", "failed", "deleting".
    status: str
    generation: int
    observed_generation: int
    status_message: str
    conditions: List[Dict[str, Any]]
    finalizers: List[str]
    spec_hash: str
    outputs: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]
    last_reconcile_time: Optional[datetime]
    next_reconcile_time: Optional[datetime]
    retry_count: int


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
        event_bus: Optional[EventBus] = None,
    ):
        self.db = db
        self.registry = registry
        self.shutdown_event = shutdown_event
        self.event_bus = event_bus

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
            Each dict conforms to the ResourceRecord TypedDict shape.
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

    async def set_condition(
        self,
        resource_id: int,
        condition_type: str,
        status: str,
        reason: str,
        message: str = "",
        observed_generation: Optional[int] = None,
    ) -> None:
        """
        Set a named condition on a resource.

        Args:
            resource_id: The resource ID.
            condition_type: Condition type (e.g. 'Ready', 'DatabaseAvailable').
            status: "True", "False", or "Unknown".
            reason: Short CamelCase reason string.
            message: Human-readable detail message.
            observed_generation: Generation when this condition was set.
        """
        await self.db.set_condition(
            resource_id=resource_id,
            condition_type=condition_type,
            status=status,
            reason=reason,
            message=message,
            observed_generation=observed_generation or 0,
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

        If result.requeue_after is set, the resource is scheduled for
        re-reconciliation after that many seconds (equivalent to
        ctrl.Result{RequeueAfter: d} in controller-runtime).

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
        if result.requeue_after is not None:
            await self.db.mark_resource_for_reconciliation(
                resource_id, delay_seconds=result.requeue_after
            )

    async def add_finalizer(self, resource_id: int, finalizer: str) -> None:
        """
        Add a finalizer to a resource.

        Call this during reconciliation to register a cleanup obligation.
        The resource cannot be hard-deleted until this finalizer is removed
        via remove_finalizer().  No-op if the finalizer already exists.

        Args:
            resource_id: The resource ID.
            finalizer: Finalizer name to add (typically self.name).
        """
        await self.db.add_finalizer(resource_id, finalizer)

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

    async def update_outputs(self, resource_id: int, outputs: Dict[str, Any]) -> None:
        """
        Store output values on a resource.

        Outputs are returned in every GET /api/v1/resources/{id} response
        under the 'outputs' key, making them available to downstream consumers
        without requiring direct database access.  Use this to publish values
        produced during reconciliation, such as connection strings, endpoints,
        or allocated identifiers.

        Args:
            resource_id: The resource ID.
            outputs: Dict of output key-value pairs.  Replaces the existing
                     outputs dict entirely on each call.
        """
        await self.db.update_resource_outputs(resource_id, outputs)


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
            resource: The resource record from the operator cache.
                      See ResourceRecord for all available fields.
            ctx: ReconcilerContext for status updates and action plugins.

        Returns:
            ReconcileResult indicating success/failure.
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown. Clean up any resources."""
        pass


class BaseReconciler(ReconcilerPlugin):
    """
    Convenience base class that provides the standard reconciliation loop.

    Subclass this instead of ReconcilerPlugin when you want the canonical
    fetch → reconcile → record pattern handled for you.  Only reconcile()
    needs to be implemented; stop() has a no-op default.

    The loop interval defaults to 30 seconds.  Override the class attribute
    to change it::

        class MyReconciler(BaseReconciler):
            reconcile_interval = 60

    Per-resource exceptions are caught and recorded as failures so that one
    bad resource does not skip the rest of the batch.  Loop-level exceptions
    (e.g. database connectivity loss) are also caught and logged; the loop
    resumes after the next interval.
    """

    reconcile_interval: int = 30

    async def start(self, ctx: ReconcilerContext) -> None:
        from events import EventType

        sub_id, subscription = None, None
        if ctx.event_bus:
            sub_id, subscription = await ctx.event_bus.subscribe(
                lambda e: e.event_type == EventType.TRIGGER
                and e.resource_type_name in self.resource_types
            )

        logger.info(f"{self.name} reconciler started")
        try:
            while not ctx.shutdown_event.is_set():
                try:
                    resources = await ctx.get_resources_needing_reconciliation(
                        resource_type_names=self.resource_types,
                    )
                    for resource in resources:
                        if ctx.shutdown_event.is_set():
                            break
                        start_time = time.monotonic()
                        try:
                            result = await self.reconcile(resource, ctx)
                        except Exception as exc:
                            logger.exception(
                                f"{self.name}: unhandled error reconciling "
                                f"resource {resource['id']}"
                            )
                            result = ReconcileResult(success=False, message=str(exc))
                        await ctx.record_reconciliation(
                            resource_id=resource["id"],
                            result=result,
                            duration_seconds=time.monotonic() - start_time,
                        )
                except Exception:
                    logger.exception(f"{self.name}: error in reconcile loop")

                # Wait for shutdown, poll timeout, or immediate TRIGGER wake-up
                shutdown_task = asyncio.ensure_future(ctx.shutdown_event.wait())
                sleep_task = asyncio.ensure_future(
                    asyncio.sleep(float(self.reconcile_interval))
                )
                wait_tasks = [shutdown_task, sleep_task]

                async def _next_trigger(sub):
                    try:
                        await sub.__anext__()
                    except (StopAsyncIteration, asyncio.CancelledError):
                        pass

                trigger_task = (
                    asyncio.ensure_future(_next_trigger(subscription))
                    if subscription
                    else None
                )
                if trigger_task:
                    wait_tasks.append(trigger_task)

                done, pending = await asyncio.wait(
                    wait_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

                if shutdown_task in done:
                    break
        finally:
            if sub_id and ctx.event_bus:
                await ctx.event_bus.unsubscribe(sub_id)

        logger.info(f"{self.name} reconciler stopped")

    async def stop(self) -> None:
        pass
