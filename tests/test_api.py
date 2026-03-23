"""Tests for HTTP API endpoints in plugins/inputs/http/api.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import auth as auth_module
from auth import AuthManager
from db import DatabaseManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(mgr: AuthManager, user: dict) -> dict:
    token = mgr.create_token(user)
    return {"Authorization": f"Bearer {token}"}


async def _make_plugin_client(
    db_mock, *, ldap_mgr=None, auth_mgr=None, admission_chain=None
) -> TestClient:
    """Build a TestClient for the 3 resource write routes in HTTPInputPlugin."""
    from plugins.inputs.http.api import HTTPInputPlugin

    plugin = HTTPInputPlugin()
    await plugin.initialize({"host": "127.0.0.1", "port": 8000})
    plugin.set_db_manager(db_mock)
    if admission_chain is not None:
        plugin.set_admission_chain(admission_chain)
    plugin._setup_routes()
    return TestClient(plugin.app, raise_server_exceptions=False)


_UNSET = object()  # sentinel to distinguish "not passed" from None


async def _make_management_client(
    db_mock, *, ldap_mgr=_UNSET, auth_mgr=_UNSET, admission_chain=None
) -> TestClient:
    """Build a TestClient for all management routes (create_management_router)."""
    from fastapi import FastAPI
    from management_api import create_management_router

    # Use a real MagicMock only when the caller didn't specify the arg at all.
    # When the caller explicitly passes None we forward None so that the route
    # handler can detect the absence of the dependency.
    resolved_ldap = MagicMock() if ldap_mgr is _UNSET else ldap_mgr
    resolved_auth = MagicMock() if auth_mgr is _UNSET else auth_mgr

    app = FastAPI()
    router = create_management_router(
        db_manager=db_mock,
        auth_manager=resolved_auth,
        ldap_manager=resolved_ldap,
        event_bus=MagicMock(),
        admission_chain=admission_chain or MagicMock(),
    )
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


# Backward-compatible alias used by tests that call either helper
async def _make_client(db_mock, *, ldap_mgr=_UNSET, auth_mgr=_UNSET) -> TestClient:
    """Legacy helper: returns a management client for non-write-route tests."""
    return await _make_management_client(db_mock, ldap_mgr=ldap_mgr, auth_mgr=auth_mgr)


def _admin() -> dict:
    return {
        "id": 1,
        "username": "admin",
        "is_admin": True,
        "source": "manual",
        "custom_role_id": None,
    }


def _viewer(custom_role_id=None) -> dict:
    return {
        "id": 2,
        "username": "viewer",
        "is_admin": False,
        "source": "manual",
        "custom_role_id": custom_role_id,
    }


def _user_row(**kwargs) -> dict:
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
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "last_login_at": None,
        "last_synced_at": None,
        "custom_role_id": None,
    }
    defaults.update(kwargs)
    return defaults


def _resource_row(**kwargs) -> dict:
    defaults = {
        "id": 1,
        "name": "my-cluster",
        "resource_type_name": "DatabaseCluster",
        "resource_type_version": "v1",
        "action_plugin": "",
        "spec": {"engine": "postgres"},
        "plugin_config": {},
        "metadata": {},
        "outputs": {},
        "status": "ready",
        "status_message": None,
        "generation": 1,
        "observed_generation": 1,
        "spec_hash": "abc",
        "retry_count": 0,
        "last_reconcile_time": None,
        "next_reconcile_time": None,
        "finalizers": [],
        "conditions": [],
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "deleted_at": None,
    }
    defaults.update(kwargs)
    return defaults


def _resource_type_row(**kwargs) -> dict:
    defaults = {
        "id": 1,
        "name": "DatabaseCluster",
        "version": "v1",
        "schema": {"type": "object", "properties": {"engine": {"type": "string"}}},
        "description": None,
        "status": "active",
        "metadata": {},
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(kwargs)
    return defaults


def _webhook_row(**kwargs) -> dict:
    defaults = {
        "id": 1,
        "name": "my-webhook",
        "webhook_url": "http://example.com/webhook",
        "webhook_type": "validating",
        "operations": ["CREATE", "UPDATE"],
        "resource_type_name": None,
        "resource_type_version": None,
        "timeout_seconds": 10,
        "failure_policy": "Fail",
        "ordering": 0,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(kwargs)
    return defaults


def _history_row(**kwargs) -> dict:
    defaults = {
        "id": 1,
        "resource_id": 1,
        "generation": 1,
        "success": True,
        "phase": "complete",
        "error_message": None,
        "resources_created": 1,
        "resources_updated": 0,
        "resources_deleted": 0,
        "reconcile_time": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(kwargs)
    return defaults


def _perm_row(**kwargs) -> dict:
    # operations is a list here because API-level mocks bypass the DB parsing layer
    defaults = {
        "id": 1,
        "role_id": 1,
        "resource_type_name": "DatabaseCluster",
        "resource_type_version": "v1",
        "operations": ["CREATE", "READ"],
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(kwargs)
    return defaults


def _role_data(**kwargs) -> dict:
    defaults = {
        "id": 1,
        "name": "db-writer",
        "description": None,
        "system_permissions": [],
        "permissions": [],
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class TestValidateNameFormat:
    def test_valid_name(self):
        from api_models import validate_name_format

        assert validate_name_format("my-cluster", "name") == "my-cluster"

    def test_single_char_valid(self):
        from api_models import validate_name_format

        assert validate_name_format("a", "name") == "a"

    def test_empty_raises(self):
        from api_models import validate_name_format

        with pytest.raises(ValueError, match="cannot be empty"):
            validate_name_format("", "name")

    def test_too_long_raises(self):
        from api_models import validate_name_format

        with pytest.raises(ValueError, match="cannot exceed"):
            validate_name_format("a" * 64, "name")

    def test_uppercase_raises(self):
        from api_models import validate_name_format

        with pytest.raises(ValueError, match="must consist of lowercase"):
            validate_name_format("MyCluster", "name")

    def test_starts_with_hyphen_raises(self):
        from api_models import validate_name_format

        with pytest.raises(ValueError):
            validate_name_format("-bad-name", "name")

    def test_ends_with_hyphen_raises(self):
        from api_models import validate_name_format

        with pytest.raises(ValueError):
            validate_name_format("bad-name-", "name")


class TestValidateJsonSize:
    def test_within_limit(self):
        from api_models import validate_json_size

        result = validate_json_size({"key": "val"}, "spec")
        assert result == {"key": "val"}

    def test_none_is_allowed(self):
        from api_models import validate_json_size

        assert validate_json_size(None, "spec") is None

    def test_over_limit_raises(self):
        from api_models import MAX_SPEC_SIZE, validate_json_size

        big = {"k": "x" * MAX_SPEC_SIZE}
        with pytest.raises(ValueError, match="exceeds maximum size"):
            validate_json_size(big, "spec")


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


class TestAuthEndpoints:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)
        self.admin = _admin()

    async def test_login_success(self):
        db = AsyncMock(spec=DatabaseManager)
        pw_hash = self.mgr.hash_password("password123")
        db.get_user_by_username = AsyncMock(
            return_value=_user_row(
                username="admin", is_admin=True, password_hash=pw_hash
            )
        )
        db.update_user_last_login = AsyncMock()
        client = await _make_client(db, auth_mgr=self.mgr)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "password123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["username"] == "admin"

    async def test_login_wrong_password(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_user_by_username = AsyncMock(
            return_value=_user_row(password_hash=self.mgr.hash_password("correct"))
        )
        client = await _make_client(db, auth_mgr=self.mgr)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401

    async def test_login_user_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_user_by_username = AsyncMock(return_value=None)
        client = await _make_client(db, auth_mgr=self.mgr)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "pass"},
        )
        assert resp.status_code == 401

    async def test_login_suspended_user(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_user_by_username = AsyncMock(return_value=_user_row(status="suspended"))
        client = await _make_client(db, auth_mgr=self.mgr)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "pass"},
        )
        assert resp.status_code == 401

    async def test_login_ldap_user_no_ldap_manager(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_user_by_username = AsyncMock(
            return_value=_user_row(source="ldap", ldap_dn="uid=alice,dc=example,dc=com")
        )
        client = await _make_client(db, ldap_mgr=None, auth_mgr=self.mgr)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "pass"},
        )
        assert resp.status_code == 401

    async def test_login_ldap_user_bad_password(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_user_by_username = AsyncMock(
            return_value=_user_row(source="ldap", ldap_dn="uid=alice,dc=example,dc=com")
        )
        ldap_mgr = MagicMock()
        ldap_mgr.authenticate = MagicMock(return_value=False)
        db.update_user_last_login = AsyncMock()
        client = await _make_client(db, ldap_mgr=ldap_mgr, auth_mgr=self.mgr)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "wrong"},
        )
        assert resp.status_code == 401

    async def test_auth_me_returns_current_user(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_user = AsyncMock(return_value=_user_row(id=1, username="admin"))
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/auth/me",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"

    async def test_auth_me_no_token_returns_401(self):
        db = AsyncMock(spec=DatabaseManager)
        client = await _make_client(db)

        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    async def test_auth_me_user_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_user = AsyncMock(return_value=None)
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/auth/me",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# User management endpoints
# ---------------------------------------------------------------------------


class TestUserEndpoints:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)
        self.admin = _admin()
        self.non_admin = _viewer()

    async def test_create_user(self):
        db = AsyncMock(spec=DatabaseManager)
        db.create_user = AsyncMock(return_value=_user_row(username="newuser"))
        client = await _make_client(db, auth_mgr=self.mgr)

        resp = client.post(
            "/api/v1/users",
            json={"username": "newuser", "password": "password123"},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 201
        assert resp.json()["username"] == "newuser"

    async def test_create_user_requires_admin(self):
        db = AsyncMock(spec=DatabaseManager)
        client = await _make_client(db, auth_mgr=self.mgr)

        resp = client.post(
            "/api/v1/users",
            json={"username": "newuser", "password": "password123"},
            headers=_auth_headers(self.mgr, self.non_admin),
        )
        assert resp.status_code == 403

    async def test_create_user_duplicate_returns_409(self):
        db = AsyncMock(spec=DatabaseManager)
        db.create_user = AsyncMock(side_effect=Exception("unique constraint violated"))
        client = await _make_client(db, auth_mgr=self.mgr)

        resp = client.post(
            "/api/v1/users",
            json={"username": "existing", "password": "password123"},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 409

    async def test_create_user_invalid_username_422(self):
        db = AsyncMock(spec=DatabaseManager)
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/users",
            json={"username": "InvalidUPPER", "password": "password123"},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 422

    async def test_list_users(self):
        db = AsyncMock(spec=DatabaseManager)
        db.list_users = AsyncMock(
            return_value=[_user_row(id=1), _user_row(id=2, username="other")]
        )
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/users",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_get_user_by_id(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_user = AsyncMock(return_value=_user_row(id=5, username="specific"))
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/users/5",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "specific"

    async def test_get_user_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_user = AsyncMock(return_value=None)
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/users/999",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_update_user(self):
        db = AsyncMock(spec=DatabaseManager)
        db.update_user = AsyncMock(return_value=_user_row(email="new@example.com"))
        client = await _make_client(db)

        resp = client.put(
            "/api/v1/users/1",
            json={"email": "new@example.com"},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "new@example.com"

    async def test_update_user_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.update_user = AsyncMock(return_value=None)
        client = await _make_client(db)

        resp = client.put(
            "/api/v1/users/999",
            json={"email": "x@y.com"},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_delete_user(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_user = AsyncMock(return_value=True)
        client = await _make_client(db)

        resp = client.delete(
            "/api/v1/users/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 204

    async def test_delete_user_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_user = AsyncMock(return_value=False)
        client = await _make_client(db)

        resp = client.delete(
            "/api/v1/users/999",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_ldap_sync_no_manager_returns_503(self):
        db = AsyncMock(spec=DatabaseManager)
        client = await _make_client(db, ldap_mgr=None)

        resp = client.post(
            "/api/v1/users/ldap-sync",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 503

    async def test_ldap_sync_success(self):
        db = AsyncMock(spec=DatabaseManager)
        ldap_mgr = MagicMock()
        ldap_mgr.is_configured.return_value = True
        ldap_mgr.sync_to_db = AsyncMock(
            return_value={"created": 2, "updated": 1, "total": 3}
        )
        client = await _make_client(db, ldap_mgr=ldap_mgr)

        resp = client.post(
            "/api/v1/users/ldap-sync",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["created"] == 2
        assert resp.json()["total"] == 3

    async def test_ldap_sync_not_configured(self):
        db = AsyncMock(spec=DatabaseManager)
        ldap_mgr = MagicMock()
        ldap_mgr.is_configured.return_value = False
        client = await _make_client(db, ldap_mgr=ldap_mgr)

        resp = client.post(
            "/api/v1/users/ldap-sync",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Resource type endpoints
# ---------------------------------------------------------------------------


class TestResourceTypeEndpoints:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)
        self.admin = _admin()
        self.viewer = _viewer()

    async def test_create_resource_type(self):
        db = AsyncMock(spec=DatabaseManager)
        db.create_resource_type = AsyncMock(return_value=1)
        db.get_resource_type = AsyncMock(return_value=_resource_type_row())
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/resource-types",
            json={
                "name": "DatabaseCluster",
                "version": "v1",
                "schema": {"type": "object"},
            },
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "DatabaseCluster"

    async def test_create_resource_type_requires_admin(self):
        db = AsyncMock(spec=DatabaseManager)
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/resource-types",
            json={
                "name": "DatabaseCluster",
                "version": "v1",
                "schema": {"type": "object"},
            },
            headers=_auth_headers(self.mgr, self.viewer),
        )
        assert resp.status_code == 403

    async def test_create_resource_type_duplicate_returns_409(self):
        db = AsyncMock(spec=DatabaseManager)
        db.create_resource_type = AsyncMock(
            side_effect=Exception("unique constraint violated")
        )
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/resource-types",
            json={
                "name": "DatabaseCluster",
                "version": "v1",
                "schema": {"type": "object"},
            },
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 409

    async def test_list_resource_types(self):
        db = AsyncMock(spec=DatabaseManager)
        db.list_resource_types = AsyncMock(return_value=[_resource_type_row()])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/resource-types",
            headers=_auth_headers(self.mgr, self.viewer),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_get_resource_type_by_id(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource_type = AsyncMock(return_value=_resource_type_row())
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/resource-types/1",
            headers=_auth_headers(self.mgr, self.viewer),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "DatabaseCluster"

    async def test_get_resource_type_by_id_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource_type = AsyncMock(return_value=None)
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/resource-types/999",
            headers=_auth_headers(self.mgr, self.viewer),
        )
        assert resp.status_code == 404

    async def test_get_resource_type_by_name_version(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource_type_by_name_version = AsyncMock(
            return_value=_resource_type_row()
        )
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/resource-types/DatabaseCluster/v1",
            headers=_auth_headers(self.mgr, self.viewer),
        )
        assert resp.status_code == 200

    async def test_get_resource_type_by_name_version_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource_type_by_name_version = AsyncMock(return_value=None)
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/resource-types/Missing/v1",
            headers=_auth_headers(self.mgr, self.viewer),
        )
        assert resp.status_code == 404

    async def test_update_resource_type(self):
        db = AsyncMock(spec=DatabaseManager)
        db.update_resource_type = AsyncMock()
        db.get_resource_type = AsyncMock(
            return_value=_resource_type_row(description="updated")
        )
        client = await _make_client(db)

        resp = client.put(
            "/api/v1/resource-types/1",
            json={"description": "updated"},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "updated"

    async def test_update_resource_type_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.update_resource_type = AsyncMock()
        db.get_resource_type = AsyncMock(return_value=None)
        client = await _make_client(db)

        resp = client.put(
            "/api/v1/resource-types/999",
            json={"description": "x"},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_delete_resource_type(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_resource_type = AsyncMock(return_value=True)
        client = await _make_client(db)

        resp = client.delete(
            "/api/v1/resource-types/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 204

    async def test_delete_resource_type_has_resources_returns_409(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_resource_type = AsyncMock(return_value=False)
        client = await _make_client(db)

        resp = client.delete(
            "/api/v1/resource-types/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Resource endpoints
# ---------------------------------------------------------------------------


class TestResourceEndpoints:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)
        self.admin = _admin()

    def _mock_registry(self, *, has_reconciler=True, reconciler_name="db-reconciler"):
        mock_reconciler = MagicMock()
        mock_reconciler.name = reconciler_name
        registry = MagicMock()
        registry.has_reconciler_for_resource_type.return_value = has_reconciler
        registry.get_reconciler_for_resource_type.return_value = mock_reconciler
        return registry

    async def test_create_resource_with_reconciler(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource_type_by_name_version = AsyncMock(
            return_value=_resource_type_row()
        )
        db.get_matching_webhooks = AsyncMock(return_value=[])
        db.create_resource = AsyncMock(return_value=1)
        db.get_resource = AsyncMock(return_value=_resource_row())
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_plugin_client(db)

        with patch(
            "plugins.registry.get_registry",
            return_value=self._mock_registry(),
        ):
            resp = client.post(
                "/api/v1/resources",
                json={
                    "name": "my-cluster",
                    "resource_type_name": "DatabaseCluster",
                    "resource_type_version": "v1",
                    "spec": {"engine": "postgres"},
                },
                headers=_auth_headers(self.mgr, self.admin),
            )
        assert resp.status_code == 201
        assert resp.json()["name"] == "my-cluster"

    async def test_create_resource_no_reconciler_no_plugin_returns_400(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_plugin_client(db)

        with patch(
            "plugins.registry.get_registry",
            return_value=self._mock_registry(has_reconciler=False),
        ):
            resp = client.post(
                "/api/v1/resources",
                json={
                    "name": "my-cluster",
                    "resource_type_name": "DatabaseCluster",
                    "resource_type_version": "v1",
                    "spec": {"engine": "postgres"},
                },
                headers=_auth_headers(self.mgr, self.admin),
            )
        assert resp.status_code == 400
        assert "reconciler" in resp.json()["detail"].lower()

    async def test_create_resource_no_spec_returns_400(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource_type_by_name_version = AsyncMock(
            return_value=_resource_type_row()
        )
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_plugin_client(db)

        with patch(
            "plugins.registry.get_registry",
            return_value=self._mock_registry(),
        ):
            resp = client.post(
                "/api/v1/resources",
                json={
                    "name": "my-cluster",
                    "resource_type_name": "DatabaseCluster",
                    "resource_type_version": "v1",
                },
                headers=_auth_headers(self.mgr, self.admin),
            )
        assert resp.status_code == 400
        assert "spec" in resp.json()["detail"].lower()

    async def test_create_resource_resource_type_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource_type_by_name_version = AsyncMock(return_value=None)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_plugin_client(db)

        with patch(
            "plugins.registry.get_registry",
            return_value=self._mock_registry(),
        ):
            resp = client.post(
                "/api/v1/resources",
                json={
                    "name": "my-cluster",
                    "resource_type_name": "DatabaseCluster",
                    "resource_type_version": "v1",
                    "spec": {"engine": "postgres"},
                },
                headers=_auth_headers(self.mgr, self.admin),
            )
        assert resp.status_code == 400

    async def test_get_resource_by_name(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource_by_name = AsyncMock(return_value=_resource_row())
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/resources/by-name/DatabaseCluster/v1/my-cluster",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-cluster"

    async def test_get_resource_by_name_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource_by_name = AsyncMock(return_value=None)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/resources/by-name/DatabaseCluster/v1/missing",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_update_resource(self):
        db = AsyncMock(spec=DatabaseManager)
        resource = _resource_row()
        db.get_resource = AsyncMock(return_value=resource)
        db.get_resource_type_by_name_version = AsyncMock(
            return_value=_resource_type_row()
        )
        db.get_matching_webhooks = AsyncMock(return_value=[])
        db.update_resource = AsyncMock()
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_plugin_client(db)

        resp = client.put(
            "/api/v1/resources/1",
            json={"spec": {"engine": "postgres"}},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200

    async def test_update_resource_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=None)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_plugin_client(db)

        resp = client.put(
            "/api/v1/resources/999",
            json={"spec": {"engine": "postgres"}},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_delete_resource(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=_resource_row())
        db.get_matching_webhooks = AsyncMock(return_value=[])
        db.delete_resource = AsyncMock()
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_plugin_client(db)

        resp = client.delete(
            "/api/v1/resources/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 202
        assert resp.json()["resource_id"] == 1

    async def test_delete_resource_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=None)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_plugin_client(db)

        resp = client.delete(
            "/api/v1/resources/999",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_update_finalizers_add(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(
            return_value=_resource_row(status="ready", finalizers=[])
        )
        db.add_finalizer = AsyncMock()
        db.remove_finalizer = AsyncMock()
        db.get_finalizers = AsyncMock(return_value=["my-finalizer"])
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.put(
            "/api/v1/resources/1/finalizers",
            json={"add": ["my-finalizer"], "remove": []},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200

    async def test_update_finalizers_clears_deleting_resource(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(
            return_value=_resource_row(status="deleting", finalizers=["f1"])
        )
        db.add_finalizer = AsyncMock()
        db.remove_finalizer = AsyncMock()
        db.get_finalizers = AsyncMock(return_value=[])
        db.hard_delete_resource = AsyncMock()
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.put(
            "/api/v1/resources/1/finalizers",
            json={"add": [], "remove": ["f1"]},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"].lower()
        db.hard_delete_resource.assert_called_once_with(1)

    async def test_trigger_reconciliation(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=_resource_row())
        db.mark_resource_for_reconciliation = AsyncMock()
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/resources/1/reconcile",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 202
        assert resp.json()["message"] == "Reconciliation triggered"
        db.mark_resource_for_reconciliation.assert_called_once_with(1)

    async def test_trigger_reconciliation_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=None)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/resources/999/reconcile",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_get_reconciliation_history(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=_resource_row())
        db.get_reconciliation_history = AsyncMock(return_value=[_history_row()])
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/resources/1/history",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["success"] is True

    async def test_get_reconciliation_history_resource_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=None)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/resources/999/history",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_get_resource_outputs(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(
            return_value=_resource_row(outputs={"endpoint": "db.example.com"})
        )
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/resources/1/outputs",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["outputs"]["endpoint"] == "db.example.com"

    async def test_get_resource_outputs_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=None)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/resources/999/outputs",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Custom role permission sub-endpoints + PUT /custom-roles/{id}
# ---------------------------------------------------------------------------


class TestCustomRolePermissionEndpoints:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)
        self.admin = _admin()

    async def test_add_permission_to_role(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role = AsyncMock(return_value=_role_data())
        db.add_role_permission = AsyncMock(return_value=_perm_row())
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/custom-roles/1/permissions",
            json={
                "resource_type_name": "DatabaseCluster",
                "resource_type_version": "v1",
                "operations": ["CREATE", "READ"],
            },
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 201
        assert resp.json()["role_id"] == 1

    async def test_add_permission_role_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role = AsyncMock(return_value=None)
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/custom-roles/999/permissions",
            json={"operations": ["READ"]},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_add_permission_duplicate_returns_409(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role = AsyncMock(return_value=_role_data())
        db.add_role_permission = AsyncMock(
            side_effect=Exception("unique constraint violated")
        )
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/custom-roles/1/permissions",
            json={"operations": ["READ"]},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 409

    async def test_update_role_permission(self):
        db = AsyncMock(spec=DatabaseManager)
        db.update_role_permission = AsyncMock(return_value=_perm_row())
        client = await _make_client(db)

        resp = client.put(
            "/api/v1/custom-roles/1/permissions/1",
            json={"operations": ["READ"]},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200

    async def test_update_role_permission_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.update_role_permission = AsyncMock(return_value=None)
        client = await _make_client(db)

        resp = client.put(
            "/api/v1/custom-roles/1/permissions/999",
            json={"operations": ["READ"]},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_delete_role_permission(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_role_permission = AsyncMock(return_value=True)
        client = await _make_client(db)

        resp = client.delete(
            "/api/v1/custom-roles/1/permissions/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 204

    async def test_delete_role_permission_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_role_permission = AsyncMock(return_value=False)
        client = await _make_client(db)

        resp = client.delete(
            "/api/v1/custom-roles/1/permissions/999",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_update_custom_role(self):
        db = AsyncMock(spec=DatabaseManager)
        db.update_custom_role = AsyncMock(return_value=_role_data(name="renamed"))
        client = await _make_client(db)

        resp = client.put(
            "/api/v1/custom-roles/1",
            json={"name": "renamed"},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "renamed"

    async def test_update_custom_role_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.update_custom_role = AsyncMock(return_value=None)
        client = await _make_client(db)

        resp = client.put(
            "/api/v1/custom-roles/999",
            json={"name": "x"},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Admission webhook endpoints
# ---------------------------------------------------------------------------


class TestAdmissionWebhookEndpoints:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)
        self.admin = _admin()
        self.no_perm_user = _viewer()

    async def test_create_webhook(self):
        db = AsyncMock(spec=DatabaseManager)
        db.create_admission_webhook = AsyncMock(return_value=1)
        db.get_admission_webhook = AsyncMock(return_value=_webhook_row())
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/admission-webhooks",
            json={
                "name": "my-webhook",
                "webhook_url": "http://example.com/webhook",
                "webhook_type": "validating",
                "operations": ["CREATE"],
            },
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "my-webhook"

    async def test_create_webhook_invalid_type_returns_422(self):
        db = AsyncMock(spec=DatabaseManager)
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/admission-webhooks",
            json={
                "name": "my-webhook",
                "webhook_url": "http://example.com/webhook",
                "webhook_type": "invalid",
                "operations": ["CREATE"],
            },
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 422

    async def test_create_webhook_invalid_operation_returns_422(self):
        db = AsyncMock(spec=DatabaseManager)
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/admission-webhooks",
            json={
                "name": "my-webhook",
                "webhook_url": "http://example.com/webhook",
                "webhook_type": "validating",
                "operations": ["INVALID"],
            },
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 422

    async def test_create_webhook_duplicate_returns_409(self):
        db = AsyncMock(spec=DatabaseManager)
        db.create_admission_webhook = AsyncMock(
            side_effect=Exception("unique constraint violated")
        )
        client = await _make_client(db)

        resp = client.post(
            "/api/v1/admission-webhooks",
            json={
                "name": "my-webhook",
                "webhook_url": "http://example.com/webhook",
                "webhook_type": "validating",
                "operations": ["CREATE"],
            },
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 409

    async def test_list_webhooks_requires_view_webhooks_permission(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/admission-webhooks",
            headers=_auth_headers(self.mgr, self.no_perm_user),
        )
        assert resp.status_code == 403

    async def test_list_webhooks_admin_bypasses_permission(self):
        db = AsyncMock(spec=DatabaseManager)
        db.list_admission_webhooks = AsyncMock(return_value=[_webhook_row()])
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/admission-webhooks",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_get_webhook_by_id(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_admission_webhook = AsyncMock(return_value=_webhook_row())
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/admission-webhooks/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-webhook"

    async def test_get_webhook_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_admission_webhook = AsyncMock(return_value=None)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/admission-webhooks/999",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_update_webhook(self):
        db = AsyncMock(spec=DatabaseManager)
        db.update_admission_webhook = AsyncMock()
        db.get_admission_webhook = AsyncMock(
            return_value=_webhook_row(timeout_seconds=30)
        )
        client = await _make_client(db)

        resp = client.put(
            "/api/v1/admission-webhooks/1",
            json={"timeout_seconds": 30},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["timeout_seconds"] == 30

    async def test_update_webhook_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.update_admission_webhook = AsyncMock()
        db.get_admission_webhook = AsyncMock(return_value=None)
        client = await _make_client(db)

        resp = client.put(
            "/api/v1/admission-webhooks/999",
            json={"timeout_seconds": 30},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_delete_webhook(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_admission_webhook = AsyncMock(return_value=True)
        client = await _make_client(db)

        resp = client.delete(
            "/api/v1/admission-webhooks/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 204

    async def test_delete_webhook_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_admission_webhook = AsyncMock(return_value=False)
        client = await _make_client(db)

        resp = client.delete(
            "/api/v1/admission-webhooks/999",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Plugin discovery endpoints
# ---------------------------------------------------------------------------


class TestPluginEndpoints:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)
        self.admin = _admin()
        self.no_perm_user = _viewer()

    async def test_list_action_plugins_requires_view_plugins_permission(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/plugins/actions",
            headers=_auth_headers(self.mgr, self.no_perm_user),
        )
        assert resp.status_code == 403

    async def test_list_action_plugins_admin(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        registry = MagicMock()
        registry.list_action_plugins.return_value = ["github_actions"]
        registry.get_action_plugin_info.return_value = {
            "name": "github_actions",
            "version": "1.0.0",
        }
        client = await _make_client(db)

        with patch("plugins.registry.get_registry", return_value=registry):
            resp = client.get(
                "/api/v1/plugins/actions",
                headers=_auth_headers(self.mgr, self.admin),
            )
        assert resp.status_code == 200
        assert resp.json()[0]["name"] == "github_actions"

    async def test_list_input_plugins_admin(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        registry = MagicMock()
        registry.list_input_plugins.return_value = ["http"]
        registry.get_input_plugin_info.return_value = {
            "name": "http",
            "version": "1.0.0",
        }
        client = await _make_client(db)

        with patch("plugins.registry.get_registry", return_value=registry):
            resp = client.get(
                "/api/v1/plugins/inputs",
                headers=_auth_headers(self.mgr, self.admin),
            )
        assert resp.status_code == 200
        assert resp.json()[0]["name"] == "http"

    async def test_list_input_plugins_requires_permission(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await _make_client(db)

        resp = client.get(
            "/api/v1/plugins/inputs",
            headers=_auth_headers(self.mgr, self.no_perm_user),
        )
        assert resp.status_code == 403
