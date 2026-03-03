"""Unit tests for user CRUD methods in DatabaseManager."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from db import DatabaseManager


def make_db() -> DatabaseManager:
    db = DatabaseManager(
        host="localhost",
        port=5432,
        database="testdb",
        user="testuser",
        password="testpass",
    )
    db.pool = AsyncMock()
    return db


def mock_acquire(row):
    """Return an asynccontextmanager that yields a mock connection
    whose fetchrow() returns *row*."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetch = AsyncMock(return_value=[row] if row else [])
    conn.execute = AsyncMock(return_value="UPDATE 1")

    @asynccontextmanager
    async def _acquire():
        yield conn

    return _acquire, conn


def _row(**kwargs):
    """Build a minimal fake asyncpg Record-like dict for users."""
    defaults = {
        "id": 1,
        "username": "testuser",
        "email": None,
        "display_name": None,
        "source": "manual",
        "is_admin": False,
        "status": "active",
        "password_hash": None,
        "ldap_dn": None,
        "ldap_uid": None,
        "created_at": None,
        "updated_at": None,
        "last_login_at": None,
        "last_synced_at": None,
    }
    defaults.update(kwargs)
    return defaults


class TestParseUserRow:
    def test_basic(self):
        db = make_db()
        row = _row(source="manual", is_admin=True, status="active")
        result = db._parse_user_row(row)
        assert result["source"] == "manual"
        assert result["is_admin"] is True
        assert result["status"] == "active"


class TestCreateUser:
    async def test_create_returns_user(self):
        db = make_db()
        fake_row = _row(username="alice", is_admin=True, source="manual")
        acquire, conn = mock_acquire(fake_row)
        db.pool.acquire = acquire

        result = await db.create_user(
            username="alice",
            is_admin=True,
            password_hash="hashed",
        )
        assert result["username"] == "alice"
        assert result["is_admin"] is True


class TestGetUser:
    async def test_found(self):
        db = make_db()
        fake_row = _row(id=7, username="bob")
        acquire, conn = mock_acquire(fake_row)
        db.pool.acquire = acquire

        result = await db.get_user(7)
        assert result is not None
        assert result["id"] == 7

    async def test_not_found(self):
        db = make_db()
        acquire, conn = mock_acquire(None)
        db.pool.acquire = acquire

        result = await db.get_user(999)
        assert result is None


class TestGetUserByUsername:
    async def test_found(self):
        db = make_db()
        fake_row = _row(username="carol")
        acquire, conn = mock_acquire(fake_row)
        db.pool.acquire = acquire

        result = await db.get_user_by_username("carol")
        assert result["username"] == "carol"

    async def test_not_found(self):
        db = make_db()
        acquire, conn = mock_acquire(None)
        db.pool.acquire = acquire

        result = await db.get_user_by_username("nobody")
        assert result is None


class TestListUsers:
    async def test_list_returns_rows(self):
        db = make_db()
        rows = [_row(id=1, username="a"), _row(id=2, username="b")]

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=rows)

        @asynccontextmanager
        async def acquire():
            yield conn

        db.pool.acquire = acquire

        results = await db.list_users()
        assert len(results) == 2

    async def test_list_with_filters(self):
        db = make_db()
        rows = [_row(source="ldap")]

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=rows)

        @asynccontextmanager
        async def acquire():
            yield conn

        db.pool.acquire = acquire

        results = await db.list_users(source="ldap")
        assert results[0]["source"] == "ldap"


class TestDeleteUser:
    async def test_soft_delete(self):
        db = make_db()
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="UPDATE 1")

        @asynccontextmanager
        async def acquire():
            yield conn

        db.pool.acquire = acquire

        result = await db.delete_user(1)
        assert result is True

    async def test_not_found(self):
        db = make_db()
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value="UPDATE 0")

        @asynccontextmanager
        async def acquire():
            yield conn

        db.pool.acquire = acquire

        result = await db.delete_user(999)
        assert result is False


class TestCountUsers:
    async def test_count(self):
        db = make_db()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"n": 5})

        @asynccontextmanager
        async def acquire():
            yield conn

        db.pool.acquire = acquire

        count = await db.count_users()
        assert count == 5


class TestUpsertLdapUser:
    async def test_creates_new(self):
        db = make_db()
        created_row = _row(
            username="ldap-user",
            source="ldap",
            ldap_dn="uid=ldap-user,dc=example,dc=com",
        )

        conn = AsyncMock()
        # fetchrow for "SELECT id FROM users WHERE username = $1" → None (new)
        # fetchrow for INSERT RETURNING * → created_row
        conn.fetchrow = AsyncMock(side_effect=[None, created_row])

        @asynccontextmanager
        async def acquire():
            yield conn

        db.pool.acquire = acquire

        result = await db.upsert_ldap_user(
            ldap_dn="uid=ldap-user,dc=example,dc=com",
            ldap_uid="ldap-user",
            username="ldap-user",
            email="ldap@example.com",
            display_name="LDAP User",
        )
        assert result["_created"] is True
        assert result["username"] == "ldap-user"

    async def test_updates_existing(self):
        db = make_db()
        existing_row = {"id": 3}
        updated_row = _row(id=3, username="ldap-user", source="ldap")

        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=[existing_row, updated_row])

        @asynccontextmanager
        async def acquire():
            yield conn

        db.pool.acquire = acquire

        result = await db.upsert_ldap_user(
            ldap_dn="uid=ldap-user,dc=example,dc=com",
            ldap_uid="ldap-user",
            username="ldap-user",
            email=None,
            display_name=None,
        )
        assert result["_created"] is False
