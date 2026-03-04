"""Unit tests for auth.py — JWT, password hashing, and FastAPI dependencies."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

import auth as auth_module
from auth import AuthManager, check_system_permission


class TestAuthManagerPasswords:
    def test_hash_and_verify(self):
        mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        hashed = mgr.hash_password("hunter2")
        assert hashed != "hunter2"
        assert mgr.verify_password("hunter2", hashed)

    def test_wrong_password_fails(self):
        mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        hashed = mgr.hash_password("correct")
        assert not mgr.verify_password("wrong", hashed)

    def test_empty_secret_raises(self):
        with pytest.raises(ValueError):
            AuthManager(jwt_secret_key="")


class TestAuthManagerTokens:
    def setup_method(self):
        self.mgr = AuthManager(
            jwt_secret_key="unit-test-secret-key-for-tests-only-32b", jwt_expiry_hours=1
        )
        self.user = {
            "id": 42,
            "username": "alice",
            "is_admin": True,
            "source": "manual",
        }

    def test_create_and_decode(self):
        token = self.mgr.create_token(self.user)
        payload = self.mgr.decode_token(token)
        assert payload["sub"] == "42"
        assert payload["username"] == "alice"
        assert payload["is_admin"] is True
        assert payload["source"] == "manual"

    def test_create_token_non_admin(self):
        user = dict(self.user, is_admin=False)
        token = self.mgr.create_token(user)
        payload = self.mgr.decode_token(token)
        assert payload["is_admin"] is False

    def test_wrong_secret_raises_401(self):
        token = self.mgr.create_token(self.user)
        other = AuthManager(jwt_secret_key="other-secret-key-for-unit-tests-only-32b")
        with pytest.raises(HTTPException) as exc_info:
            other.decode_token(token)
        assert exc_info.value.status_code == 401

    def test_expired_token_raises_401(self):
        import jwt
        from datetime import datetime, timezone

        payload = {
            "sub": "1",
            "username": "u",
            "is_admin": False,
            "source": "manual",
            "exp": datetime(2000, 1, 1, tzinfo=timezone.utc),
        }
        token = jwt.encode(
            payload, "unit-test-secret-key-for-tests-only-32b", algorithm="HS256"
        )
        with pytest.raises(HTTPException) as exc_info:
            self.mgr.decode_token(token)
        assert exc_info.value.status_code == 401


class TestDependencies:
    """Tests for get_current_user / require_* dependency functions."""

    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="dep-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)
        self.user = {
            "id": 1,
            "username": "testuser",
            "is_admin": True,
            "source": "manual",
        }

    def _make_request(self, token: str):
        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {token}"}
        return req

    async def test_get_current_user_valid(self):
        token = self.mgr.create_token(self.user)
        req = self._make_request(token)
        payload = await auth_module.get_current_user(req)
        assert payload["username"] == "testuser"

    async def test_get_current_user_no_header(self):
        req = MagicMock()
        req.headers = {}
        with pytest.raises(HTTPException) as exc_info:
            await auth_module.get_current_user(req)
        assert exc_info.value.status_code == 401

    async def test_require_admin_passes(self):
        payload = {"is_admin": True, "username": "admin"}
        result = await auth_module.require_admin(payload)
        assert result == payload

    async def test_require_admin_rejects_non_admin(self):
        with pytest.raises(HTTPException) as exc_info:
            await auth_module.require_admin({"is_admin": False})
        assert exc_info.value.status_code == 403

    async def test_require_admin_rejects_missing_flag(self):
        with pytest.raises(HTTPException) as exc_info:
            await auth_module.require_admin({})
        assert exc_info.value.status_code == 403


class TestCheckSystemPermission:
    """Tests for check_system_permission."""

    async def test_admin_always_allowed(self):
        user = {"is_admin": True}
        db = AsyncMock()
        result = await check_system_permission(user, db, "view_webhooks")
        assert result is True
        db.get_custom_role.assert_not_called()

    async def test_no_custom_role_denied(self):
        user = {"is_admin": False, "custom_role_id": None}
        db = AsyncMock()
        result = await check_system_permission(user, db, "view_webhooks")
        assert result is False

    async def test_permission_present_allowed(self):
        user = {"is_admin": False, "custom_role_id": 7}
        db = AsyncMock()
        db.get_custom_role = AsyncMock(
            return_value={"system_permissions": ["view_webhooks", "view_plugins"]}
        )
        result = await check_system_permission(user, db, "view_webhooks")
        assert result is True

    async def test_permission_absent_denied(self):
        user = {"is_admin": False, "custom_role_id": 7}
        db = AsyncMock()
        db.get_custom_role = AsyncMock(
            return_value={"system_permissions": ["view_webhooks"]}
        )
        result = await check_system_permission(user, db, "view_plugins")
        assert result is False

    async def test_role_not_found_denied(self):
        user = {"is_admin": False, "custom_role_id": 99}
        db = AsyncMock()
        db.get_custom_role = AsyncMock(return_value=None)
        result = await check_system_permission(user, db, "view_webhooks")
        assert result is False
