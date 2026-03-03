"""Unit tests for LDAPSyncManager."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ldap_sync import LDAPConfig, LDAPSyncManager


def _make_manager(url: str = "ldap://localhost:389") -> LDAPSyncManager:
    cfg = LDAPConfig(
        url=url,
        bind_dn="cn=admin,dc=example,dc=com",
        bind_password="secret",
        base_dn="dc=example,dc=com",
        user_filter="(objectClass=inetOrgPerson)",
        attr_username="uid",
        attr_email="mail",
        attr_display_name="cn",
        sync_interval=0,
    )
    return LDAPSyncManager(cfg)


class TestIsConfigured:
    def test_configured_when_url_set(self):
        mgr = _make_manager()
        assert mgr.is_configured() is True

    def test_not_configured_without_url(self):
        mgr = LDAPSyncManager(LDAPConfig())
        assert mgr.is_configured() is False


class TestAuthenticate:
    def test_returns_false_when_not_configured(self):
        mgr = LDAPSyncManager(LDAPConfig())
        result = mgr.authenticate("uid=alice,dc=example,dc=com", "pass")
        assert result is False

    def test_successful_bind(self):
        mgr = _make_manager()
        mock_conn = MagicMock()
        mock_conn.unbind = MagicMock()

        with patch("ldap3.Connection", return_value=mock_conn):
            with patch("ldap3.Server"):
                result = mgr.authenticate("uid=alice,dc=example,dc=com", "correct")
        assert result is True

    def test_failed_bind_returns_false(self):
        mgr = _make_manager()
        with patch("ldap3.Connection", side_effect=Exception("Invalid credentials")):
            with patch("ldap3.Server"):
                result = mgr.authenticate("uid=alice,dc=example,dc=com", "wrong")
        assert result is False


class TestSearchUsers:
    def test_raises_when_not_configured(self):
        mgr = LDAPSyncManager(LDAPConfig())
        with pytest.raises(RuntimeError):
            mgr.search_users()

    def test_returns_user_list(self):
        mgr = _make_manager()

        # Build a fake ldap3 entry using a plain class so __getattr__ works
        class _Attr:
            def __init__(self, v):
                self.value = v

        class _Entry:
            entry_dn = "uid=alice,dc=example,dc=com"
            uid = _Attr("alice")
            mail = _Attr("alice@example.com")
            cn = _Attr("Alice Smith")

        mock_conn = MagicMock()
        mock_conn.entries = [_Entry()]
        mock_conn.unbind = MagicMock()

        with patch("ldap3.Server"), patch("ldap3.Connection", return_value=mock_conn):
            users = mgr.search_users()

        assert len(users) == 1
        assert users[0]["uid"] == "alice"
        assert users[0]["dn"] == "uid=alice,dc=example,dc=com"


class TestSyncToDb:
    async def test_sync_not_configured_returns_zeros(self):
        mgr = LDAPSyncManager(LDAPConfig())
        db = AsyncMock()
        stats = await mgr.sync_to_db(db)
        assert stats == {"created": 0, "updated": 0, "total": 0}

    async def test_sync_creates_users(self):
        mgr = _make_manager()

        fake_users = [
            {
                "dn": "uid=alice,dc=example,dc=com",
                "uid": "alice",
                "email": "alice@example.com",
                "display_name": "Alice",
            }
        ]

        db = AsyncMock()
        db.upsert_ldap_user = AsyncMock(
            return_value={"username": "alice", "_created": True}
        )

        with patch.object(mgr, "search_users", return_value=fake_users):
            stats = await mgr.sync_to_db(db)

        assert stats["created"] == 1
        assert stats["updated"] == 0
        assert stats["total"] == 1

    async def test_sync_updates_existing(self):
        mgr = _make_manager()

        fake_users = [
            {
                "dn": "uid=bob,dc=example,dc=com",
                "uid": "bob",
                "email": "bob@example.com",
                "display_name": "Bob",
            }
        ]

        db = AsyncMock()
        db.upsert_ldap_user = AsyncMock(
            return_value={"username": "bob", "_created": False}
        )

        with patch.object(mgr, "search_users", return_value=fake_users):
            stats = await mgr.sync_to_db(db)

        assert stats["created"] == 0
        assert stats["updated"] == 1
        assert stats["total"] == 1
