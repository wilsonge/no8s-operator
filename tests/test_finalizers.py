"""Unit tests for finalizers functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

from controller import Controller, ControllerConfig
from db import DatabaseManager, ResourceStatus
from plugins.base import ActionResult, ActionPhase


class TestFinalizersDB:
    """Tests for finalizer database operations."""

    @pytest.fixture
    def db_manager(self):
        return DatabaseManager(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            password="testpass",
        )

    @pytest.fixture
    def mock_pool(self):
        return AsyncMock()

    def test_parse_resource_row_with_finalizers(self, db_manager):
        """Test parsing resource row includes finalizers."""

        class MockRecord:
            def __init__(self, data):
                self._data = data

            def __iter__(self):
                return iter(self._data.items())

            def keys(self):
                return self._data.keys()

            def get(self, key, default=None):
                return self._data.get(key, default)

            def __getitem__(self, key):
                return self._data[key]

        record = MockRecord(
            {
                "id": 1,
                "name": "test",
                "spec": "{}",
                "plugin_config": "{}",
                "metadata": "{}",
                "outputs": "{}",
                "finalizers": '["github_actions", "custom"]',
                "status": "ready",
            }
        )
        result = db_manager._parse_resource_row(record)
        assert result["finalizers"] == ["github_actions", "custom"]

    def test_parse_resource_row_empty_finalizers(self, db_manager):
        """Test parsing resource row with empty finalizers."""

        class MockRecord:
            def __init__(self, data):
                self._data = data

            def __iter__(self):
                return iter(self._data.items())

            def keys(self):
                return self._data.keys()

            def get(self, key, default=None):
                return self._data.get(key, default)

            def __getitem__(self, key):
                return self._data[key]

        record = MockRecord(
            {
                "id": 1,
                "name": "test",
                "spec": "{}",
                "plugin_config": "{}",
                "metadata": "{}",
                "outputs": "{}",
                "finalizers": "[]",
                "status": "ready",
            }
        )
        result = db_manager._parse_resource_row(record)
        assert result["finalizers"] == []

    def test_parse_resource_row_missing_finalizers(self, db_manager):
        """Test parsing resource row without finalizers key."""

        class MockRecord:
            def __init__(self, data):
                self._data = data

            def __iter__(self):
                return iter(self._data.items())

            def keys(self):
                return self._data.keys()

            def get(self, key, default=None):
                return self._data.get(key, default)

            def __getitem__(self, key):
                return self._data[key]

        record = MockRecord(
            {
                "id": 1,
                "name": "test",
                "spec": "{}",
                "plugin_config": "{}",
                "metadata": "{}",
                "outputs": "{}",
                "status": "ready",
            }
        )
        result = db_manager._parse_resource_row(record)
        assert result["finalizers"] == []


@pytest.mark.asyncio
class TestFinalizersDBAsync:
    """Async tests for finalizer database operations."""

    @pytest.fixture
    def db_manager(self):
        return DatabaseManager(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            password="testpass",
        )

    @pytest.fixture
    def mock_pool(self):
        return AsyncMock()

    async def test_add_finalizer(self, db_manager, mock_pool):
        """Test adding a finalizer to a resource."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.execute = AsyncMock()
            yield conn

        mock_pool.acquire = mock_acquire

        await db_manager.add_finalizer(1, "custom-controller")

    async def test_remove_finalizer(self, db_manager, mock_pool):
        """Test removing a finalizer from a resource."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.execute = AsyncMock()
            yield conn

        mock_pool.acquire = mock_acquire

        await db_manager.remove_finalizer(1, "github_actions")

    async def test_get_finalizers(self, db_manager, mock_pool):
        """Test getting finalizers for a resource."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value='["github_actions", "custom"]')
            yield conn

        mock_pool.acquire = mock_acquire

        finalizers = await db_manager.get_finalizers(1)
        assert finalizers == ["github_actions", "custom"]

    async def test_get_finalizers_not_found(self, db_manager, mock_pool):
        """Test getting finalizers for non-existent resource."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=None)
            yield conn

        mock_pool.acquire = mock_acquire

        finalizers = await db_manager.get_finalizers(999)
        assert finalizers == []

    async def test_hard_delete_blocked_by_finalizers(self, db_manager, mock_pool):
        """Test that hard_delete fails when finalizers remain."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            # fetchval returns None (no row matched the WHERE clause)
            conn.fetchval = AsyncMock(return_value=None)
            yield conn

        mock_pool.acquire = mock_acquire

        result = await db_manager.hard_delete_resource(1)
        assert result is False

    async def test_create_resource_with_default_finalizers(self, db_manager, mock_pool):
        """Test that create_resource sets default finalizers."""
        db_manager.pool = mock_pool
        captured_args = {}

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()

            async def capture_fetchval(query, *args):
                captured_args["args"] = args
                return 1

            conn.fetchval = capture_fetchval
            yield conn

        mock_pool.acquire = mock_acquire

        await db_manager.create_resource(
            name="test",
            resource_type_name="TestType",
            resource_type_version="v1",
            action_plugin="github_actions",
        )

        # The 10th argument should be the finalizers JSON
        assert '["github_actions"]' in str(captured_args["args"])

    async def test_create_resource_with_custom_finalizers(self, db_manager, mock_pool):
        """Test creating a resource with custom finalizers."""
        db_manager.pool = mock_pool
        captured_args = {}

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()

            async def capture_fetchval(query, *args):
                captured_args["args"] = args
                return 1

            conn.fetchval = capture_fetchval
            yield conn

        mock_pool.acquire = mock_acquire

        await db_manager.create_resource(
            name="test",
            resource_type_name="TestType",
            resource_type_version="v1",
            action_plugin="github_actions",
            finalizers=["github_actions", "custom"],
        )

        assert '["github_actions", "custom"]' in str(captured_args["args"])


