"""Tests for the cluster health/nodes endpoints and related DB methods."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth as auth_module
from auth import AuthManager, get_current_user, require_admin
from cluster_status import _format_age, create_cluster_status_router
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


def _node_row(
    *,
    node_id: str = "host-a:1:aaa",
    hostname: str = "host-a",
    pid: str = "1",
    first_seen: Optional[datetime] = None,
    last_heartbeat: Optional[datetime] = None,
    lease_duration_seconds: int = 60,
) -> dict:
    now = _now()
    return {
        "node_id": node_id,
        "hostname": hostname,
        "pid": pid,
        "first_seen": first_seen or now,
        "last_heartbeat": last_heartbeat or now,
        "lease_duration_seconds": lease_duration_seconds,
    }


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
# _format_age helper
# ---------------------------------------------------------------------------


class TestFormatAge:
    def test_seconds_only(self):
        assert _format_age(timedelta(seconds=45)) == "45s"

    def test_minutes_and_seconds(self):
        assert _format_age(timedelta(minutes=3, seconds=20)) == "3m20s"

    def test_minutes_no_seconds(self):
        assert _format_age(timedelta(minutes=10)) == "10m"

    def test_hours_and_minutes(self):
        assert _format_age(timedelta(hours=2, minutes=30)) == "2h30m"

    def test_hours_no_minutes(self):
        assert _format_age(timedelta(hours=5)) == "5h"

    def test_days_and_hours(self):
        assert _format_age(timedelta(days=3, hours=4)) == "3d4h"

    def test_days_no_hours(self):
        assert _format_age(timedelta(days=7)) == "7d"

    def test_negative_returns_zero(self):
        assert _format_age(timedelta(seconds=-10)) == "0s"


# ---------------------------------------------------------------------------
# DB: register_node / get_cluster_nodes / deregister_node
# ---------------------------------------------------------------------------


class TestRegisterNode:
    async def test_execute_called_with_correct_params(self):
        db, conn = _make_db()
        conn.execute = AsyncMock(return_value=None)

        await db.register_node(
            node_id="host:1234:uuid",
            hostname="host",
            pid="1234",
            lease_duration_seconds=60,
        )

        conn.execute.assert_called_once()
        call_args = conn.execute.call_args[0]
        assert "INSERT INTO cluster_nodes" in call_args[0]
        assert call_args[1] == "host:1234:uuid"
        assert call_args[2] == "host"
        assert call_args[3] == "1234"
        assert call_args[4] == 60


class TestGetClusterNodes:
    async def test_returns_list_of_dicts(self):
        db, conn = _make_db()
        now = _now()
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "node_id": "host-a:1:aaa",
                    "hostname": "host-a",
                    "pid": "1",
                    "first_seen": now,
                    "last_heartbeat": now,
                    "lease_duration_seconds": 60,
                }
            ]
        )

        result = await db.get_cluster_nodes()

        assert len(result) == 1
        assert result[0]["node_id"] == "host-a:1:aaa"
        assert result[0]["hostname"] == "host-a"

    async def test_returns_empty_list_when_no_nodes(self):
        db, conn = _make_db()
        conn.fetch = AsyncMock(return_value=[])

        result = await db.get_cluster_nodes()

        assert result == []


class TestDeregisterNode:
    async def test_execute_called_with_node_id(self):
        db, conn = _make_db()
        conn.execute = AsyncMock(return_value=None)

        await db.deregister_node("host:1234:uuid")

        conn.execute.assert_called_once()
        call_args = conn.execute.call_args[0]
        assert "DELETE FROM cluster_nodes" in call_args[0]
        assert call_args[1] == "host:1234:uuid"


# ---------------------------------------------------------------------------
# HTTP: GET /api/v1/cluster/nodes — auth
# ---------------------------------------------------------------------------


class TestClusterNodesAuth:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)

    async def test_returns_401_without_token(self):
        db = AsyncMock(spec=DatabaseManager)
        le = _make_le()
        client = _make_client(le, db)

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.status_code == 401

    async def test_returns_403_for_non_admin(self):
        db = AsyncMock(spec=DatabaseManager)
        le = _make_le()
        app = FastAPI()
        app.include_router(create_cluster_status_router(le, db))
        app.dependency_overrides[get_current_user] = lambda: _viewer()
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.status_code == 403

    async def test_admin_can_access(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        db.get_cluster_nodes = AsyncMock(return_value=[])
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# HTTP: GET /api/v1/cluster/nodes — this_instance fields
# ---------------------------------------------------------------------------


class TestClusterNodesThisInstance:
    async def test_this_instance_id_reflects_leader_election_holder(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        db.get_cluster_nodes = AsyncMock(return_value=[])
        le = _make_le(holder_id="myhost:99:zzz")
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.json()["this_instance_id"] == "myhost:99:zzz"

    async def test_this_instance_is_leader_true_when_leading(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        db.get_cluster_nodes = AsyncMock(return_value=[])
        le = _make_le(is_leader=True)
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.json()["this_instance_is_leader"] is True

    async def test_this_instance_is_leader_false_when_follower(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        db.get_cluster_nodes = AsyncMock(return_value=[])
        le = _make_le(is_leader=False)
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.json()["this_instance_is_leader"] is False


# ---------------------------------------------------------------------------
# HTTP: GET /api/v1/cluster/nodes — leader_lock field
# ---------------------------------------------------------------------------


class TestClusterNodesLeaderLock:
    async def test_leader_lock_null_when_no_lock_row(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        db.get_cluster_nodes = AsyncMock(return_value=[])
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.json()["leader_lock"] is None

    async def test_leader_lock_populated_from_db(self):
        acquired = datetime(2026, 3, 7, 10, 0, 0, tzinfo=timezone.utc)
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(
            return_value={
                "holder_id": "other-host:9:zzz",
                "acquired_at": acquired,
                "lease_duration_seconds": 30,
            }
        )
        db.get_cluster_nodes = AsyncMock(return_value=[])
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        lock = resp.json()["leader_lock"]
        assert lock["holder_id"] == "other-host:9:zzz"
        assert lock["acquired_at"] == "2026-03-07T10:00:00Z"
        assert lock["expires_at"] == "2026-03-07T10:00:30Z"

    async def test_leader_lock_is_valid_true_for_unexpired_lock(self):
        acquired = _now() - timedelta(seconds=5)
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(
            return_value={
                "holder_id": "host:1:abc",
                "acquired_at": acquired,
                "lease_duration_seconds": 30,
            }
        )
        db.get_cluster_nodes = AsyncMock(return_value=[])
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.json()["leader_lock"]["is_valid"] is True

    async def test_leader_lock_is_valid_false_for_expired_lock(self):
        acquired = _now() - timedelta(seconds=60)
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(
            return_value={
                "holder_id": "host:1:abc",
                "acquired_at": acquired,
                "lease_duration_seconds": 30,
            }
        )
        db.get_cluster_nodes = AsyncMock(return_value=[])
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.json()["leader_lock"]["is_valid"] is False

    async def test_naive_datetime_in_lock_treated_as_utc(self):
        acquired = datetime(2026, 3, 7, 10, 0, 0)  # no tzinfo
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(
            return_value={
                "holder_id": "host:1:abc",
                "acquired_at": acquired,
                "lease_duration_seconds": 30,
            }
        )
        db.get_cluster_nodes = AsyncMock(return_value=[])
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.status_code == 200
        assert resp.json()["leader_lock"]["acquired_at"] == "2026-03-07T10:00:00Z"

    async def test_lock_db_error_returns_null_leader_lock(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(side_effect=Exception("db gone"))
        db.get_cluster_nodes = AsyncMock(return_value=[])
        le = _make_le(is_leader=True, holder_id="this-host:1:abc")
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.status_code == 200
        assert resp.json()["leader_lock"] is None
        assert resp.json()["this_instance_is_leader"] is True
        assert resp.json()["this_instance_id"] == "this-host:1:abc"


# ---------------------------------------------------------------------------
# HTTP: GET /api/v1/cluster/nodes — node list content
# ---------------------------------------------------------------------------


class TestClusterNodesContent:
    async def test_empty_nodes_list(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        db.get_cluster_nodes = AsyncMock(return_value=[])
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.status_code == 200
        assert resp.json()["nodes"] == []

    async def test_ready_node_when_heartbeat_fresh(self):
        now = _now()
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        db.get_cluster_nodes = AsyncMock(
            return_value=[_node_row(last_heartbeat=now, lease_duration_seconds=60)]
        )
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.json()["nodes"][0]["status"] == "Ready"

    async def test_not_ready_node_when_heartbeat_expired(self):
        stale = _now() - timedelta(seconds=120)
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        db.get_cluster_nodes = AsyncMock(
            return_value=[_node_row(last_heartbeat=stale, lease_duration_seconds=60)]
        )
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.json()["nodes"][0]["status"] == "NotReady"

    async def test_leader_role_assigned_to_lock_holder(self):
        now = _now()
        acquired = now - timedelta(seconds=5)
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(
            return_value={
                "holder_id": "host-a:1:aaa",
                "acquired_at": acquired,
                "lease_duration_seconds": 60,
            }
        )
        db.get_cluster_nodes = AsyncMock(
            return_value=[
                _node_row(node_id="host-a:1:aaa", hostname="host-a"),
                _node_row(node_id="host-b:2:bbb", hostname="host-b"),
            ]
        )
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        nodes = {n["node_id"]: n for n in resp.json()["nodes"]}
        assert nodes["host-a:1:aaa"]["role"] == "leader"
        assert nodes["host-b:2:bbb"]["role"] == "follower"

    async def test_expired_leader_lock_gives_all_follower(self):
        stale_acquired = _now() - timedelta(seconds=120)
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(
            return_value={
                "holder_id": "host-a:1:aaa",
                "acquired_at": stale_acquired,
                "lease_duration_seconds": 30,
            }
        )
        db.get_cluster_nodes = AsyncMock(
            return_value=[_node_row(node_id="host-a:1:aaa")]
        )
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.json()["nodes"][0]["role"] == "follower"

    async def test_node_fields_present(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        db.get_cluster_nodes = AsyncMock(
            return_value=[
                _node_row(node_id="myhost:42:uuid", hostname="myhost", pid="42")
            ]
        )
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        node = resp.json()["nodes"][0]
        assert node["node_id"] == "myhost:42:uuid"
        assert node["hostname"] == "myhost"
        assert node["pid"] == "42"
        assert "age" in node
        assert "first_seen" in node
        assert "last_heartbeat" in node

    async def test_nodes_db_error_returns_empty_list(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        db.get_cluster_nodes = AsyncMock(side_effect=Exception("db gone"))
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.status_code == 200
        assert resp.json()["nodes"] == []

    async def test_naive_datetime_in_node_treated_as_utc(self):
        """Naive datetimes from the DB are handled without raising."""
        naive_ts = datetime(2026, 3, 1, 12, 0, 0)  # no tzinfo
        db = AsyncMock(spec=DatabaseManager)
        db.get_leader_lock_info = AsyncMock(return_value=None)
        db.get_cluster_nodes = AsyncMock(
            return_value=[_node_row(first_seen=naive_ts, last_heartbeat=naive_ts)]
        )
        le = _make_le()
        client = _make_client(le, db, user=_admin())

        resp = client.get("/api/v1/cluster/nodes")

        assert resp.status_code == 200
