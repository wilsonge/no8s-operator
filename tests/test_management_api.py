"""Tests for the management API router (management_api.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth as auth_module
from auth import AuthManager
from db import DatabaseManager
from management_api import create_management_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(
    db_mock,
    auth_mgr,
    *,
    ldap_mgr=None,
    event_bus=None,
    admission_chain=None,
) -> TestClient:
    app = FastAPI()
    router = create_management_router(
        db_manager=db_mock,
        auth_manager=auth_mgr or MagicMock(),
        ldap_manager=ldap_mgr or MagicMock(),
        event_bus=event_bus or MagicMock(),
        admission_chain=admission_chain or MagicMock(),
    )
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _auth_headers(mgr: AuthManager, user: dict) -> dict:
    token = mgr.create_token(user)
    return {"Authorization": f"Bearer {token}"}


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
# Auth endpoints
# ---------------------------------------------------------------------------


class TestAuthLogin:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)

    async def test_login_success(self):
        db = AsyncMock(spec=DatabaseManager)
        pw_hash = self.mgr.hash_password("password123")
        db.get_user_by_username = AsyncMock(
            return_value=_user_row(
                username="admin", is_admin=True, password_hash=pw_hash
            )
        )
        db.update_user_last_login = AsyncMock()
        client = _make_client(db, self.mgr)

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
        client = _make_client(db, self.mgr)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401

    async def test_login_user_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_user_by_username = AsyncMock(return_value=None)
        client = _make_client(db, self.mgr)

        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "pass"},
        )
        assert resp.status_code == 401

    async def test_auth_me_returns_current_user(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_user = AsyncMock(return_value=_user_row(id=1, username="admin"))
        client = _make_client(db, self.mgr)

        resp = client.get(
            "/api/v1/auth/me",
            headers=_auth_headers(self.mgr, _admin()),
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"

    async def test_auth_me_no_token_returns_401(self):
        db = AsyncMock(spec=DatabaseManager)
        client = _make_client(db, self.mgr)

        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401


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
        client = _make_client(db, self.mgr)

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
        client = _make_client(db, self.mgr)

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

    async def test_list_resource_types(self):
        db = AsyncMock(spec=DatabaseManager)
        db.list_resource_types = AsyncMock(return_value=[_resource_type_row()])
        client = _make_client(db, self.mgr)

        resp = client.get(
            "/api/v1/resource-types",
            headers=_auth_headers(self.mgr, self.viewer),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_get_resource_type_by_id(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource_type = AsyncMock(return_value=_resource_type_row())
        client = _make_client(db, self.mgr)

        resp = client.get(
            "/api/v1/resource-types/1",
            headers=_auth_headers(self.mgr, self.viewer),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "DatabaseCluster"

    async def test_get_resource_type_by_id_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource_type = AsyncMock(return_value=None)
        client = _make_client(db, self.mgr)

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
        client = _make_client(db, self.mgr)

        resp = client.get(
            "/api/v1/resource-types/DatabaseCluster/v1",
            headers=_auth_headers(self.mgr, self.viewer),
        )
        assert resp.status_code == 200

    async def test_update_resource_type(self):
        db = AsyncMock(spec=DatabaseManager)
        db.update_resource_type = AsyncMock()
        db.get_resource_type = AsyncMock(
            return_value=_resource_type_row(description="updated")
        )
        client = _make_client(db, self.mgr)

        resp = client.put(
            "/api/v1/resource-types/1",
            json={"description": "updated"},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "updated"

    async def test_delete_resource_type(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_resource_type = AsyncMock(return_value=True)
        client = _make_client(db, self.mgr)

        resp = client.delete(
            "/api/v1/resource-types/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 204

    async def test_delete_resource_type_has_resources_returns_409(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_resource_type = AsyncMock(return_value=False)
        client = _make_client(db, self.mgr)

        resp = client.delete(
            "/api/v1/resource-types/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Resource read endpoints
# ---------------------------------------------------------------------------


class TestResourceReadEndpoints:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)
        self.admin = _admin()

    async def test_list_resources(self):
        db = AsyncMock(spec=DatabaseManager)
        db.list_resources = AsyncMock(return_value=[_resource_row()])
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = _make_client(db, self.mgr)

        resp = client.get(
            "/api/v1/resources",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_get_resource_by_id(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=_resource_row())
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = _make_client(db, self.mgr)

        resp = client.get(
            "/api/v1/resources/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-cluster"

    async def test_get_resource_by_id_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=None)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = _make_client(db, self.mgr)

        resp = client.get(
            "/api/v1/resources/999",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_get_resource_by_name(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource_by_name = AsyncMock(return_value=_resource_row())
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = _make_client(db, self.mgr)

        resp = client.get(
            "/api/v1/resources/by-name/DatabaseCluster/v1/my-cluster",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-cluster"

    async def test_trigger_reconciliation(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=_resource_row())
        db.mark_resource_for_reconciliation = AsyncMock()
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = _make_client(db, self.mgr)

        resp = client.post(
            "/api/v1/resources/1/reconcile",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 202
        assert resp.json()["message"] == "Reconciliation triggered"

    async def test_get_resource_outputs(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(
            return_value=_resource_row(outputs={"endpoint": "db.example.com"})
        )
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = _make_client(db, self.mgr)

        resp = client.get(
            "/api/v1/resources/1/outputs",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert resp.json()["outputs"]["endpoint"] == "db.example.com"


# ---------------------------------------------------------------------------
# Plugin discovery endpoints
# ---------------------------------------------------------------------------


class TestPluginDiscovery:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)
        self.admin = _admin()
        self.viewer = _viewer()

    async def test_list_action_plugins_admin(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        registry = MagicMock()
        registry.list_action_plugins.return_value = ["github_actions"]
        registry.get_action_plugin_info.return_value = {
            "name": "github_actions",
            "version": "1.0.0",
        }
        client = _make_client(db, self.mgr)

        with patch("plugins.registry.get_registry", return_value=registry):
            resp = client.get(
                "/api/v1/plugins/actions",
                headers=_auth_headers(self.mgr, self.admin),
            )
        assert resp.status_code == 200
        assert resp.json()[0]["name"] == "github_actions"

    async def test_list_action_plugins_requires_view_plugins_permission(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = _make_client(db, self.mgr)

        resp = client.get(
            "/api/v1/plugins/actions",
            headers=_auth_headers(self.mgr, self.viewer),
        )
        assert resp.status_code == 403

    async def test_list_input_plugins_admin(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        registry = MagicMock()
        registry.list_input_plugins.return_value = ["http"]
        registry.get_input_plugin_info.return_value = {
            "name": "http",
            "version": "1.0.0",
        }
        client = _make_client(db, self.mgr)

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
        client = _make_client(db, self.mgr)

        resp = client.get(
            "/api/v1/plugins/inputs",
            headers=_auth_headers(self.mgr, self.viewer),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Admission webhook endpoints
# ---------------------------------------------------------------------------


class TestAdmissionWebhookEndpoints:
    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret-key-for-unit-tests-only-32b")
        auth_module.set_auth_manager(self.mgr)
        self.admin = _admin()
        self.viewer = _viewer()

    async def test_create_webhook(self):
        db = AsyncMock(spec=DatabaseManager)
        db.create_admission_webhook = AsyncMock(return_value=1)
        db.get_admission_webhook = AsyncMock(return_value=_webhook_row())
        client = _make_client(db, self.mgr)

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

    async def test_create_webhook_requires_admin(self):
        db = AsyncMock(spec=DatabaseManager)
        client = _make_client(db, self.mgr)

        resp = client.post(
            "/api/v1/admission-webhooks",
            json={
                "name": "my-webhook",
                "webhook_url": "http://example.com/webhook",
                "webhook_type": "validating",
                "operations": ["CREATE"],
            },
            headers=_auth_headers(self.mgr, self.viewer),
        )
        assert resp.status_code == 403

    async def test_list_webhooks_admin(self):
        db = AsyncMock(spec=DatabaseManager)
        db.list_admission_webhooks = AsyncMock(return_value=[_webhook_row()])
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = _make_client(db, self.mgr)

        resp = client.get(
            "/api/v1/admission-webhooks",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_delete_webhook(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_admission_webhook = AsyncMock(return_value=True)
        client = _make_client(db, self.mgr)

        resp = client.delete(
            "/api/v1/admission-webhooks/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 204