@pytest.mark.asyncio
class TestFinalizersController:
    """Tests for finalizer handling in the controller."""

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
        registry = MagicMock()
        registry.get_action_plugin = AsyncMock()
        return registry

    @pytest.fixture
    def controller(self, mock_db, mock_registry):
        config = ControllerConfig(reconcile_interval=1, max_concurrent_reconciles=2)
        return Controller(db_manager=mock_db, registry=mock_registry, config=config)

    async def test_destroy_removes_action_plugin_finalizer(
        self, controller, mock_db, mock_registry
    ):
        """Test that successful destroy removes the action plugin finalizer."""
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
        mock_registry.get_action_plugin.return_value = mock_plugin

        resource = {
            "id": 1,
            "name": "test-resource",
            "action_plugin": "github_actions",
            "generation": 1,
            "observed_generation": 1,
            "spec": {},
            "spec_hash": "abc123",
            "plugin_config": {},
            "status": ResourceStatus.DELETING.value,
            "last_reconcile_time": "2024-01-01T00:00:00",
        }

        await controller._reconcile_resource(resource)

        mock_db.remove_finalizer.assert_called_once_with(1, "github_actions")
        mock_db.get_finalizers.assert_called_once_with(1)

    async def test_hard_delete_after_all_finalizers_cleared(
        self, controller, mock_db, mock_registry
    ):
        """Test hard-delete happens when all finalizers are cleared."""
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
        mock_registry.get_action_plugin.return_value = mock_plugin
        mock_db.get_finalizers.return_value = []

        resource = {
            "id": 1,
            "name": "test-resource",
            "action_plugin": "github_actions",
            "generation": 1,
            "observed_generation": 1,
            "spec": {},
            "spec_hash": "abc123",
            "plugin_config": {},
            "status": ResourceStatus.DELETING.value,
            "last_reconcile_time": "2024-01-01T00:00:00",
        }

        await controller._reconcile_resource(resource)

        mock_db.hard_delete_resource.assert_called_once_with(1)

    async def test_no_hard_delete_when_finalizers_remain(
        self, controller, mock_db, mock_registry
    ):
        """Test resource stays when external finalizers exist."""
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
        mock_registry.get_action_plugin.return_value = mock_plugin
        mock_db.get_finalizers.return_value = ["external-controller"]

        resource = {
            "id": 1,
            "name": "test-resource",
            "action_plugin": "github_actions",
            "generation": 1,
            "observed_generation": 1,
            "spec": {},
            "spec_hash": "abc123",
            "plugin_config": {},
            "status": ResourceStatus.DELETING.value,
            "last_reconcile_time": "2024-01-01T00:00:00",
        }

        await controller._reconcile_resource(resource)

        mock_db.remove_finalizer.assert_called_once_with(1, "github_actions")
        mock_db.hard_delete_resource.assert_not_called()

    async def test_destroy_failure_keeps_finalizer(
        self, controller, mock_db, mock_registry
    ):
        """Test that finalizer is not removed on destroy failure."""
        mock_plugin = AsyncMock()
        mock_plugin.prepare = AsyncMock(return_value="/workspace")
        mock_plugin.plan = AsyncMock(
            return_value=ActionResult(success=True, has_changes=True)
        )
        mock_plugin.destroy = AsyncMock(
            return_value=ActionResult(
                success=False,
                phase=ActionPhase.FAILED,
                error_message="Destroy failed",
            )
        )
        mock_plugin.cleanup = AsyncMock()
        mock_registry.get_action_plugin.return_value = mock_plugin

        resource = {
            "id": 1,
            "name": "test-resource",
            "action_plugin": "github_actions",
            "generation": 1,
            "observed_generation": 1,
            "spec": {},
            "spec_hash": "abc123",
            "plugin_config": {},
            "status": ResourceStatus.DELETING.value,
            "last_reconcile_time": "2024-01-01T00:00:00",
        }

        await controller._reconcile_resource(resource)

        mock_db.remove_finalizer.assert_not_called()
        mock_db.hard_delete_resource.assert_not_called()
