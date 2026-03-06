"""Tests for leader election — DatabaseManager lock methods and LeaderElection."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from config import LeaderElectionConfig
from db import DatabaseManager
from leader_election import LeaderElection, _default_holder_id

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db() -> DatabaseManager:
    """Return a DatabaseManager with a mocked connection pool."""
    db = DatabaseManager(
        host="localhost",
        port=5432,
        database="testdb",
        user="testuser",
        password="testpass",
    )
    mock_conn = AsyncMock()
    mock_pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield mock_conn

    mock_pool.acquire = _acquire
    db.pool = mock_pool
    return db, mock_conn


def _make_config(**kwargs) -> LeaderElectionConfig:
    defaults = dict(
        lock_name="test-lock",
        holder_id="test-holder",
        lease_duration_seconds=30,
        renew_interval_seconds=0,  # no sleep in tests
        retry_interval_seconds=0,
    )
    defaults.update(kwargs)
    return LeaderElectionConfig(**defaults)


# ---------------------------------------------------------------------------
# DatabaseManager lock methods
# ---------------------------------------------------------------------------


class TestAcquireOrRenewLock:
    async def test_acquire_new_lock(self):
        db, conn = _make_db()
        conn.fetchrow = AsyncMock(return_value={"resource_key": "test-lock"})

        result = await db.acquire_or_renew_lock("test-lock", "holder-1", 30)

        assert result is True
        conn.fetchrow.assert_called_once()
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO locks" in args[0]

    async def test_renew_existing_lock(self):
        db, conn = _make_db()
        conn.fetchrow = AsyncMock(return_value={"resource_key": "test-lock"})

        result = await db.acquire_or_renew_lock("test-lock", "holder-1", 30)

        assert result is True

    async def test_returns_false_when_another_holder(self):
        db, conn = _make_db()
        conn.fetchrow = AsyncMock(return_value=None)

        result = await db.acquire_or_renew_lock("test-lock", "holder-2", 30)

        assert result is False

    async def test_passes_correct_parameters(self):
        db, conn = _make_db()
        conn.fetchrow = AsyncMock(return_value={"resource_key": "my-lock"})

        await db.acquire_or_renew_lock("my-lock", "my-holder", 60)

        call_args = conn.fetchrow.call_args[0]
        assert "my-lock" in call_args
        assert "my-holder" in call_args
        assert 60 in call_args


class TestReleaseLock:
    async def test_release_held_lock(self):
        db, conn = _make_db()
        conn.execute = AsyncMock(return_value="DELETE 1")

        result = await db.release_lock("test-lock", "holder-1")

        assert result is True
        conn.execute.assert_called_once()
        args = conn.execute.call_args[0]
        assert "DELETE FROM locks" in args[0]

    async def test_returns_false_when_not_held(self):
        db, conn = _make_db()
        conn.execute = AsyncMock(return_value="DELETE 0")

        result = await db.release_lock("test-lock", "holder-2")

        assert result is False

    async def test_passes_correct_parameters(self):
        db, conn = _make_db()
        conn.execute = AsyncMock(return_value="DELETE 1")

        await db.release_lock("my-lock", "my-holder")

        call_args = conn.execute.call_args[0]
        assert "my-lock" in call_args
        assert "my-holder" in call_args


# ---------------------------------------------------------------------------
# LeaderElection
# ---------------------------------------------------------------------------


class TestDefaultHolderId:
    def test_contains_hostname_and_pid(self):
        import os
        import socket

        holder = _default_holder_id()
        parts = holder.split(":")
        assert parts[0] == socket.gethostname()
        assert parts[1] == str(os.getpid())
        assert len(parts) == 3  # hostname:pid:uuid


class TestLeaderElectionProperties:
    def test_is_leader_defaults_false(self):
        db, _ = _make_db()
        le = LeaderElection(db=db, config=_make_config())
        assert le.is_leader is False

    def test_holder_id_from_config(self):
        db, _ = _make_db()
        le = LeaderElection(db=db, config=_make_config(holder_id="explicit"))
        assert le.holder_id == "explicit"

    def test_holder_id_auto_generated_when_empty(self):
        db, _ = _make_db()
        le = LeaderElection(db=db, config=_make_config(holder_id=""))
        assert le.holder_id != ""
        assert ":" in le.holder_id


class TestLeaderElectionBecomeLeader:
    async def test_becomes_leader_and_calls_on_started_leading(self):
        db, _ = _make_db()
        db.acquire_or_renew_lock = AsyncMock(return_value=True)
        db.release_lock = AsyncMock(return_value=True)

        cfg = _make_config(renew_interval_seconds=0, retry_interval_seconds=0)
        le = LeaderElection(db=db, config=cfg)

        started = asyncio.Event()

        async def on_started():
            started.set()
            # Block until cancelled
            await asyncio.sleep(9999)

        async def on_stopped():
            pass

        async def _run():
            await le.run(on_started_leading=on_started, on_stopped_leading=on_stopped)

        task = asyncio.create_task(_run())
        await asyncio.wait_for(started.wait(), timeout=2)
        assert le.is_leader is True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestLeaderElectionLosesLease:
    async def test_loses_leadership_when_lock_not_renewed(self):
        db, _ = _make_db()
        # First call acquires, second call fails (lost lease)
        db.acquire_or_renew_lock = AsyncMock(side_effect=[True, False])
        db.release_lock = AsyncMock(return_value=True)

        cfg = _make_config(renew_interval_seconds=0, retry_interval_seconds=0)
        le = LeaderElection(db=db, config=cfg)

        stopped = asyncio.Event()

        async def on_started():
            await asyncio.sleep(9999)

        async def on_stopped():
            stopped.set()

        async def _run():
            await le.run(on_started_leading=on_started, on_stopped_leading=on_stopped)

        task = asyncio.create_task(_run())
        await asyncio.wait_for(stopped.wait(), timeout=2)
        assert le.is_leader is False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestLeaderElectionStop:
    async def test_stop_releases_lock(self):
        db, _ = _make_db()
        db.acquire_or_renew_lock = AsyncMock(return_value=True)
        db.release_lock = AsyncMock(return_value=True)

        cfg = _make_config(renew_interval_seconds=0)
        le = LeaderElection(db=db, config=cfg)

        started = asyncio.Event()

        async def on_started():
            started.set()
            await asyncio.sleep(9999)

        async def on_stopped():
            pass

        task = asyncio.create_task(
            le.run(on_started_leading=on_started, on_stopped_leading=on_stopped)
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        await le.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        db.release_lock.assert_called_once_with(
            resource_key=cfg.lock_name,
            holder_id=le.holder_id,
        )
        assert le.is_leader is False

    async def test_stop_when_not_leader_does_not_release(self):
        db, _ = _make_db()
        db.acquire_or_renew_lock = AsyncMock(return_value=False)
        db.release_lock = AsyncMock(return_value=False)

        cfg = _make_config(retry_interval_seconds=9999)
        le = LeaderElection(db=db, config=cfg)

        async def on_started():
            pass

        async def on_stopped():
            pass

        task = asyncio.create_task(
            le.run(on_started_leading=on_started, on_stopped_leading=on_stopped)
        )
        # Give the loop one iteration to attempt and fail
        await asyncio.sleep(0)
        await le.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        db.release_lock.assert_not_called()


class TestNetworkPartitionFailures:
    async def test_db_error_during_acquire_does_not_crash_loop(self):
        """A DB exception while not leader is caught; the loop retries."""
        db, _ = _make_db()
        # First two calls raise, then succeed indefinitely
        db.acquire_or_renew_lock = AsyncMock(
            side_effect=[ConnectionError("partition"), ConnectionError("partition")]
            + [True] * 20
        )
        db.release_lock = AsyncMock(return_value=True)

        cfg = _make_config(retry_interval_seconds=0, renew_interval_seconds=0)
        le = LeaderElection(db=db, config=cfg)

        started = asyncio.Event()

        async def on_started():
            started.set()
            await asyncio.sleep(9999)

        async def on_stopped():
            pass

        task = asyncio.create_task(
            le.run(on_started_leading=on_started, on_stopped_leading=on_stopped)
        )
        # started firing proves leadership was gained after the errors
        await asyncio.wait_for(started.wait(), timeout=2)
        assert db.acquire_or_renew_lock.call_count >= 3
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_db_error_during_renewal_loses_leadership(self):
        """A DB exception while renewing causes leadership to be lost."""
        db, _ = _make_db()
        # Acquire succeeds, then renewal raises; stopped event proves the callback ran
        db.acquire_or_renew_lock = AsyncMock(
            side_effect=[True, ConnectionError("partition")] + [False] * 20
        )
        db.release_lock = AsyncMock(return_value=True)

        cfg = _make_config(retry_interval_seconds=0, renew_interval_seconds=0)
        le = LeaderElection(db=db, config=cfg)

        started = asyncio.Event()
        stopped = asyncio.Event()

        async def on_started():
            started.set()
            await asyncio.sleep(9999)

        async def on_stopped():
            stopped.set()

        task = asyncio.create_task(
            le.run(on_started_leading=on_started, on_stopped_leading=on_stopped)
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.wait_for(stopped.wait(), timeout=2)
        assert le.is_leader is False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_db_error_during_release_on_stop_is_swallowed(self):
        """A DB exception in release_lock during stop() does not propagate."""
        db, _ = _make_db()
        db.acquire_or_renew_lock = AsyncMock(return_value=True)
        db.release_lock = AsyncMock(side_effect=ConnectionError("partition"))

        cfg = _make_config(renew_interval_seconds=0)
        le = LeaderElection(db=db, config=cfg)

        started = asyncio.Event()

        async def on_started():
            started.set()
            await asyncio.sleep(9999)

        async def on_stopped():
            pass

        task = asyncio.create_task(
            le.run(on_started_leading=on_started, on_stopped_leading=on_stopped)
        )
        await asyncio.wait_for(started.wait(), timeout=2)

        # Should not raise even though release_lock throws
        await le.stop()

        assert le.is_leader is False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestLeaderElectionRetry:
    async def test_non_leader_retries(self):
        db, _ = _make_db()
        # Fail twice then succeed indefinitely
        db.acquire_or_renew_lock = AsyncMock(
            side_effect=[False, False, True] + [True] * 20
        )
        db.release_lock = AsyncMock(return_value=True)

        cfg = _make_config(retry_interval_seconds=0, renew_interval_seconds=0)
        le = LeaderElection(db=db, config=cfg)

        started = asyncio.Event()

        async def on_started():
            started.set()
            await asyncio.sleep(9999)

        async def on_stopped():
            pass

        task = asyncio.create_task(
            le.run(on_started_leading=on_started, on_stopped_leading=on_stopped)
        )
        # started firing proves leadership was eventually gained after retries
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
