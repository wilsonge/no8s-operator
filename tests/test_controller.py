"""Unit tests for controller.py - Main reconciliation controller."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from controller import Controller, ControllerConfig
from db import ResourceStatus, ReconciliationResult
from plugins.base import ActionContext, ActionResult, ActionPhase


class TestControllerConfig:
    """Tests for ControllerConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ControllerConfig()
        assert config.reconcile_interval == 60
        assert config.max_concurrent_reconciles == 5
        assert config.plugin_configs == {}
        assert config.backoff_base_delay == 60
        assert config.backoff_max_delay == 3600
        assert config.backoff_jitter_factor == 0.1

    def test_custom_values(self):
        """Test custom configuration values."""
        config = ControllerConfig(
            reconcile_interval=30,
            max_concurrent_reconciles=10,
            plugin_configs={"github_actions": {"timeout": 1800}},
            backoff_base_delay=120,
            backoff_max_delay=7200,
            backoff_jitter_factor=0.2,
        )
        assert config.reconcile_interval == 30
        assert config.max_concurrent_reconciles == 10
        assert config.plugin_configs == {"github_actions": {"timeout": 1800}}
        assert config.backoff_base_delay == 120
        assert config.backoff_max_delay == 7200
        assert config.backoff_jitter_factor == 0.2

    def test_post_init_none_plugin_configs(self):
        """Test that None plugin_configs is converted to empty dict."""
        config = ControllerConfig(plugin_configs=None)
        assert config.plugin_configs == {}


class TestController:
    """Tests for Controller class."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database manager."""
        db = AsyncMock()
        db.get_resources_needing_reconciliation = AsyncMock(return_value=[])
        db.update_resource_status = AsyncMock()
        db.record_reconciliation = AsyncMock()
        db.mark_resource_for_reconciliation = AsyncMock()
        db.update_resource_outputs = AsyncMock()
        db.requeue_failed_resources = AsyncMock()
        return db

    @pytest.fixture
    def mock_registry(self):
        """Create a mock plugin registry."""
        registry = MagicMock()
        registry.get_action_plugin = AsyncMock()
        return registry

    @pytest.fixture
    def controller(self, mock_db, mock_registry):
        """Create a controller for testing."""
        config = ControllerConfig(
            reconcile_interval=1,
            max_concurrent_reconciles=2,
        )
        return Controller(
            db_manager=mock_db,
            registry=mock_registry,
            config=config,
        )

    def test_init(self, controller, mock_db, mock_registry):
        """Test controller initialization."""
        assert controller.db is mock_db
        assert controller.registry is mock_registry
        assert controller.reconcile_interval == 1
        assert controller.max_concurrent_reconciles == 2
        assert controller.running is False

    def test_init_default_config(self, mock_db, mock_registry):
        """Test controller with default config."""
        controller = Controller(db_manager=mock_db, registry=mock_registry)
        assert controller.reconcile_interval == 60
        assert controller.max_concurrent_reconciles == 5

    def test_determine_trigger_reason_initial(self, controller):
        """Test trigger reason for initial reconciliation."""
        resource = {"last_reconcile_time": None}
        reason = controller._determine_trigger_reason(resource)
        assert reason == "initial"

    def test_determine_trigger_reason_spec_change(self, controller):
        """Test trigger reason for spec change."""
        resource = {
            "last_reconcile_time": "2024-01-01T00:00:00",
            "generation": 2,
            "observed_generation": 1,
            "status": "ready",
        }
        reason = controller._determine_trigger_reason(resource)
        assert reason == "spec_change"

    def test_determine_trigger_reason_deletion(self, controller):
        """Test trigger reason for deletion."""
        resource = {
            "last_reconcile_time": "2024-01-01T00:00:00",
            "generation": 1,
            "observed_generation": 1,
            "status": ResourceStatus.DELETING.value,
        }
        reason = controller._determine_trigger_reason(resource)
        assert reason == "deletion"

    def test_determine_trigger_reason_retry(self, controller):
        """Test trigger reason for retry after failure."""
        resource = {
            "last_reconcile_time": "2024-01-01T00:00:00",
            "generation": 1,
            "observed_generation": 1,
            "status": ResourceStatus.FAILED.value,
        }
        reason = controller._determine_trigger_reason(resource)
        assert reason == "retry"

    def test_determine_trigger_reason_scheduled(self, controller):
        """Test trigger reason for scheduled reconciliation."""
        resource = {
            "last_reconcile_time": "2024-01-01T00:00:00",
            "generation": 1,
            "observed_generation": 1,
            "status": ResourceStatus.READY.value,
        }
        reason = controller._determine_trigger_reason(resource)
        assert reason == "scheduled"


