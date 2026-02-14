"""Unit tests for admission webhooks."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager
from datetime import datetime

from admission import (
    AdmissionChain,
    AdmissionError,
    AdmissionRequest,
    apply_patches,
)
from db import DatabaseManager

# ==================== apply_patches tests ====================


class TestApplyPatches:
    """Tests for JSON Patch application."""

    def test_add_new_field(self):
        spec = {"engine": "postgres"}
        patches = [{"op": "add", "path": "/replicas", "value": 3}]
        result = apply_patches(spec, patches)
        assert result == {"engine": "postgres", "replicas": 3}
        # Original unchanged
        assert "replicas" not in spec

    def test_add_with_spec_prefix(self):
        spec = {"engine": "postgres"}
        patches = [{"op": "add", "path": "/spec/replicas", "value": 3}]
        result = apply_patches(spec, patches)
        assert result == {"engine": "postgres", "replicas": 3}

    def test_replace_existing_field(self):
        spec = {"engine": "postgres", "replicas": 1}
        patches = [{"op": "replace", "path": "/replicas", "value": 5}]
        result = apply_patches(spec, patches)
        assert result["replicas"] == 5

    def test_remove_field(self):
        spec = {"engine": "postgres", "debug": True}
        patches = [{"op": "remove", "path": "/debug"}]
        result = apply_patches(spec, patches)
        assert "debug" not in result
        assert result == {"engine": "postgres"}

    def test_nested_add(self):
        spec = {"config": {"enabled": True}}
        patches = [{"op": "add", "path": "/config/timeout", "value": 30}]
        result = apply_patches(spec, patches)
        assert result["config"]["timeout"] == 30

    def test_nested_remove(self):
        spec = {"config": {"enabled": True, "debug": False}}
        patches = [{"op": "remove", "path": "/config/debug"}]
        result = apply_patches(spec, patches)
        assert "debug" not in result["config"]

    def test_multiple_patches(self):
        spec = {"engine": "postgres", "replicas": 1}
        patches = [
            {"op": "replace", "path": "/replicas", "value": 3},
            {"op": "add", "path": "/ha", "value": True},
        ]
        result = apply_patches(spec, patches)
        assert result == {"engine": "postgres", "replicas": 3, "ha": True}

    def test_empty_patches(self):
        spec = {"engine": "postgres"}
        result = apply_patches(spec, [])
        assert result == {"engine": "postgres"}

    def test_unsupported_op_raises(self):
        spec = {"engine": "postgres"}
        patches = [{"op": "move", "path": "/engine", "value": "mysql"}]
        with pytest.raises(AdmissionError, match="Unsupported patch operation"):
            apply_patches(spec, patches)

    def test_remove_nonexistent_path_raises(self):
        spec = {"engine": "postgres"}
        patches = [{"op": "remove", "path": "/nonexistent"}]
        with pytest.raises(AdmissionError, match="Patch path not found"):
            apply_patches(spec, patches)

    def test_invalid_nested_path_raises(self):
        spec = {"engine": "postgres"}
        patches = [{"op": "add", "path": "/config/timeout", "value": 30}]
        with pytest.raises(AdmissionError, match="Patch path not found"):
            apply_patches(spec, patches)

    def test_empty_path_raises(self):
        spec = {"engine": "postgres"}
        patches = [{"op": "add", "path": "/", "value": "bad"}]
        with pytest.raises(AdmissionError, match="Invalid patch path"):
            apply_patches(spec, patches)


# ==================== AdmissionChain tests ====================


@pytest.mark.asyncio
class TestAdmissionChain:
    """Tests for the admission webhook chain."""

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.get_matching_webhooks = AsyncMock(return_value=[])
        return db

    @pytest.fixture
    def chain(self, mock_db):
        return AdmissionChain(mock_db)

    @pytest.fixture
    def base_request(self):
        return AdmissionRequest(
            operation="CREATE",
            resource={
                "name": "test-resource",
                "resource_type_name": "DatabaseCluster",
                "resource_type_version": "v1",
                "spec": {"engine": "postgres", "replicas": 1},
            },
        )

    async def test_no_webhooks_passthrough(self, chain, base_request):
        """When no webhooks registered, spec passes through unchanged."""
        result = await chain.run(base_request)
        assert result == {"engine": "postgres", "replicas": 1}

    async def test_mutating_webhook_applies_patches(self, chain, mock_db, base_request):
        """Mutating webhook patches are applied to the spec."""
        mock_db.get_matching_webhooks.return_value = [
            {
                "id": 1,
                "name": "add-defaults",
                "webhook_url": "http://localhost:9000/mutate",
                "webhook_type": "mutating",
                "operations": ["CREATE"],
                "timeout_seconds": 10,
                "failure_policy": "Fail",
                "ordering": 0,
            }
        ]

        mock_response = {
            "allowed": True,
            "message": "OK",
            "patches": [
                {"op": "add", "path": "/spec/ha", "value": True},
                {"op": "replace", "path": "/spec/replicas", "value": 3},
            ],
        }

        with patch("admission.aiohttp.ClientSession") as mock_session_cls:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=mock_response)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_resp),
                    __aexit__=AsyncMock(return_value=False),
                )
            )

            mock_session_cls.return_value = AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=False),
            )

            result = await chain.run(base_request)

        assert result["ha"] is True
        assert result["replicas"] == 3
        assert result["engine"] == "postgres"

    async def test_validating_webhook_denies(self, chain, mock_db, base_request):
        """Validating webhook denial raises AdmissionError."""
        mock_db.get_matching_webhooks.return_value = [
            {
                "id": 1,
                "name": "policy-check",
                "webhook_url": "http://localhost:9000/validate",
                "webhook_type": "validating",
                "operations": ["CREATE"],
                "timeout_seconds": 10,
                "failure_policy": "Fail",
                "ordering": 0,
            }
        ]

        mock_response = {
            "allowed": False,
            "message": "Replicas must be >= 3",
        }

        with patch("admission.aiohttp.ClientSession") as mock_session_cls:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=mock_response)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_resp),
                    __aexit__=AsyncMock(return_value=False),
                )
            )

            mock_session_cls.return_value = AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=False),
            )

            with pytest.raises(AdmissionError, match="Replicas must be >= 3"):
                await chain.run(base_request)

    async def test_validating_webhook_allows(self, chain, mock_db, base_request):
        """Validating webhook that allows passes through."""
        mock_db.get_matching_webhooks.return_value = [
            {
                "id": 1,
                "name": "policy-check",
                "webhook_url": "http://localhost:9000/validate",
                "webhook_type": "validating",
                "operations": ["CREATE"],
                "timeout_seconds": 10,
                "failure_policy": "Fail",
                "ordering": 0,
            }
        ]

        mock_response = {"allowed": True, "message": "OK"}

        with patch("admission.aiohttp.ClientSession") as mock_session_cls:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=mock_response)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_resp),
                    __aexit__=AsyncMock(return_value=False),
                )
            )

            mock_session_cls.return_value = AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=False),
            )

            result = await chain.run(base_request)

        assert result == {"engine": "postgres", "replicas": 1}

    async def test_failure_policy_ignore(self, chain, mock_db, base_request):
        """Failure policy 'Ignore' allows request when webhook fails."""
        mock_db.get_matching_webhooks.return_value = [
            {
                "id": 1,
                "name": "flaky-webhook",
                "webhook_url": "http://localhost:9000/validate",
                "webhook_type": "validating",
                "operations": ["CREATE"],
                "timeout_seconds": 1,
                "failure_policy": "Ignore",
                "ordering": 0,
            }
        ]

        with patch("admission.aiohttp.ClientSession") as mock_session_cls:
            mock_session_cls.return_value = AsyncMock(
                __aenter__=AsyncMock(side_effect=Exception("Connection refused")),
                __aexit__=AsyncMock(return_value=False),
            )

            result = await chain.run(base_request)

        assert result == {"engine": "postgres", "replicas": 1}

    async def test_failure_policy_fail(self, chain, mock_db, base_request):
        """Failure policy 'Fail' denies request when webhook fails."""
        mock_db.get_matching_webhooks.return_value = [
            {
                "id": 1,
                "name": "strict-webhook",
                "webhook_url": "http://localhost:9000/validate",
                "webhook_type": "validating",
                "operations": ["CREATE"],
                "timeout_seconds": 1,
                "failure_policy": "Fail",
                "ordering": 0,
            }
        ]

        with patch("admission.aiohttp.ClientSession") as mock_session_cls:
            mock_session_cls.return_value = AsyncMock(
                __aenter__=AsyncMock(side_effect=Exception("Connection refused")),
                __aexit__=AsyncMock(return_value=False),
            )

            with pytest.raises(AdmissionError, match="failed"):
                await chain.run(base_request)

    async def test_multiple_mutating_webhooks_accumulate(
        self, chain, mock_db, base_request
    ):
        """Multiple mutating webhooks accumulate patches in order."""
        mock_db.get_matching_webhooks.return_value = [
            {
                "id": 1,
                "name": "mutate-1",
                "webhook_url": "http://localhost:9001/mutate",
                "webhook_type": "mutating",
                "operations": ["CREATE"],
                "timeout_seconds": 10,
                "failure_policy": "Fail",
                "ordering": 0,
            },
            {
                "id": 2,
                "name": "mutate-2",
                "webhook_url": "http://localhost:9002/mutate",
                "webhook_type": "mutating",
                "operations": ["CREATE"],
                "timeout_seconds": 10,
                "failure_policy": "Fail",
                "ordering": 1,
            },
        ]

        responses = [
            {
                "allowed": True,
                "patches": [{"op": "add", "path": "/spec/ha", "value": True}],
            },
            {
                "allowed": True,
                "patches": [{"op": "replace", "path": "/spec/replicas", "value": 5}],
            },
        ]
        call_count = 0

        with patch("admission.aiohttp.ClientSession") as mock_session_cls:

            async def make_response(*args, **kwargs):
                nonlocal call_count
                resp = AsyncMock()
                resp.status = 200
                resp.json = AsyncMock(return_value=responses[call_count])
                call_count += 1
                return resp

            mock_session = AsyncMock()
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = make_response
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_session.post = MagicMock(return_value=mock_cm)

            mock_session_cls.return_value = AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=False),
            )

            result = await chain.run(base_request)

        assert result["ha"] is True
        assert result["replicas"] == 5
        assert result["engine"] == "postgres"

    async def test_mutating_then_validating_order(self, chain, mock_db, base_request):
        """Mutating webhooks run before validating webhooks."""
        call_order = []

        mock_db.get_matching_webhooks.return_value = [
            {
                "id": 1,
                "name": "mutator",
                "webhook_url": "http://localhost:9001/mutate",
                "webhook_type": "mutating",
                "operations": ["CREATE"],
                "timeout_seconds": 10,
                "failure_policy": "Fail",
                "ordering": 0,
            },
            {
                "id": 2,
                "name": "validator",
                "webhook_url": "http://localhost:9002/validate",
                "webhook_type": "validating",
                "operations": ["CREATE"],
                "timeout_seconds": 10,
                "failure_policy": "Fail",
                "ordering": 0,
            },
        ]

        with patch("admission.aiohttp.ClientSession") as mock_session_cls:

            def make_post(url, **kwargs):
                if "mutate" in str(url):
                    call_order.append("mutating")
                else:
                    call_order.append("validating")
                resp = AsyncMock()
                resp.status = 200
                resp.json = AsyncMock(return_value={"allowed": True, "patches": []})
                cm = AsyncMock()
                cm.__aenter__ = AsyncMock(return_value=resp)
                cm.__aexit__ = AsyncMock(return_value=False)
                return cm

            mock_session = AsyncMock()
            mock_session.post = make_post

            mock_session_cls.return_value = AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=False),
            )

            await chain.run(base_request)

        assert call_order == ["mutating", "validating"]

    async def test_mutating_denial_stops_chain(self, chain, mock_db, base_request):
        """If a mutating webhook denies, the chain stops."""
        mock_db.get_matching_webhooks.return_value = [
            {
                "id": 1,
                "name": "deny-mutator",
                "webhook_url": "http://localhost:9001/mutate",
                "webhook_type": "mutating",
                "operations": ["CREATE"],
                "timeout_seconds": 10,
                "failure_policy": "Fail",
                "ordering": 0,
            },
        ]

        mock_response = {"allowed": False, "message": "Not allowed"}

        with patch("admission.aiohttp.ClientSession") as mock_session_cls:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=mock_response)

            mock_session = AsyncMock()
            mock_session.post = MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=mock_resp),
                    __aexit__=AsyncMock(return_value=False),
                )
            )

            mock_session_cls.return_value = AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=False),
            )

            with pytest.raises(AdmissionError, match="Not allowed"):
                await chain.run(base_request)

    async def test_update_request_includes_old_resource(self, chain, mock_db):
        """UPDATE requests include old_resource in the webhook call."""
        old_resource = {
            "name": "test-resource",
            "resource_type_name": "DatabaseCluster",
            "resource_type_version": "v1",
            "spec": {"engine": "postgres", "replicas": 1},
        }
        request = AdmissionRequest(
            operation="UPDATE",
            resource={
                "name": "test-resource",
                "resource_type_name": "DatabaseCluster",
                "resource_type_version": "v1",
                "spec": {"engine": "postgres", "replicas": 3},
            },
            old_resource=old_resource,
        )

        mock_db.get_matching_webhooks.return_value = [
            {
                "id": 1,
                "name": "validator",
                "webhook_url": "http://localhost:9000/validate",
                "webhook_type": "validating",
                "operations": ["UPDATE"],
                "timeout_seconds": 10,
                "failure_policy": "Fail",
                "ordering": 0,
            }
        ]

        captured_payload = {}

        with patch("admission.aiohttp.ClientSession") as mock_session_cls:

            def make_post(url, **kwargs):
                captured_payload.update(kwargs.get("json", {}))
                resp = AsyncMock()
                resp.status = 200
                resp.json = AsyncMock(return_value={"allowed": True})
                cm = AsyncMock()
                cm.__aenter__ = AsyncMock(return_value=resp)
                cm.__aexit__ = AsyncMock(return_value=False)
                return cm

            mock_session = AsyncMock()
            mock_session.post = make_post

            mock_session_cls.return_value = AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=False),
            )

            result = await chain.run(request)

        assert result == {"engine": "postgres", "replicas": 3}
        assert captured_payload.get("old_resource") == old_resource
        assert captured_payload.get("operation") == "UPDATE"


# ==================== Admission Webhook DB tests ====================


@pytest.mark.asyncio
class TestAdmissionWebhookDB:
    """Tests for admission webhook database operations."""

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

    async def test_create_admission_webhook(self, db_manager, mock_pool):
        db_manager.pool = mock_pool
        captured = {}

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()

            async def capture_fetchval(query, *args):
                captured["args"] = args
                return 1

            conn.fetchval = capture_fetchval
            yield conn

        mock_pool.acquire = mock_acquire

        wh_id = await db_manager.create_admission_webhook(
            name="test-webhook",
            webhook_url="http://localhost:9000/validate",
            webhook_type="validating",
            operations=["CREATE", "UPDATE"],
            timeout_seconds=5,
            failure_policy="Fail",
            ordering=0,
        )

        assert wh_id == 1
        assert captured["args"][0] == "test-webhook"
        assert captured["args"][1] == "http://localhost:9000/validate"
        assert captured["args"][2] == "validating"

    async def test_get_admission_webhook(self, db_manager, mock_pool):
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

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(
                return_value=MockRecord(
                    {
                        "id": 1,
                        "name": "test-webhook",
                        "webhook_url": "http://localhost:9000/validate",
                        "webhook_type": "validating",
                        "operations": '["CREATE", "UPDATE"]',
                        "resource_type_name": None,
                        "resource_type_version": None,
                        "timeout_seconds": 10,
                        "failure_policy": "Fail",
                        "ordering": 0,
                        "created_at": datetime.now(),
                        "updated_at": datetime.now(),
                    }
                )
            )
            yield conn

        mock_pool.acquire = mock_acquire

        webhook = await db_manager.get_admission_webhook(1)
        assert webhook is not None
        assert webhook["name"] == "test-webhook"
        assert webhook["operations"] == ["CREATE", "UPDATE"]

    async def test_get_admission_webhook_not_found(self, db_manager, mock_pool):
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchrow = AsyncMock(return_value=None)
            yield conn

        mock_pool.acquire = mock_acquire

        webhook = await db_manager.get_admission_webhook(999)
        assert webhook is None

    async def test_delete_admission_webhook(self, db_manager, mock_pool):
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=1)
            yield conn

        mock_pool.acquire = mock_acquire

        result = await db_manager.delete_admission_webhook(1)
        assert result is True

    async def test_delete_admission_webhook_not_found(self, db_manager, mock_pool):
        db_manager.pool = mock_pool

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetchval = AsyncMock(return_value=None)
            yield conn

        mock_pool.acquire = mock_acquire

        result = await db_manager.delete_admission_webhook(999)
        assert result is False

    async def test_list_admission_webhooks(self, db_manager, mock_pool):
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

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetch = AsyncMock(
                return_value=[
                    MockRecord(
                        {
                            "id": 1,
                            "name": "wh-1",
                            "webhook_url": "http://localhost:9000",
                            "webhook_type": "mutating",
                            "operations": '["CREATE"]',
                            "resource_type_name": None,
                            "resource_type_version": None,
                            "timeout_seconds": 10,
                            "failure_policy": "Fail",
                            "ordering": 0,
                            "created_at": datetime.now(),
                            "updated_at": datetime.now(),
                        }
                    ),
                    MockRecord(
                        {
                            "id": 2,
                            "name": "wh-2",
                            "webhook_url": "http://localhost:9001",
                            "webhook_type": "validating",
                            "operations": '["CREATE", "UPDATE"]',
                            "resource_type_name": "DatabaseCluster",
                            "resource_type_version": "v1",
                            "timeout_seconds": 5,
                            "failure_policy": "Ignore",
                            "ordering": 1,
                            "created_at": datetime.now(),
                            "updated_at": datetime.now(),
                        }
                    ),
                ]
            )
            yield conn

        mock_pool.acquire = mock_acquire

        webhooks = await db_manager.list_admission_webhooks()
        assert len(webhooks) == 2
        assert webhooks[0]["name"] == "wh-1"
        assert webhooks[1]["operations"] == ["CREATE", "UPDATE"]

    async def test_update_admission_webhook(self, db_manager, mock_pool):
        db_manager.pool = mock_pool
        captured_query = {}

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()

            async def capture_execute(query, *args):
                captured_query["query"] = query
                captured_query["args"] = args

            conn.execute = capture_execute
            yield conn

        mock_pool.acquire = mock_acquire

        await db_manager.update_admission_webhook(
            webhook_id=1,
            webhook_url="http://localhost:9999/new",
            failure_policy="Ignore",
        )

        assert "webhook_url" in captured_query["query"]
        assert "failure_policy" in captured_query["query"]

    async def test_update_admission_webhook_no_changes(self, db_manager, mock_pool):
        """No-op when no fields are provided."""
        db_manager.pool = mock_pool
        executed = False

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()

            async def track_execute(query, *args):
                nonlocal executed
                executed = True

            conn.execute = track_execute
            yield conn

        mock_pool.acquire = mock_acquire

        await db_manager.update_admission_webhook(webhook_id=1)
        assert executed is False

    async def test_get_matching_webhooks(self, db_manager, mock_pool):
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

        @asynccontextmanager
        async def mock_acquire():
            conn = AsyncMock()
            conn.fetch = AsyncMock(
                return_value=[
                    MockRecord(
                        {
                            "id": 1,
                            "name": "global-mutator",
                            "webhook_url": "http://localhost:9000",
                            "webhook_type": "mutating",
                            "operations": '["CREATE"]',
                            "resource_type_name": None,
                            "resource_type_version": None,
                            "timeout_seconds": 10,
                            "failure_policy": "Fail",
                            "ordering": 0,
                            "created_at": datetime.now(),
                            "updated_at": datetime.now(),
                        }
                    )
                ]
            )
            yield conn

        mock_pool.acquire = mock_acquire

        webhooks = await db_manager.get_matching_webhooks(
            resource_type_name="DatabaseCluster",
            resource_type_version="v1",
            operation="CREATE",
        )
        assert len(webhooks) == 1
        assert webhooks[0]["name"] == "global-mutator"

    def test_parse_webhook_row_string_operations(self, db_manager):
        """Test that string operations are parsed as JSON."""

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

        row = MockRecord(
            {
                "id": 1,
                "name": "test",
                "operations": '["CREATE", "DELETE"]',
            }
        )
        result = db_manager._parse_webhook_row(row)
        assert result["operations"] == ["CREATE", "DELETE"]

    def test_parse_webhook_row_list_operations(self, db_manager):
        """Test that list operations pass through."""

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

        row = MockRecord(
            {
                "id": 1,
                "name": "test",
                "operations": ["CREATE", "DELETE"],
            }
        )
        result = db_manager._parse_webhook_row(row)
        assert result["operations"] == ["CREATE", "DELETE"]
