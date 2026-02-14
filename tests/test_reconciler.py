"""Unit tests for the reconciler plugin system."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controller import Controller, ControllerConfig
from db import ResourceStatus
from plugins.reconcilers.base import (
    ReconcilerPlugin,
    ReconcilerContext,
    ReconcileResult,
)
from plugins.registry import PluginRegistry, reset_registry

# ==================== Test Helpers ====================


class DummyReconciler(ReconcilerPlugin):
    """Concrete reconciler for testing."""

    def __init__(self):
        self._started = False
        self._stopped = False

    @property
    def name(self) -> str:
        return "dummy"

    @property
    def resource_types(self) -> list[str]:
        return ["DummyResource"]

    async def start(self, ctx: ReconcilerContext) -> None:
        self._started = True
        # Wait for shutdown
        await ctx.shutdown_event.wait()

    async def reconcile(self, resource, ctx):
        return ReconcileResult(success=True, message="OK")

    async def stop(self) -> None:
        self._stopped = True


class MultiTypeReconciler(ReconcilerPlugin):
    """Reconciler that handles multiple resource types."""

    @property
    def name(self) -> str:
        return "multi"

    @property
    def resource_types(self) -> list[str]:
        return ["TypeA", "TypeB"]

    async def start(self, ctx):
        await ctx.shutdown_event.wait()

    async def reconcile(self, resource, ctx):
        return ReconcileResult(success=True)

    async def stop(self):
        pass


# ==================== ReconcileResult Tests ====================


class TestReconcileResult:
    """Tests for ReconcileResult dataclass."""

    def test_default_values(self):
        result = ReconcileResult()
        assert result.success is False
        assert result.message == ""
        assert result.requeue_after is None

    def test_custom_values(self):
        result = ReconcileResult(success=True, message="All good", requeue_after=300)
        assert result.success is True
        assert result.message == "All good"
        assert result.requeue_after == 300


# ==================== ReconcilerPlugin Base Tests ====================


class TestReconcilerPlugin:
    """Tests for ReconcilerPlugin abstract base class."""

    def test_cannot_instantiate_abstract(self):
        """Test that ReconcilerPlugin cannot be instantiated directly."""
        with pytest.raises(TypeError):
            ReconcilerPlugin()

    def test_concrete_subclass(self):
        """Test that a concrete subclass can be instantiated."""
        reconciler = DummyReconciler()
        assert reconciler.name == "dummy"
        assert reconciler.resource_types == ["DummyResource"]

    def test_multi_type_reconciler(self):
        """Test reconciler handling multiple resource types."""
        reconciler = MultiTypeReconciler()
        assert reconciler.name == "multi"
        assert reconciler.resource_types == ["TypeA", "TypeB"]

    def test_incomplete_subclass_raises(self):
        """Test that a subclass missing methods raises TypeError."""

        class IncompleteReconciler(ReconcilerPlugin):
            @property
            def name(self):
                return "incomplete"

        with pytest.raises(TypeError):
            IncompleteReconciler()


# ==================== ReconcilerContext Tests ====================


@pytest.mark.asyncio
class TestReconcilerContext:
    """Tests for ReconcilerContext."""

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.get_resources_needing_reconciliation_by_type = AsyncMock(return_value=[])
        db.update_resource_status = AsyncMock()
        db.record_reconciliation = AsyncMock()
        db.remove_finalizer = AsyncMock()
        db.get_finalizers = AsyncMock(return_value=[])
        db.hard_delete_resource = AsyncMock(return_value=True)
        return db

    @pytest.fixture
    def mock_registry(self):
        registry = MagicMock()
        registry.get_action_plugin = AsyncMock()
        return registry

    @pytest.fixture
    def ctx(self, mock_db, mock_registry):
        return ReconcilerContext(
            db=mock_db,
            registry=mock_registry,
            shutdown_event=asyncio.Event(),
        )

    async def test_get_resources_needing_reconciliation(self, ctx, mock_db):
        """Test that get_resources delegates to DB with type filter."""
        mock_db.get_resources_needing_reconciliation_by_type.return_value = [
            {"id": 1, "name": "test"}
        ]

        resources = await ctx.get_resources_needing_reconciliation(["DummyResource"])

        mock_db.get_resources_needing_reconciliation_by_type.assert_called_once_with(
            resource_type_names=["DummyResource"],
            limit=10,
        )
        assert len(resources) == 1
        assert resources[0]["id"] == 1

    async def test_get_resources_custom_limit(self, ctx, mock_db):
        """Test custom limit parameter."""
        await ctx.get_resources_needing_reconciliation(["TypeA"], limit=5)

        mock_db.get_resources_needing_reconciliation_by_type.assert_called_once_with(
            resource_type_names=["TypeA"],
            limit=5,
        )

    async def test_update_status(self, ctx, mock_db):
        """Test that update_status delegates to db.update_resource_status."""
        await ctx.update_status(1, "ready", message="Done", observed_generation=3)

        mock_db.update_resource_status.assert_called_once_with(
            resource_id=1,
            status=ResourceStatus.READY,
            message="Done",
            observed_generation=3,
        )

    async def test_update_status_reconciling(self, ctx, mock_db):
        """Test update_status with reconciling status."""
        await ctx.update_status(1, "reconciling", message="Working...")

        mock_db.update_resource_status.assert_called_once_with(
            resource_id=1,
            status=ResourceStatus.RECONCILING,
            message="Working...",
            observed_generation=None,
        )

    async def test_get_action_plugin(self, ctx, mock_registry):
        """Test that get_action_plugin delegates to registry."""
        mock_plugin = AsyncMock()
        mock_registry.get_action_plugin.return_value = mock_plugin

        plugin = await ctx.get_action_plugin("github_actions")

        mock_registry.get_action_plugin.assert_called_once_with("github_actions")
        assert plugin is mock_plugin

    async def test_record_reconciliation_success(self, ctx, mock_db):
        """Test recording a successful reconciliation."""
        result = ReconcileResult(success=True, message="OK")

        await ctx.record_reconciliation(
            resource_id=1,
            result=result,
            duration_seconds=1.5,
            trigger_reason="initial",
        )

        mock_db.record_reconciliation.assert_called_once_with(
            resource_id=1,
            success=True,
            phase="completed",
            error_message=None,
            duration_seconds=1.5,
            trigger_reason="initial",
            drift_detected=False,
        )

    async def test_record_reconciliation_failure(self, ctx, mock_db):
        """Test recording a failed reconciliation."""
        result = ReconcileResult(success=False, message="Connection refused")

        await ctx.record_reconciliation(
            resource_id=1,
            result=result,
            duration_seconds=0.3,
            trigger_reason="retry",
            drift_detected=True,
        )

        mock_db.record_reconciliation.assert_called_once_with(
            resource_id=1,
            success=False,
            phase="failed",
            error_message="Connection refused",
            duration_seconds=0.3,
            trigger_reason="retry",
            drift_detected=True,
        )

    async def test_remove_finalizer(self, ctx, mock_db):
        """Test remove_finalizer delegates to DB."""
        await ctx.remove_finalizer(1, "dummy")
        mock_db.remove_finalizer.assert_called_once_with(1, "dummy")

    async def test_get_finalizers(self, ctx, mock_db):
        """Test get_finalizers delegates to DB."""
        mock_db.get_finalizers.return_value = ["dummy", "external"]
        finalizers = await ctx.get_finalizers(1)
        assert finalizers == ["dummy", "external"]

    async def test_hard_delete_resource(self, ctx, mock_db):
        """Test hard_delete_resource delegates to DB."""
        result = await ctx.hard_delete_resource(1)
        mock_db.hard_delete_resource.assert_called_once_with(1)
        assert result is True

    async def test_shutdown_event(self, ctx):
        """Test that the shutdown event is accessible."""
        assert not ctx.shutdown_event.is_set()
        ctx.shutdown_event.set()
        assert ctx.shutdown_event.is_set()


# ==================== PluginRegistry Reconciler Tests ====================


class TestPluginRegistryReconciler:
    """Tests for reconciler support in PluginRegistry."""

    @pytest.fixture(autouse=True)
    def fresh_registry(self):
        """Ensure a fresh registry for each test."""
        reset_registry()
        yield
        reset_registry()

    def test_register_reconciler_plugin(self):
        """Test registering a reconciler plugin."""
        registry = PluginRegistry()
        registry.register_reconciler_plugin(DummyReconciler)

        assert "dummy" in registry.list_reconciler_plugins()

    def test_register_reconciler_info(self):
        """Test reconciler plugin info is cached."""
        registry = PluginRegistry()
        registry.register_reconciler_plugin(DummyReconciler)

        info = registry.get_reconciler_plugin_info("dummy")
        assert info is not None
        assert info["name"] == "dummy"
        assert info["resource_types"] == ["DummyResource"]

    def test_get_reconciler_plugin(self):
        """Test getting a reconciler instance."""
        registry = PluginRegistry()
        registry.register_reconciler_plugin(DummyReconciler)

        instance = registry.get_reconciler_plugin("dummy")
        assert isinstance(instance, DummyReconciler)

    def test_get_reconciler_plugin_cached(self):
        """Test that reconciler instances are cached."""
        registry = PluginRegistry()
        registry.register_reconciler_plugin(DummyReconciler)

        instance1 = registry.get_reconciler_plugin("dummy")
        instance2 = registry.get_reconciler_plugin("dummy")
        assert instance1 is instance2

    def test_get_unknown_reconciler_raises(self):
        """Test that getting an unknown reconciler raises ValueError."""
        registry = PluginRegistry()

        with pytest.raises(ValueError, match="Unknown reconciler plugin"):
            registry.get_reconciler_plugin("nonexistent")

    def test_has_reconciler_for_resource_type(self):
        """Test checking if a reconciler handles a resource type."""
        registry = PluginRegistry()
        registry.register_reconciler_plugin(DummyReconciler)

        assert registry.has_reconciler_for_resource_type("DummyResource") is True
        assert registry.has_reconciler_for_resource_type("Unknown") is False

    def test_get_reconciler_for_resource_type(self):
        """Test getting the reconciler for a resource type."""
        registry = PluginRegistry()
        registry.register_reconciler_plugin(DummyReconciler)

        reconciler = registry.get_reconciler_for_resource_type("DummyResource")
        assert isinstance(reconciler, DummyReconciler)

    def test_get_reconciler_for_unknown_resource_type(self):
        """Test getting reconciler for unregistered resource type returns None."""
        registry = PluginRegistry()

        assert registry.get_reconciler_for_resource_type("Unknown") is None

    def test_multi_type_reconciler_registration(self):
        """Test reconciler handling multiple resource types."""
        registry = PluginRegistry()
        registry.register_reconciler_plugin(MultiTypeReconciler)

        assert registry.has_reconciler_for_resource_type("TypeA") is True
        assert registry.has_reconciler_for_resource_type("TypeB") is True

        # Both types should map to the same reconciler
        reconciler_a = registry.get_reconciler_for_resource_type("TypeA")
        reconciler_b = registry.get_reconciler_for_resource_type("TypeB")
        assert reconciler_a is reconciler_b

    def test_resource_type_conflict_raises(self):
        """Test that registering conflicting resource types raises ValueError."""
        registry = PluginRegistry()
        registry.register_reconciler_plugin(DummyReconciler)

        class ConflictingReconciler(ReconcilerPlugin):
            @property
            def name(self):
                return "conflicting"

            @property
            def resource_types(self):
                return ["DummyResource"]  # Already claimed by dummy

            async def start(self, ctx):
                pass

            async def reconcile(self, resource, ctx):
                return ReconcileResult()

            async def stop(self):
                pass

        with pytest.raises(ValueError, match="already claimed"):
            registry.register_reconciler_plugin(ConflictingReconciler)

    def test_list_reconciler_plugins_empty(self):
        """Test listing reconciler plugins when none registered."""
        registry = PluginRegistry()
        assert registry.list_reconciler_plugins() == []

    def test_overwrite_same_reconciler_warns(self):
        """Test that re-registering the same reconciler logs a warning."""
        registry = PluginRegistry()
        registry.register_reconciler_plugin(DummyReconciler)
        # Re-register the same class (same name, same resource types)
        registry.register_reconciler_plugin(DummyReconciler)

        assert len(registry.list_reconciler_plugins()) == 1

    def test_get_reconciler_plugin_info_not_found(self):
        """Test getting info for unregistered reconciler returns None."""
        registry = PluginRegistry()
        assert registry.get_reconciler_plugin_info("nonexistent") is None


# ==================== Entry Point Discovery Tests ====================


class TestEntryPointDiscovery:
    """Tests for reconciler plugin discovery via entry points."""

    @pytest.fixture(autouse=True)
    def fresh_registry(self):
        reset_registry()
        yield
        reset_registry()

    @patch("plugins.registry.entry_points")
    def test_discover_reconciler_via_entry_point(self, mock_entry_points):
        """Test that reconcilers are discovered via entry points."""
        mock_ep = MagicMock()
        mock_ep.name = "dummy"
        mock_ep.load.return_value = DummyReconciler
        mock_entry_points.return_value = [mock_ep]

        from plugins.registry import register_builtin_plugins, get_registry

        # Patch action/input imports to avoid failures
        with patch(
            "plugins.registry.GitHubActionsPlugin",
            create=True,
        ):
            with patch.dict(
                "sys.modules",
                {
                    "plugins.actions.github_actions": MagicMock(
                        GitHubActionsPlugin=MagicMock(
                            return_value=MagicMock(
                                name="github_actions", version="1.0.0"
                            )
                        )
                    ),
                    "plugins.inputs.http": MagicMock(
                        HTTPInputPlugin=MagicMock(
                            return_value=MagicMock(name="http", version="1.0.0")
                        )
                    ),
                },
            ):
                register_builtin_plugins()

        registry = get_registry()
        mock_entry_points.assert_called_once_with(group="no8s.reconcilers")
        assert registry.has_reconciler_for_resource_type("DummyResource")

    @patch("plugins.registry.entry_points")
    def test_failed_entry_point_graceful(self, mock_entry_points):
        """Test that a failing entry point is handled gracefully."""
        mock_ep = MagicMock()
        mock_ep.name = "broken"
        mock_ep.load.side_effect = ImportError("broken module")
        mock_entry_points.return_value = [mock_ep]

        from plugins.registry import register_builtin_plugins, get_registry

        with patch.dict(
            "sys.modules",
            {
                "plugins.actions.github_actions": MagicMock(
                    GitHubActionsPlugin=MagicMock(
                        return_value=MagicMock(name="github_actions", version="1.0.0")
                    )
                ),
                "plugins.inputs.http": MagicMock(
                    HTTPInputPlugin=MagicMock(
                        return_value=MagicMock(name="http", version="1.0.0")
                    )
                ),
            },
        ):
            # Should not raise
            register_builtin_plugins()

        registry = get_registry()
        assert registry.list_reconciler_plugins() == []

    @patch("plugins.registry.entry_points")
    def test_no_reconciler_entry_points(self, mock_entry_points):
        """Test when no reconciler entry points exist."""
        mock_entry_points.return_value = []

        from plugins.registry import register_builtin_plugins, get_registry

        with patch.dict(
            "sys.modules",
            {
                "plugins.actions.github_actions": MagicMock(
                    GitHubActionsPlugin=MagicMock(
                        return_value=MagicMock(name="github_actions", version="1.0.0")
                    )
                ),
                "plugins.inputs.http": MagicMock(
                    HTTPInputPlugin=MagicMock(
                        return_value=MagicMock(name="http", version="1.0.0")
                    )
                ),
            },
        ):
            register_builtin_plugins()

        registry = get_registry()
        assert registry.list_reconciler_plugins() == []


# ==================== Controller Integration Tests ====================


@pytest.mark.asyncio
class TestControllerReconcilerIntegration:
    """Tests for controller integration with reconciler plugins."""

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.get_resources_needing_reconciliation = AsyncMock(return_value=[])
        db.update_resource_status = AsyncMock()
        db.record_reconciliation = AsyncMock()
        db.mark_resource_for_reconciliation = AsyncMock()
        db.update_resource_outputs = AsyncMock()
        db.requeue_failed_resources = AsyncMock()
        db.hard_delete_resource = AsyncMock(return_value=True)
        db.add_finalizer = AsyncMock()
        db.remove_finalizer = AsyncMock()
        db.get_finalizers = AsyncMock(return_value=[])
        return db

    @pytest.fixture
    def mock_registry(self):
        registry = MagicMock(spec=PluginRegistry)
        registry.get_action_plugin = AsyncMock()
        registry.list_reconciler_plugins.return_value = []
        return registry

    @pytest.fixture
    def controller(self, mock_db, mock_registry):
        config = ControllerConfig(
            reconcile_interval=1,
            max_concurrent_reconciles=2,
        )
        return Controller(
            db_manager=mock_db,
            registry=mock_registry,
            config=config,
        )

    async def test_start_calls_reconciler_start(self, mock_db, mock_registry):
        """Test that controller.start() starts reconciler plugins."""
        mock_reconciler = AsyncMock()
        mock_reconciler.name = "test_reconciler"
        mock_reconciler.start = AsyncMock()

        mock_registry.list_reconciler_plugins.return_value = ["test_reconciler"]
        mock_registry.get_reconciler_plugin.return_value = mock_reconciler

        config = ControllerConfig(reconcile_interval=1)
        controller = Controller(
            db_manager=mock_db,
            registry=mock_registry,
            config=config,
        )

        # Start the controller but stop it immediately
        controller.running = True

        # Start reconcilers
        await controller._start_reconcilers()

        mock_registry.get_reconciler_plugin.assert_called_once_with("test_reconciler")
        # The reconciler task should have been created
        assert len(controller._reconciler_tasks) == 1

    async def test_stop_sets_shutdown_event(self, controller):
        """Test that stop() sets the shutdown event."""
        controller.running = True
        assert not controller._shutdown_event.is_set()

        await controller.stop()

        assert controller._shutdown_event.is_set()
        assert controller.running is False

    async def test_stop_calls_reconciler_stop(self, mock_db, mock_registry):
        """Test that stop() calls stop on all reconciler plugins."""
        mock_reconciler = AsyncMock()
        mock_reconciler.name = "test_reconciler"
        mock_reconciler.stop = AsyncMock()

        mock_registry.list_reconciler_plugins.return_value = ["test_reconciler"]
        mock_registry.get_reconciler_plugin.return_value = mock_reconciler

        config = ControllerConfig(reconcile_interval=1)
        controller = Controller(
            db_manager=mock_db,
            registry=mock_registry,
            config=config,
        )
        controller.running = True

        await controller.stop()

        mock_reconciler.stop.assert_called_once()

    async def test_reconciler_crash_is_caught(self, mock_db, mock_registry):
        """Test that a crashing reconciler doesn't take down the controller."""
        mock_reconciler = AsyncMock()
        mock_reconciler.name = "crasher"
        mock_reconciler.start = AsyncMock(side_effect=Exception("Reconciler crash!"))

        mock_registry.list_reconciler_plugins.return_value = ["crasher"]
        mock_registry.get_reconciler_plugin.return_value = mock_reconciler

        config = ControllerConfig(reconcile_interval=1)
        controller = Controller(
            db_manager=mock_db,
            registry=mock_registry,
            config=config,
        )

        ctx = ReconcilerContext(
            db=mock_db,
            registry=mock_registry,
            shutdown_event=asyncio.Event(),
        )

        # Should not raise
        await controller._run_reconciler(mock_reconciler, ctx)

    async def test_no_reconcilers_registered(self, controller, mock_registry):
        """Test controller works normally with no reconcilers."""
        mock_registry.list_reconciler_plugins.return_value = []

        await controller._start_reconcilers()

        assert len(controller._reconciler_tasks) == 0

    async def test_stop_cancels_reconciler_tasks(self, mock_db, mock_registry):
        """Test that stop cancels running reconciler tasks."""
        mock_reconciler = AsyncMock()
        mock_reconciler.name = "slow"
        mock_reconciler.stop = AsyncMock()

        # Simulate a long-running reconciler
        async def slow_start(ctx):
            await asyncio.sleep(3600)

        mock_reconciler.start = slow_start

        mock_registry.list_reconciler_plugins.return_value = ["slow"]
        mock_registry.get_reconciler_plugin.return_value = mock_reconciler

        config = ControllerConfig(reconcile_interval=1)
        controller = Controller(
            db_manager=mock_db,
            registry=mock_registry,
            config=config,
        )
        controller.running = True

        await controller._start_reconcilers()
        assert len(controller._reconciler_tasks) == 1

        await controller.stop()

        # Tasks should be cleared after stop
        assert len(controller._reconciler_tasks) == 0


