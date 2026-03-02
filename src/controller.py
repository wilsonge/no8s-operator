"""
Operator Controller - Main reconciliation loop.

Similar to Kubernetes controllers, continuously reconciles desired state with actual state.
Uses a plugin architecture for extensibility.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from db import DatabaseManager, ReconciliationResult, ResourceStatus
from events import EventBus, EventType, ResourceEvent
from plugins import ActionContext, get_registry
from plugins.actions.base import ActionPlugin
from plugins.reconcilers.base import ReconcilerContext
from plugins.registry import PluginRegistry

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class ControllerConfig:
    """Configuration for the controller."""

    reconcile_interval: int = 60
    max_concurrent_reconciles: int = 5
    plugin_configs: Optional[Dict[str, Dict[str, Any]]] = None

    # Exponential backoff configuration
    backoff_base_delay: int = 60  # base delay in seconds
    backoff_max_delay: int = 3600  # max delay in seconds (1 hour)
    backoff_jitter_factor: float = 0.1  # ±10% jitter

    def __post_init__(self):
        if self.plugin_configs is None:
            self.plugin_configs = {}


class Controller:
    """
    Main controller that implements the reconciliation loop.

    Watches for resources that need reconciliation and dispatches to the
    appropriate action plugin for execution. Also starts and stops
    reconciler plugin loops for resource types claimed by reconcilers.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        registry: Optional[PluginRegistry] = None,
        config: Optional[ControllerConfig] = None,
        event_bus: Optional[EventBus] = None,
    ):
        self.db = db_manager
        self.registry = registry or get_registry()
        self.config = config or ControllerConfig()
        self.reconcile_interval = self.config.reconcile_interval
        self.max_concurrent_reconciles = self.config.max_concurrent_reconciles
        self.semaphore = asyncio.Semaphore(self.max_concurrent_reconciles)
        self.running = False
        self._event_bus = event_bus

        # Cache of initialized action plugins
        self._action_plugins: Dict[str, ActionPlugin] = {}

        # Reconciler plugin management
        self._shutdown_event = asyncio.Event()
        self._reconciler_tasks: List[asyncio.Task] = []

    async def _get_action_plugin(
        self, name: str, config: Optional[Dict[str, Any]] = None
    ) -> ActionPlugin:
        """
        Get or create an action plugin instance.

        Retrieves an action plugin from the cache or creates a new one.
        Plugin configuration is merged from global config and resource-specific
        config, with resource-specific values taking precedence.

        Args:
            name: The name of the action plugin (e.g., 'github_actions')
            config: Optional resource-specific plugin configuration to merge

        Returns:
            An initialized ActionPlugin instance

        Raises:
            ValueError: If the plugin name is not registered
        """
        if name not in self._action_plugins:
            # Merge global plugin config with resource-specific config
            plugin_config = self.config.plugin_configs.get(name, {}).copy()
            if config:
                plugin_config.update(config)

            self._action_plugins[name] = await self.registry.get_action_plugin(
                name, plugin_config
            )
        return self._action_plugins[name]

    async def start(self):
        """Start the controller reconciliation loop and reconciler plugins."""
        logger.info("Starting Operator Controller")
        self.running = True
        self._shutdown_event.clear()

        # Start reconciler plugins
        await self._start_reconcilers()

        # Start the main reconciliation loop
        reconcile_task = asyncio.create_task(self._reconciliation_loop())

        # Start the requeue handler for failed reconciliations
        requeue_task = asyncio.create_task(self._requeue_loop())

        try:
            await asyncio.gather(reconcile_task, requeue_task, *self._reconciler_tasks)
        except Exception as e:
            logger.error(f"Controller error: {e}")
            raise

    async def stop(self):
        """Stop the controller and all reconciler plugins gracefully."""
        logger.info("Stopping Operator Controller")
        self.running = False
        self._shutdown_event.set()

        await self._stop_reconcilers()

    async def _start_reconcilers(self) -> None:
        """Start all registered reconciler plugin loops."""
        reconciler_ctx = ReconcilerContext(
            db=self.db,
            registry=self.registry,
            shutdown_event=self._shutdown_event,
        )

        for reconciler_name in self.registry.list_reconciler_plugins():
            reconciler = self.registry.get_reconciler_plugin(reconciler_name)
            task = asyncio.create_task(self._run_reconciler(reconciler, reconciler_ctx))
            self._reconciler_tasks.append(task)
            logger.info(f"Started reconciler plugin: {reconciler_name}")

    async def _run_reconciler(self, reconciler: Any, ctx: ReconcilerContext) -> None:
        """Run a reconciler plugin, catching exceptions."""
        try:
            await reconciler.start(ctx)
        except Exception as e:
            logger.error(
                f"Reconciler plugin '{reconciler.name}' crashed: {e}",
                exc_info=True,
            )

    async def _stop_reconcilers(self) -> None:
        """Stop all running reconciler plugins."""
        for reconciler_name in self.registry.list_reconciler_plugins():
            try:
                reconciler = self.registry.get_reconciler_plugin(reconciler_name)
                await reconciler.stop()
                logger.info(f"Stopped reconciler plugin: {reconciler_name}")
            except Exception as e:
                logger.error(f"Error stopping reconciler '{reconciler_name}': {e}")

        # Cancel any remaining reconciler tasks
        for task in self._reconciler_tasks:
            if not task.done():
                task.cancel()
        self._reconciler_tasks.clear()

    async def _reconciliation_loop(self):
        """Main reconciliation loop - watches for resources needing reconciliation."""
        while self.running:
            try:
                # Get resources that need reconciliation
                resources = await self.db.get_resources_needing_reconciliation(
                    limit=self.max_concurrent_reconciles * 2
                )

                if resources:
                    logger.info(
                        f"Found {len(resources)} resources needing reconciliation"
                    )

                    # Create reconciliation tasks
                    tasks = [
                        self._reconcile_resource(resource) for resource in resources
                    ]

                    # Wait for all reconciliations to complete
                    await asyncio.gather(*tasks, return_exceptions=True)

                # Sleep before next reconciliation cycle
                await asyncio.sleep(self.reconcile_interval)

            except Exception as e:
                logger.error(f"Error in reconciliation loop: {e}", exc_info=True)
                await asyncio.sleep(10)  # Brief pause on error

    async def _requeue_loop(self):
        """Handles requeuing of failed reconciliations with exponential backoff."""
        while self.running:
            try:
                # Requeue resources that failed but should be retried
                await self.db.requeue_failed_resources(
                    base_delay=self.config.backoff_base_delay,
                    max_delay=self.config.backoff_max_delay,
                    jitter_factor=self.config.backoff_jitter_factor,
                )
                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                logger.error(f"Error in requeue loop: {e}", exc_info=True)
                await asyncio.sleep(10)

    def _determine_trigger_reason(self, resource: Dict[str, Any]) -> str:
        """Determine why this reconciliation was triggered."""
        if resource.get("last_reconcile_time") is None:
            return "initial"
        elif resource.get("generation", 0) > resource.get("observed_generation", 0):
            return "spec_change"
        elif resource.get("status") == ResourceStatus.DELETING.value:
            return "deletion"
        elif resource.get("status") == ResourceStatus.FAILED.value:
            return "retry"
        else:
            # Scheduled re-reconciliation (drift detection window)
            return "scheduled"

    async def _reconcile_resource(self, resource: Dict[str, Any]):
        """
        Reconcile a single resource.

        This is the core reconciliation logic similar to Kubernetes controllers.
        Dispatches to the appropriate action plugin based on the resource's
        action_plugin field.
        """
        async with self.semaphore:
            resource_id = resource["id"]
            resource_name = resource["name"]
            action_plugin_name = resource["action_plugin"]
            generation = resource.get("generation", 0)
            start_time = time.monotonic()
            trigger_reason = self._determine_trigger_reason(resource)
            drift_detected = False

            try:
                # Mark as reconciling
                await self.db.update_resource_status(
                    resource_id,
                    ResourceStatus.RECONCILING,
                    message="Starting reconciliation",
                )
                await self.db.set_condition(
                    resource_id,
                    "Reconciling",
                    "True",
                    "ReconcileStarted",
                    "Reconciliation has started",
                    generation,
                )
                await self.db.set_condition(
                    resource_id,
                    "Ready",
                    "Unknown",
                    "ReconcileStarted",
                    "Reconciliation in progress",
                    generation,
                )

                # Get the appropriate action plugin
                plugin_config = resource.get("plugin_config", {})
                action_plugin = await self._get_action_plugin(
                    action_plugin_name, plugin_config
                )

                # Create action context
                ctx = ActionContext(
                    resource_id=resource_id,
                    resource_name=resource_name,
                    generation=resource["generation"],
                    spec=resource.get("spec", {}),
                    spec_hash=resource["spec_hash"],
                    plugin_config=plugin_config,
                )

                # Execute the reconciliation
                result = await self._execute_reconciliation(
                    action_plugin, ctx, resource
                )

                # Check if drift was detected (changes found during scheduled check)
                if trigger_reason == "scheduled" and result.has_changes:
                    drift_detected = True
                    logger.info(
                        f"Drift detected for {resource_name}: "
                        f"changes found during scheduled reconciliation"
                    )

                # Update final status
                if result.success:
                    if resource.get("status") == ResourceStatus.DELETING.value:
                        # Destroy succeeded — remove our finalizer and
                        # hard-delete if all finalizers are cleared
                        await self.db.set_condition(
                            resource_id,
                            "Reconciling",
                            "False",
                            "Deleting",
                            "Resource is being deleted",
                            generation,
                        )
                        await self.db.set_condition(
                            resource_id,
                            "Ready",
                            "Unknown",
                            "Deleting",
                            "Resource is being deleted",
                            generation,
                        )
                        await self.db.remove_finalizer(resource_id, action_plugin_name)
                        remaining = await self.db.get_finalizers(resource_id)
                        if not remaining:
                            await self.db.hard_delete_resource(resource_id)
                            logger.info(
                                f"Destroyed and deleted resource " f"{resource_name}"
                            )
                        else:
                            logger.info(
                                f"Finalizer removed for {resource_name}, "
                                f"waiting on: {remaining}"
                            )
                    else:
                        await self.db.update_resource_status(
                            resource_id,
                            ResourceStatus.READY,
                            message="Reconciliation successful",
                            observed_generation=ctx.generation,
                        )
                        await self.db.set_condition(
                            resource_id,
                            "Ready",
                            "True",
                            "ReconcileSuccess",
                            "Resource reconciled successfully",
                            generation,
                        )
                        await self.db.set_condition(
                            resource_id,
                            "Reconciling",
                            "False",
                            "ReconcileComplete",
                            "Reconciliation completed",
                            generation,
                        )
                        await self.db.set_condition(
                            resource_id,
                            "Degraded",
                            "False",
                            "NoErrors",
                            "",
                            generation,
                        )
                        logger.info(f"Successfully reconciled {resource_name}")

                        # Publish RECONCILED event
                        if self._event_bus:
                            updated_resource = await self.db.get_resource(resource_id)
                            if updated_resource:
                                event = ResourceEvent.from_resource(
                                    EventType.RECONCILED, updated_resource
                                )
                                await self._event_bus.publish(event)
                else:
                    error_msg = result.error_message or "Reconciliation failed"
                    await self.db.update_resource_status(
                        resource_id,
                        ResourceStatus.FAILED,
                        message=error_msg,
                    )
                    await self.db.set_condition(
                        resource_id,
                        "Ready",
                        "False",
                        "ReconcileFailed",
                        error_msg,
                        generation,
                    )
                    await self.db.set_condition(
                        resource_id,
                        "Reconciling",
                        "False",
                        "ReconcileFailed",
                        "",
                        generation,
                    )
                    await self.db.set_condition(
                        resource_id,
                        "Degraded",
                        "True",
                        "ReconcileFailed",
                        error_msg,
                        generation,
                    )
                    logger.error(
                        f"Failed to reconcile {resource_name}: "
                        f"{result.error_message}"
                    )

                # Record reconciliation result with duration
                duration_seconds = time.monotonic() - start_time
                await self.db.record_reconciliation(
                    resource_id=resource_id,
                    success=result.success,
                    phase=result.phase,
                    plan_output=result.plan_output,
                    apply_output=result.apply_output,
                    error_message=result.error_message,
                    resources_created=result.resources_created,
                    resources_updated=result.resources_updated,
                    resources_deleted=result.resources_deleted,
                    duration_seconds=duration_seconds,
                    trigger_reason=trigger_reason,
                    drift_detected=drift_detected,
                )

            except Exception as e:
                logger.error(f"Error reconciling {resource_name}: {e}", exc_info=True)
                error_msg = f"Reconciliation error: {str(e)}"
                await self.db.update_resource_status(
                    resource_id,
                    ResourceStatus.FAILED,
                    message=error_msg,
                )
                await self.db.set_condition(
                    resource_id,
                    "Ready",
                    "False",
                    "ReconcileFailed",
                    error_msg,
                    generation,
                )
                await self.db.set_condition(
                    resource_id,
                    "Reconciling",
                    "False",
                    "ReconcileFailed",
                    "",
                    generation,
                )
                await self.db.set_condition(
                    resource_id,
                    "Degraded",
                    "True",
                    "ReconcileFailed",
                    error_msg,
                    generation,
                )

    async def _execute_reconciliation(
        self,
        plugin: ActionPlugin,
        ctx: ActionContext,
        resource: Dict[str, Any],
    ) -> ReconciliationResult:
        """
        Execute reconciliation using the action plugin.

        Phases: Prepare -> Plan -> Apply (or Destroy)
        """
        result = ReconciliationResult()
        workspace = None

        try:
            # Phase 1: Prepare workspace
            logger.info(f"Phase 1: Preparing for {ctx.resource_name}")
            result.phase = "initializing"

            workspace = await plugin.prepare(ctx)

            # Phase 2: Plan
            logger.info(f"Phase 2: Planning for {ctx.resource_name}")
            result.phase = "planning"

            plan_result = await plugin.plan(ctx, workspace)
            result.plan_output = plan_result.plan_output
            result.has_changes = plan_result.has_changes

            if not plan_result.success:
                result.success = False
                result.error_message = plan_result.error_message or "Plan failed"
                result.phase = "failed"
                return result

            # Check if this is a delete operation
            if resource.get("status") == ResourceStatus.DELETING.value:
                logger.info(f"Destroying {ctx.resource_name}")
                result.phase = "destroying"

                destroy_result = await plugin.destroy(ctx, workspace)
                result.apply_output = destroy_result.apply_output
                result.success = destroy_result.success
                result.error_message = destroy_result.error_message
                result.resources_deleted = destroy_result.resources_deleted

                if destroy_result.success:
                    result.phase = "completed"
                else:
                    result.phase = "failed"

                return result

            # Check if there are any changes to apply
            if not plan_result.has_changes:
                logger.info(f"No changes needed for {ctx.resource_name}")
                result.success = True
                result.phase = "completed"
                return result

            # Phase 3: Apply
            logger.info(f"Phase 3: Applying for {ctx.resource_name}")
            result.phase = "applying"

            apply_result = await plugin.apply(ctx, workspace)
            result.apply_output = apply_result.apply_output
            result.resources_created = apply_result.resources_created
            result.resources_updated = apply_result.resources_updated
            result.resources_deleted = apply_result.resources_deleted

            if not apply_result.success:
                result.success = False
                result.error_message = apply_result.error_message or "Apply failed"
                result.phase = "failed"
                return result

            # Save outputs to database
            if apply_result.outputs:
                await self.db.update_resource_outputs(
                    ctx.resource_id, apply_result.outputs
                )

            result.success = True
            result.phase = "completed"

        except Exception as e:
            logger.error(f"Reconciliation execution error: {e}", exc_info=True)
            result.success = False
            result.error_message = str(e)
            result.phase = "failed"

        finally:
            # Cleanup workspace
            if workspace is not None:
                try:
                    await plugin.cleanup(workspace)
                except Exception as e:
                    logger.error(f"Error cleaning up workspace: {e}")

        return result

    async def trigger_reconciliation(self, resource_id: int):
        """Manually trigger reconciliation for a specific resource."""
        logger.info(f"Manually triggering reconciliation for resource {resource_id}")
        await self.db.mark_resource_for_reconciliation(resource_id)