@pytest.mark.asyncio
class TestControllerAsync:
    """Async tests for Controller."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database manager."""
        db = AsyncMock()
        db.get_resources_needing_reconciliation = AsyncMock(return_value=[])
        db.update_resource_status = AsyncMock()
        db.record_reconciliation = AsyncMock()
        db.mark_resource_for_reconciliation = AsyncMock()
        db.update_resource_outputs = AsyncMock()
        db.requeue_failed_resources = AsyncMock()
        return db

    @pytest.fixture
    def mock_registry(self):
        """Create a mock plugin registry."""
        registry = MagicMock()
        registry.get_action_plugin = AsyncMock()
        return registry

    @pytest.fixture
    def controller(self, mock_db, mock_registry):
        """Create a controller for testing."""
        config = ControllerConfig(
            reconcile_interval=1,
            max_concurrent_reconciles=2,
        )
        return Controller(
            db_manager=mock_db,
            registry=mock_registry,
            config=config,
        )

    async def test_stop(self, controller):
        """Test stopping the controller."""
        controller.running = True
        await controller.stop()
        assert controller.running is False

    async def test_trigger_reconciliation(self, controller, mock_db):
        """Test manually triggering reconciliation."""
        await controller.trigger_reconciliation(1)
        mock_db.mark_resource_for_reconciliation.assert_called_once_with(1)

    async def test_get_action_plugin_cached(self, controller, mock_registry):
        """Test that action plugins are cached."""
        mock_plugin = AsyncMock()
        mock_registry.get_action_plugin.return_value = mock_plugin

        plugin1 = await controller._get_action_plugin("github_actions")
        plugin2 = await controller._get_action_plugin("github_actions")

        assert plugin1 is plugin2
        # Should only be called once due to caching
        mock_registry.get_action_plugin.assert_called_once()

    async def test_get_action_plugin_with_config(self, controller, mock_registry):
        """Test getting action plugin with merged config."""
        controller.config.plugin_configs = {"github_actions": {"global": True}}
        mock_plugin = AsyncMock()
        mock_registry.get_action_plugin.return_value = mock_plugin

        await controller._get_action_plugin(
            "github_actions", config={"resource_specific": True}
        )

        # Config should be merged
        call_args = mock_registry.get_action_plugin.call_args
        merged_config = call_args[0][1]
        assert merged_config["global"] is True
        assert merged_config["resource_specific"] is True

    async def test_execute_reconciliation_success(self, controller, mock_db):
        """Test successful reconciliation execution."""
        mock_plugin = AsyncMock()
        mock_plugin.prepare = AsyncMock(return_value="/workspace")
        mock_plugin.plan = AsyncMock(
            return_value=ActionResult(
                success=True,
                phase=ActionPhase.COMPLETED,
                has_changes=True,
                plan_output="Plan output",
            )
        )
        mock_plugin.apply = AsyncMock(
            return_value=ActionResult(
                success=True,
                phase=ActionPhase.COMPLETED,
                apply_output="Apply output",
                resources_created=1,
                outputs={"key": "value"},
            )
        )
        mock_plugin.cleanup = AsyncMock()

        ctx = ActionContext(
            resource_id=1,
            resource_name="test",
            generation=1,
            spec={"key": "value"},
            spec_hash="abc123",
        )
        resource = {"status": "pending"}

        result = await controller._execute_reconciliation(mock_plugin, ctx, resource)

        assert result.success is True
        assert result.phase == "completed"
        mock_plugin.prepare.assert_called_once()
        mock_plugin.plan.assert_called_once()
        mock_plugin.apply.assert_called_once()
        mock_plugin.cleanup.assert_called_once()
        mock_db.update_resource_outputs.assert_called_once_with(1, {"key": "value"})

    async def test_execute_reconciliation_no_changes(self, controller, mock_db):
        """Test reconciliation with no changes needed."""
        mock_plugin = AsyncMock()
        mock_plugin.prepare = AsyncMock(return_value="/workspace")
        mock_plugin.plan = AsyncMock(
            return_value=ActionResult(
                success=True,
                phase=ActionPhase.COMPLETED,
                has_changes=False,
            )
        )
        mock_plugin.cleanup = AsyncMock()

        ctx = ActionContext(
            resource_id=1,
            resource_name="test",
            generation=1,
            spec={},
            spec_hash="abc123",
        )
        resource = {"status": "ready"}

        result = await controller._execute_reconciliation(mock_plugin, ctx, resource)

        assert result.success is True
        assert result.phase == "completed"
        mock_plugin.apply.assert_not_called()

    async def test_execute_reconciliation_plan_failure(self, controller, mock_db):
        """Test reconciliation when plan fails."""
        mock_plugin = AsyncMock()
        mock_plugin.prepare = AsyncMock(return_value="/workspace")
        mock_plugin.plan = AsyncMock(
            return_value=ActionResult(
                success=False,
                phase=ActionPhase.FAILED,
                error_message="Plan failed",
            )
        )
        mock_plugin.cleanup = AsyncMock()

        ctx = ActionContext(
            resource_id=1,
            resource_name="test",
            generation=1,
            spec={},
            spec_hash="abc123",
        )
        resource = {"status": "pending"}

        result = await controller._execute_reconciliation(mock_plugin, ctx, resource)

        assert result.success is False
        assert result.phase == "failed"
        assert "Plan failed" in result.error_message
        mock_plugin.apply.assert_not_called()

    async def test_execute_reconciliation_destroy(self, controller, mock_db):
        """Test reconciliation for resource deletion."""
        mock_plugin = AsyncMock()
        mock_plugin.prepare = AsyncMock(return_value="/workspace")
        mock_plugin.plan = AsyncMock(
            return_value=ActionResult(success=True, has_changes=True)
        )
        mock_plugin.destroy = AsyncMock(
            return_value=ActionResult(
                success=True,
                phase=ActionPhase.COMPLETED,
                resources_deleted=1,
            )
        )
        mock_plugin.cleanup = AsyncMock()

        ctx = ActionContext(
            resource_id=1,
            resource_name="test",
            generation=1,
            spec={},
            spec_hash="abc123",
        )
        resource = {"status": ResourceStatus.DELETING.value}

        result = await controller._execute_reconciliation(mock_plugin, ctx, resource)

        assert result.success is True
        assert result.resources_deleted == 1
        mock_plugin.destroy.assert_called_once()
        mock_plugin.apply.assert_not_called()

    async def test_execute_reconciliation_exception(self, controller, mock_db):
        """Test reconciliation handles exceptions gracefully."""
        mock_plugin = AsyncMock()
        mock_plugin.prepare = AsyncMock(side_effect=Exception("Prepare failed"))
        mock_plugin.cleanup = AsyncMock()

        ctx = ActionContext(
            resource_id=1,
            resource_name="test",
            generation=1,
            spec={},
            spec_hash="abc123",
        )
        resource = {"status": "pending"}

        result = await controller._execute_reconciliation(mock_plugin, ctx, resource)

        assert result.success is False
        assert result.phase == "failed"
        assert "Prepare failed" in result.error_message

    async def test_execute_reconciliation_cleanup_on_error(self, controller, mock_db):
        """Test that cleanup is called even on error."""
        mock_plugin = AsyncMock()
        mock_plugin.prepare = AsyncMock(return_value="/workspace")
        mock_plugin.plan = AsyncMock(side_effect=Exception("Plan error"))
        mock_plugin.cleanup = AsyncMock()

        ctx = ActionContext(
            resource_id=1,
            resource_name="test",
            generation=1,
            spec={},
            spec_hash="abc123",
        )
        resource = {"status": "pending"}

        await controller._execute_reconciliation(mock_plugin, ctx, resource)

        mock_plugin.cleanup.assert_called_once_with("/workspace")

    async def test_reconcile_resource_success(self, controller, mock_db, mock_registry):
        """Test full resource reconciliation success flow."""
        mock_plugin = AsyncMock()
        mock_plugin.prepare = AsyncMock(return_value="/workspace")
        mock_plugin.plan = AsyncMock(
            return_value=ActionResult(success=True, has_changes=True)
        )
        mock_plugin.apply = AsyncMock(
            return_value=ActionResult(
                success=True,
                phase=ActionPhase.COMPLETED,
                resources_updated=1,
            )
        )
        mock_plugin.cleanup = AsyncMock()
        mock_registry.get_action_plugin.return_value = mock_plugin

        resource = {
            "id": 1,
            "name": "test-resource",
            "action_plugin": "github_actions",
            "generation": 1,
            "observed_generation": 0,
            "spec": {"key": "value"},
            "spec_hash": "abc123",
            "plugin_config": {},
            "status": "pending",
            "last_reconcile_time": None,
        }

        await controller._reconcile_resource(resource)

        # Should update status to reconciling, then ready
        assert mock_db.update_resource_status.call_count >= 2
        mock_db.record_reconciliation.assert_called_once()

    async def test_reconcile_resource_failure(self, controller, mock_db, mock_registry):
        """Test resource reconciliation failure flow."""
        mock_plugin = AsyncMock()
        mock_plugin.prepare = AsyncMock(return_value="/workspace")
        mock_plugin.plan = AsyncMock(
            return_value=ActionResult(
                success=False,
                phase=ActionPhase.FAILED,
                error_message="Plan failed",
            )
        )
        mock_plugin.cleanup = AsyncMock()
        mock_registry.get_action_plugin.return_value = mock_plugin

        resource = {
            "id": 1,
            "name": "test-resource",
            "action_plugin": "github_actions",
            "generation": 1,
            "observed_generation": 0,
            "spec": {},
            "spec_hash": "abc123",
            "plugin_config": {},
            "status": "pending",
            "last_reconcile_time": None,
        }

        await controller._reconcile_resource(resource)

        # Should record failure
        record_call = mock_db.record_reconciliation.call_args
        assert record_call[1]["success"] is False

    async def test_reconcile_resource_exception(
        self, controller, mock_db, mock_registry
    ):
        """Test resource reconciliation handles exceptions."""
        mock_registry.get_action_plugin.side_effect = Exception("Plugin error")

        resource = {
            "id": 1,
            "name": "test-resource",
            "action_plugin": "nonexistent",
            "generation": 1,
            "observed_generation": 0,
            "spec": {},
            "spec_hash": "abc123",
            "plugin_config": {},
            "status": "pending",
            "last_reconcile_time": None,
        }

        await controller._reconcile_resource(resource)

        # Should update status to failed
        status_call = mock_db.update_resource_status.call_args
        assert status_call[0][1] == ResourceStatus.FAILED

    async def test_reconciliation_loop_processes_resources(
        self, controller, mock_db, mock_registry
    ):
        """Test that reconciliation loop processes resources."""
        mock_plugin = AsyncMock()
        mock_plugin.prepare = AsyncMock(return_value="/workspace")
        mock_plugin.plan = AsyncMock(
            return_value=ActionResult(success=True, has_changes=False)
        )
        mock_plugin.cleanup = AsyncMock()
        mock_registry.get_action_plugin.return_value = mock_plugin

        resources = [
            {
                "id": 1,
                "name": "resource-1",
                "action_plugin": "github_actions",
                "generation": 1,
                "observed_generation": 0,
                "spec": {},
                "spec_hash": "abc",
                "plugin_config": {},
                "status": "pending",
                "last_reconcile_time": None,
            }
        ]

        # Return resources once, then empty list
        call_count = 0

        async def mock_get_resources(limit):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return resources
            controller.running = False
            return []

        mock_db.get_resources_needing_reconciliation = mock_get_resources

        controller.running = True
        await controller._reconciliation_loop()

        # Should have processed the resource
        assert mock_db.update_resource_status.called

    async def test_requeue_loop(self, controller, mock_db):
        """Test requeue loop calls requeue_failed_resources."""
        call_count = 0

        async def mock_requeue(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                controller.running = False

        mock_db.requeue_failed_resources = mock_requeue

        controller.running = True
        await controller._requeue_loop()

        assert call_count >= 1

    async def test_semaphore_limits_concurrency(
        self, controller, mock_db, mock_registry
    ):
        """Test that semaphore limits concurrent reconciliations."""
        mock_plugin = AsyncMock()
        mock_plugin.prepare = AsyncMock(return_value="/workspace")
        mock_plugin.plan = AsyncMock(
            return_value=ActionResult(success=True, has_changes=False)
        )
        mock_plugin.cleanup = AsyncMock()
        mock_registry.get_action_plugin.return_value = mock_plugin

        # Track concurrent executions
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        original_reconcile = controller._reconcile_resource

        async def tracking_reconcile(resource):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            try:
                await original_reconcile(resource)
            finally:
                async with lock:
                    current_concurrent -= 1

        controller._reconcile_resource = tracking_reconcile

        resources = [
            {
                "id": i,
                "name": f"resource-{i}",
                "action_plugin": "github_actions",
                "generation": 1,
                "observed_generation": 0,
                "spec": {},
                "spec_hash": "abc",
                "plugin_config": {},
                "status": "pending",
                "last_reconcile_time": None,
            }
            for i in range(5)
        ]

        # Run reconciliations concurrently
        await asyncio.gather(*[tracking_reconcile(r) for r in resources])

        # Should be limited by semaphore
        assert max_concurrent <= controller.max_concurrent_reconciles