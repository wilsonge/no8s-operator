"""Unit tests for db.py - Database manager."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

from db import DatabaseManager, ResourceStatus, ReconciliationResult


class TestResourceStatus:
    """Tests for ResourceStatus enum."""

    def test_status_values(self):
        """Test status enum values."""
        assert ResourceStatus.PENDING.value == "pending"
        assert ResourceStatus.RECONCILING.value == "reconciling"
        assert ResourceStatus.READY.value == "ready"
        assert ResourceStatus.FAILED.value == "failed"
        assert ResourceStatus.DELETING.value == "deleting"

    def test_all_statuses_exist(self):
        """Test all expected statuses exist."""
        statuses = [s.value for s in ResourceStatus]
        assert "pending" in statuses
        assert "reconciling" in statuses
        assert "ready" in statuses
        assert "failed" in statuses
        assert "deleting" in statuses


class TestReconciliationResult:
    """Tests for ReconciliationResult dataclass."""

    def test_default_values(self):
        """Test default values."""
        result = ReconciliationResult()
        assert result.success is False
        assert result.phase == "pending"
        assert result.plan_output == ""
        assert result.apply_output == ""
        assert result.error_message is None
        assert result.resources_created == 0
        assert result.resources_updated == 0
        assert result.resources_deleted == 0
        assert result.has_changes is False

    def test_custom_values(self):
        """Test custom values."""
        result = ReconciliationResult(
            success=True,
            phase="completed",
            plan_output="Plan output",
            apply_output="Apply output",
            error_message=None,
            resources_created=1,
            resources_updated=2,
            resources_deleted=3,
            has_changes=True,
        )
        assert result.success is True
        assert result.phase == "completed"
        assert result.plan_output == "Plan output"
        assert result.apply_output == "Apply output"
        assert result.resources_created == 1
        assert result.resources_updated == 2
        assert result.resources_deleted == 3
        assert result.has_changes is True


class TestDatabaseManager:
    """Tests for DatabaseManager class."""

    def test_init(self):
        """Test database manager initialization."""
        db = DatabaseManager(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            password="testpass",
            min_pool_size=3,
            max_pool_size=10,
        )
        assert db.host == "localhost"
        assert db.port == 5432
        assert db.database == "testdb"
        assert db.user == "testuser"
        assert db.password == "testpass"
        assert db.min_pool_size == 3
        assert db.max_pool_size == 10
        assert db.pool is None

    def test_init_default_pool_sizes(self):
        """Test default pool sizes."""
        db = DatabaseManager(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            password="testpass",
        )
        assert db.min_pool_size == 5
        assert db.max_pool_size == 20

    def test_ensure_connected_raises_when_not_connected(self):
        """Test _ensure_connected raises when pool is None."""
        db = DatabaseManager(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            password="testpass",
        )
        with pytest.raises(RuntimeError) as exc_info:
            db._ensure_connected()
        assert "Database not connected" in str(exc_info.value)

    def test_calculate_spec_hash(self):
        """Test spec hash calculation."""
        db = DatabaseManager(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            password="testpass",
        )
        spec1 = {"key": "value", "number": 42}
        spec2 = {"number": 42, "key": "value"}
        spec3 = {"key": "different"}

        hash1 = db._calculate_spec_hash(spec1)
        hash2 = db._calculate_spec_hash(spec2)
        hash3 = db._calculate_spec_hash(spec3)

        # Same content, different order should produce same hash
        assert hash1 == hash2
        # Different content should produce different hash
        assert hash1 != hash3
        # Hash should be 64 characters (SHA256)
        assert len(hash1) == 64

    def test_calculate_spec_hash_empty(self):
        """Test spec hash for empty spec."""
        db = DatabaseManager(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            password="testpass",
        )
        hash_empty = db._calculate_spec_hash({})
        assert len(hash_empty) == 64

    def test_parse_resource_row(self):
        """Test parsing resource row from database."""
        db = DatabaseManager(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            password="testpass",
        )
        # Mock a database row (dict-like)
        row = MagicMock()
        row.__iter__ = MagicMock(
            return_value=iter(
                [
                    ("id", 1),
                    ("name", "test"),
                    ("spec", '{"key": "value"}'),
                    ("plugin_config", '{"option": true}'),
                    ("metadata", '{"label": "test"}'),
                    ("outputs", '{"result": 42}'),
                    ("status", "ready"),
                ]
            )
        )
        row.keys = MagicMock(
            return_value=[
                "id",
                "name",
                "spec",
                "plugin_config",
                "metadata",
                "outputs",
                "status",
            ]
        )
        row.__getitem__ = lambda self, key: {
            "id": 1,
            "name": "test",
            "spec": '{"key": "value"}',
            "plugin_config": '{"option": true}',
            "metadata": '{"label": "test"}',
            "outputs": '{"result": 42}',
            "status": "ready",
        }[key]

        # Create a proper dict from the mock
        mock_dict = {
            "id": 1,
            "name": "test",
            "spec": '{"key": "value"}',
            "plugin_config": '{"option": true}',
            "metadata": '{"label": "test"}',
            "outputs": '{"result": 42}',
            "status": "ready",
        }

        # Create a mock that behaves like asyncpg.Record
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

        record = MockRecord(mock_dict)
        result = db._parse_resource_row(record)

        assert result["id"] == 1
        assert result["name"] == "test"
        assert result["spec"] == {"key": "value"}
        assert result["plugin_config"] == {"option": True}
        assert result["metadata"] == {"label": "test"}
        assert result["outputs"] == {"result": 42}
        assert result["status"] == "ready"

    def test_parse_resource_row_empty_json_fields(self):
        """Test parsing resource row with empty JSON fields."""
        db = DatabaseManager(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            password="testpass",
        )

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

        mock_dict = {
            "id": 1,
            "name": "test",
            "spec": None,
            "plugin_config": None,
            "metadata": None,
            "outputs": None,
            "status": "pending",
        }
        record = MockRecord(mock_dict)
        result = db._parse_resource_row(record)

        assert result["spec"] == {}
        assert result["plugin_config"] == {}
        assert result["metadata"] == {}
        assert result["outputs"] == {}

    def test_parse_resource_type_row(self):
        """Test parsing resource type row from database."""
        db = DatabaseManager(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            password="testpass",
        )

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

        mock_dict = {
            "id": 1,
            "name": "GitHubWorkflow",
            "version": "v1",
            "schema": '{"type": "object"}',
            "metadata": '{"label": "test"}',
            "status": "active",
        }
        record = MockRecord(mock_dict)
        result = db._parse_resource_type_row(record)

        assert result["id"] == 1
        assert result["name"] == "GitHubWorkflow"
        assert result["version"] == "v1"
        assert result["schema"] == {"type": "object"}
        assert result["metadata"] == {"label": "test"}


@pytest.mark.asyncio
class TestDatabaseManagerAsync:
    """Async tests for DatabaseManager."""

    @pytest.fixture
    def db_manager(self):
        """Create a database manager for testing."""
        return DatabaseManager(
            host="localhost",
            port=5432,
            database="testdb",
            user="testuser",
            password="testpass",
        )

    @pytest.fixture
    def mock_pool(self):
        """Create a mock pool."""
        pool = AsyncMock()
        return pool

    async def test_connect(self, db_manager):
        """Test database connection."""
        with patch("db.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
            mock_pool = AsyncMock()
            mock_create.return_value = mock_pool

            await db_manager.connect()

            mock_create.assert_called_once_with(
                host="localhost",
                port=5432,
                database="testdb",
                user="testuser",
                password="testpass",
                min_size=5,
                max_size=20,
                command_timeout=60,
            )
            assert db_manager.pool is mock_pool

    async def test_close(self, db_manager, mock_pool):
        """Test database connection close."""
        db_manager.pool = mock_pool

        await db_manager.close()

        mock_pool.close.assert_called_once()

    async def test_close_when_not_connected(self, db_manager):
        """Test close when not connected does nothing."""
        db_manager.pool = None
        await db_manager.close()
        # Should not raise

    async def test_create_resource(self, db_manager, mock_pool):
        """Test creating a resource."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=1)
            yield conn

        mock_pool.acquire = mock_acquire

        resource_id = await db_manager.create_resource(
            name="test-resource",
            resource_type_name="GitHubWorkflow",
            resource_type_version="v1",
            action_plugin="github_actions",
            spec={"owner": "test", "repo": "test-repo"},
            plugin_config={"timeout": 3600},
            metadata={"env": "test"},
        )

        assert resource_id == 1

    async def test_create_resource_default_values(self, db_manager, mock_pool):
        """Test creating a resource with default values."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=1)
            yield conn

        mock_pool.acquire = mock_acquire

        resource_id = await db_manager.create_resource(
            name="test-resource",
            resource_type_name="GitHubWorkflow",
            resource_type_version="v1",
            action_plugin="github_actions",
        )

        assert resource_id == 1

    async def test_get_resource(self, db_manager, mock_pool):
        """Test getting a resource by ID."""
        db_manager.pool = mock_pool

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

        mock_row = MockRecord(
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

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(return_value=mock_row)
            yield conn

        mock_pool.acquire = mock_acquire

        resource = await db_manager.get_resource(1)

        assert resource is not None
        assert resource["id"] == 1
        assert resource["name"] == "test"

    async def test_get_resource_not_found(self, db_manager, mock_pool):
        """Test getting a non-existent resource."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(return_value=None)
            yield conn

        mock_pool.acquire = mock_acquire

        resource = await db_manager.get_resource(999)
        assert resource is None

    async def test_update_resource(self, db_manager, mock_pool):
        """Test updating a resource."""
        db_manager.pool = mock_pool

        class MockRecord:
            def __init__(self, data):
                self._data = data

            def get(self, key, default=None):
                return self._data.get(key, default)

            def __getitem__(self, key):
                return self._data[key]

        current_resource = MockRecord(
            {
                "spec": '{"owner": "old"}',
                "plugin_config": "{}",
                "generation": 1,
            }
        )

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(return_value=current_resource)
            conn.execute = AsyncMock()
            yield conn

        mock_pool.acquire = mock_acquire

        await db_manager.update_resource(
            resource_id=1,
            spec={"owner": "new"},
        )

    async def test_update_resource_not_found(self, db_manager, mock_pool):
        """Test updating a non-existent resource."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(return_value=None)
            yield conn

        mock_pool.acquire = mock_acquire

        with pytest.raises(ValueError) as exc_info:
            await db_manager.update_resource(resource_id=999, spec={"key": "value"})

        assert "not found" in str(exc_info.value)

    async def test_delete_resource(self, db_manager, mock_pool):
        """Test deleting (soft delete) a resource."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.execute = AsyncMock()
            yield conn

        mock_pool.acquire = mock_acquire

        await db_manager.delete_resource(1)

    async def test_update_resource_status(self, db_manager, mock_pool):
        """Test updating resource status."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.execute = AsyncMock()
            yield conn

        mock_pool.acquire = mock_acquire

        await db_manager.update_resource_status(
            resource_id=1,
            status=ResourceStatus.READY,
            message="Success",
            observed_generation=1,
        )

    async def test_mark_resource_for_reconciliation(self, db_manager, mock_pool):
        """Test marking resource for reconciliation."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.execute = AsyncMock()
            yield conn

        mock_pool.acquire = mock_acquire

        await db_manager.mark_resource_for_reconciliation(1)

    async def test_update_resource_outputs(self, db_manager, mock_pool):
        """Test updating resource outputs."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.execute = AsyncMock()
            yield conn

        mock_pool.acquire = mock_acquire

        await db_manager.update_resource_outputs(1, {"key": "value"})

    async def test_create_resource_type(self, db_manager, mock_pool):
        """Test creating a resource type."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=1)
            yield conn

        mock_pool.acquire = mock_acquire

        resource_type_id = await db_manager.create_resource_type(
            name="GitHubWorkflow",
            version="v1",
            schema={"type": "object"},
            description="Test resource type",
            metadata={"label": "test"},
        )

        assert resource_type_id == 1

    async def test_get_resource_type(self, db_manager, mock_pool):
        """Test getting a resource type by ID."""
        db_manager.pool = mock_pool

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

        mock_row = MockRecord(
            {
                "id": 1,
                "name": "GitHubWorkflow",
                "version": "v1",
                "schema": '{"type": "object"}',
                "metadata": "{}",
            }
        )

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(return_value=mock_row)
            yield conn

        mock_pool.acquire = mock_acquire

        rt = await db_manager.get_resource_type(1)
        assert rt is not None
        assert rt["name"] == "GitHubWorkflow"

    async def test_get_resource_type_not_found(self, db_manager, mock_pool):
        """Test getting a non-existent resource type."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(return_value=None)
            yield conn

        mock_pool.acquire = mock_acquire

        rt = await db_manager.get_resource_type(999)
        assert rt is None

    async def test_record_reconciliation(self, db_manager, mock_pool):
        """Test recording reconciliation history."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=1)
            conn.execute = AsyncMock()
            yield conn

        mock_pool.acquire = mock_acquire

        await db_manager.record_reconciliation(
            resource_id=1,
            success=True,
            phase="completed",
            plan_output="Plan output",
            apply_output="Apply output",
            resources_created=1,
            resources_updated=0,
            resources_deleted=0,
            duration_seconds=10.5,
            trigger_reason="spec_change",
            drift_detected=False,
        )

    async def test_initialize_schema_calls_run_migrations(self, db_manager, mock_pool):
        """Test that initialize_schema delegates to run_migrations."""
        db_manager.pool = mock_pool

        with patch("db.run_migrations", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = 1
            await db_manager.initialize_schema()
            mock_run.assert_called_once_with(mock_pool)

    async def test_initialize_schema_raises_when_not_connected(self, db_manager):
        """Test that initialize_schema raises when pool is None."""
        with pytest.raises(RuntimeError, match="Database not connected"):
            await db_manager.initialize_schema()

    async def test_hard_delete_resource(self, db_manager, mock_pool):
        """Test hard-deleting a soft-deleted resource."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=1)
            yield conn

        mock_pool.acquire = mock_acquire

        result = await db_manager.hard_delete_resource(1)
        assert result is True

    async def test_hard_delete_resource_not_found(self, db_manager, mock_pool):
        """Test hard-deleting a resource that doesn't exist or isn't soft-deleted."""
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=None)
            yield conn

        mock_pool.acquire = mock_acquire

        result = await db_manager.hard_delete_resource(999)
        assert result is False

    async def test_get_reconciliation_history(self, db_manager, mock_pool):
        """Test getting reconciliation history."""
        db_manager.pool = mock_pool

        mock_rows = [
            {
                "id": 1,
                "resource_id": 1,
                "generation": 1,
                "success": True,
                "phase": "completed",
            }
        ]

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetch = AsyncMock(return_value=mock_rows)
            yield conn

        mock_pool.acquire = mock_acquire

        history = await db_manager.get_reconciliation_history(1, limit=10)
        assert len(history) == 1
        assert history[0]["success"] is True
