"""Tests for the cluster health/status endpoints and the get_leader_lock_info DB method."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth as auth_module
from auth import AuthManager, get_current_user, require_admin
from cluster_status import create_cluster_status_router
from config import LeaderElectionConfig
from db import DatabaseManager
from leader_election import LeaderElection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db() -> tuple:
    """DatabaseManager with a mocked connection pool."""
    db = DatabaseManager(
        host="localhost", port=5432, database="testdb", user="u", password="p"
    )
    mock_conn = AsyncMock()
    mock_pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield mock_conn

    mock_pool.acquire = _acquire
    db.pool = mock_pool
    return db, mock_conn


def _make_le(
    *, is_leader: bool = False, holder_id: str = "test-host:1234:uuid"
) -> MagicMock:
    """Mock LeaderElection with configurable leadership state."""
    le = MagicMock(spec=LeaderElection)
    le.is_leader = is_leader
    le.holder_id = holder_id
    le._config = LeaderElectionConfig(lock_name="test-lock")
    return le


def _make_client(le, db, *, user=None) -> TestClient:
    """Build a TestClient for the cluster status router.

    Pass user=None to exercise real auth (requires a Bearer token).
    Pass a user dict to bypass auth via dependency override.
    """
    app = FastAPI()
    router = create_cluster_status_router(le, db)
    app.include_router(router)
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[require_admin] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _admin() -> dict:
    return {
        "id": 1,
        "username": "admin",
        "is_admin": True,
        "source": "manual",
        "custom_role_id": None,
    }


def _viewer() -> dict:
    return {
        "id": 2,
        "username": "viewer",
        "is_admin": False,
        "source": "manual",
        "custom_role_id": None,
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# DB: get_leader_lock_info
# ---------------------------------------------------------------------------


class TestGetLeaderLockInfo:
    async def test_returns_dict_when_row_exists(self):
        db, conn = _make_db()
        acquired = _now()
        conn.fetchrow = AsyncMock(
            return_value={
                "holder_id": "host:1:uuid",
                "acquired_at": acquired,
                "lease_duration_seconds": 30,
            }
        )

        result = await db.get_leader_lock_info("test-lock")

        assert result["holder_id"] == "host:1:uuid"
        assert result["acquired_at"] == acquired
        assert result["lease_duration_seconds"] == 30

    async def test_returns_none_when_no_row(self):
        db, conn = _make_db()
        conn.fetchrow = AsyncMock(return_value=None)

        result = await db.get_leader_lock_info("test-lock")

        assert result is None


# ---------------------------------------------------------------------------
# HTTP: GET /api/v1/cluster/health — public liveness
# ---------------------------------------------------------------------------


class TestClusterHealth:
    async def test_returns_200_when_db_reachable(self):
        db = AsyncMock(spec=DatabaseManager)
        db.ping = AsyncMock(return_value=True)
        le = _make_le()
        client = _make_client(le, db)

        resp = client.get("/api/v1/cluster/health")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_returns_503_when_db_unreachable(self):
        db = AsyncMock(spec=DatabaseManager)
        db.ping = AsyncMock(return_value=False)
        le = _make_le()
        client = _make_client(le, db)

        resp = client.get("/api/v1/cluster/health")

        assert resp.status_code == 503
        assert resp.json() == {"status": "disconnected"}

    async def test_no_auth_required(self):
        db = AsyncMock(spec=DatabaseManager)
        db.ping = AsyncMock(return_value=True)
        le = _make_le()
        client = _make_client(le, db)  # no dependency override

        resp = client.get("/api/v1/cluster/health")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# HTTP: GET /api/v1/cluster/status — admin only
# ---------------------------------------------------------------------------


class TestClusterStatusAuth:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)

    async def test_returns_401_without_token(self):
        db = AsyncMock(spec=DatabaseManager)
        le = _make_le()
        client = _make_client(le, db)

        resp = client.get("/api/v1/cluster/status")

        assert resp.status_code == 401

    async def test_returns_403_for_non_admin(self):
        db = AsyncMock(spec=DatabaseManager)
        le = _make_le()
        app = FastAPI()
        app.include_router(create_cluster_status_router(le, db))
        # Override only get_current_user so require_admin still runs its admin check
        app.dependency_overrides[get_current_user] = lambda: _viewer()
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/v1/cluster/status")

        assert resp.status_code == 403

    async def test_admin_can_access(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/status")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# HTTP: GET /api/v1/cluster/status — response content
# ---------------------------------------------------------------------------


class TestClusterStatusResponse:
    async def test_leader_instance_returns_is_leader_true(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        le = _make_le(is_leader=True, holder_id="leader-host:1:abc")
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_leader"] is True
        assert data["instance_id"] == "leader-host:1:abc"

    async def test_standby_instance_returns_is_leader_false(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        le = _make_le(is_leader=False, holder_id="standby-host:2:xyz")
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["is_leader"] is False
        assert data["instance_id"] == "standby-host:2:xyz"

    async def test_leader_is_null_when_no_lock_row(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/status")

        assert resp.status_code == 200
        assert resp.json()["leader"] is None

    async def test_leader_info_populated_from_db(self):
        acquired = datetime(2026, 3, 7, 10, 0, 0, tzinfo=timezone.utc)
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(
            return_value={
                "holder_id": "other-host:9:zzz",
                "acquired_at": acquired,
                "lease_duration_seconds": 30,
            }
        )
        le = _make_le(is_leader=False)
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/status")

        assert resp.status_code == 200
        leader = resp.json()["leader"]
        assert leader["holder_id"] == "other-host:9:zzz"
        assert leader["acquired_at"] == "2026-03-07T10:00:00Z"
        assert leader["expires_at"] == "2026-03-07T10:00:30Z"

    async def test_is_valid_true_for_unexpired_lock(self):
        acquired = _now() - timedelta(seconds=5)
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(
            return_value={
                "holder_id": "host:1:abc",
                "acquired_at": acquired,
                "lease_duration_seconds": 30,
            }
        )
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/status")

        assert resp.json()["leader"]["is_valid"] is True

    async def test_is_valid_false_for_expired_lock(self):
        acquired = _now() - timedelta(seconds=60)
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(
            return_value={
                "holder_id": "host:1:abc",
                "acquired_at": acquired,
                "lease_duration_seconds": 30,
            }
        )
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/status")

        assert resp.json()["leader"]["is_valid"] is False

    async def test_naive_datetime_treated_as_utc(self):
        """A naive acquired_at from the DB is treated as UTC without raising."""
        acquired = datetime(2026, 3, 7, 10, 0, 0)  # no tzinfo
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(
            return_value={
                "holder_id": "host:1:abc",
                "acquired_at": acquired,
                "lease_duration_seconds": 30,
            }
        )
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/status")

        assert resp.status_code == 200
        assert resp.json()["leader"]["acquired_at"] == "2026-03-07T10:00:00Z"

    async def test_db_error_returns_200_with_null_leader(self):
        """A DB failure is swallowed; the endpoint still returns the instance state."""
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(side_effect=Exception("db gone"))
        le = _make_le(is_leader=True, holder_id="this-host:1:abc")
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["leader"] is None
        assert data["is_leader"] is True
        assert data["instance_id"] == "this-host:1:abc"

    async def test_different_lock_names_return_different_leader_info(self):
        """Leader info reflects the lock row for this instance's configured lock."""
        acquired = datetime(2026, 3, 7, 10, 0, 0, tzinfo=timezone.utc)
        lock_row = {
            "holder_id": "host:1:abc",
            "acquired_at": acquired,
            "lease_duration_seconds": 30,
        }

        db_with_lock = AsyncMock(spec=DatabaseManager)
        db_with_lock.get_leader_lock_info = AsyncMock(return_value=lock_row)

        db_no_lock = AsyncMock(spec=DatabaseManager)
        db_no_lock.get_leader_lock_info = AsyncMock(return_value=None)

        le = _make_le()
        le._config = LeaderElectionConfig(lock_name="custom-lock-name")

        resp_with = _make_client(le, db_with_lock, user=_admin()).get(
            "/api/v1/cluster/status"
        )
        resp_without = _make_client(le, db_no_lock, user=_admin()).get(
            "/api/v1/cluster/status"
        )

        assert resp_with.json()["leader"]["holder_id"] == "host:1:abc"
        assert resp_without.json()["leader"] is None