# ==================== DatabaseManager Method Tests ====================


@pytest.mark.asyncio
class TestDatabaseReconciliationByType:
    """Tests for get_resources_needing_reconciliation_by_type."""

    @pytest.fixture
    def mock_pool(self):
        pool = AsyncMock()
        return pool

    async def test_empty_resource_types_returns_empty(self, mock_pool):
        """Test that empty resource_type_names returns empty list."""
        from db import DatabaseManager

        db = DatabaseManager(
            host="localhost",
            port=5432,
            database="test",
            user="test",
            password="test",
        )
        db.pool = mock_pool

        result = await db.get_resources_needing_reconciliation_by_type(
            resource_type_names=[], limit=10
        )

        assert result == []
        # Should not hit the database
        mock_pool.acquire.assert_not_called()

    async def test_filters_by_resource_type(self, mock_pool):
        """Test that query includes resource type filter."""
        from db import DatabaseManager

        db = DatabaseManager(
            host="localhost",
            port=5432,
            database="test",
            user="test",
            password="test",
        )
        db.pool = mock_pool

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        @asynccontextmanager
        async def mock_acquire():
            yield mock_conn

        mock_pool.acquire = mock_acquire

        result = await db.get_resources_needing_reconciliation_by_type(
            resource_type_names=["DatabaseCluster"],
            limit=5,
        )

        assert result == []
        # Verify the query was called with the resource type name and limit
        call_args = mock_conn.fetch.call_args
        assert "resource_type_name IN" in call_args[0][0]
        assert "DatabaseCluster" in call_args[0]
        assert 5 in call_args[0]

    async def test_multiple_resource_types(self, mock_pool):
        """Test filtering by multiple resource type names."""
        from db import DatabaseManager

        db = DatabaseManager(
            host="localhost",
            port=5432,
            database="test",
            user="test",
            password="test",
        )
        db.pool = mock_pool

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        @asynccontextmanager
        async def mock_acquire():
            yield mock_conn

        mock_pool.acquire = mock_acquire

        await db.get_resources_needing_reconciliation_by_type(
            resource_type_names=["TypeA", "TypeB"],
            limit=10,
        )

        call_args = mock_conn.fetch.call_args
        query = call_args[0][0]
        assert "$1" in query
        assert "$2" in query
        assert "$3" in query  # limit parameter
        assert call_args[0][1] == "TypeA"
        assert call_args[0][2] == "TypeB"
        assert call_args[0][3] == 10
