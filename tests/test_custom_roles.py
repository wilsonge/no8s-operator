"""Unit tests for custom role CRUD and resource permission checks."""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import auth as auth_module
from auth import AuthManager, check_resource_permission
from db import DatabaseManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def mock_acquire(row=None, *, rows=None, execute_result="DELETE 1"):
    """Build a mock pool.acquire() context manager.

    Supports single fetchrow, fetch list, and execute result.
    """
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetch = AsyncMock(
        return_value=rows if rows is not None else ([row] if row else [])
    )
    conn.execute = AsyncMock(return_value=execute_result)

    @asynccontextmanager
    async def _acquire():
        yield conn

    return _acquire, conn


def _role_row(**kwargs):
    defaults = {
        "id": 1,
        "name": "db-writer",
        "description": "Can write DB resources",
        "system_permissions": [],
        "created_at": None,
        "updated_at": None,
    }
    defaults.update(kwargs)
    return defaults


def _perm_row(**kwargs):
    defaults = {
        "id": 1,
        "role_id": 1,
        "resource_type_name": "DatabaseCluster",
        "resource_type_version": "v1",
        "operations": json.dumps(["CREATE", "READ", "UPDATE", "DELETE"]),
        "created_at": None,
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# DB method tests
# ---------------------------------------------------------------------------


class TestCreateCustomRole:
    async def test_returns_role_with_empty_permissions(self):
        db = make_db()
        acquire, conn = mock_acquire(_role_row())
        db.pool.acquire = acquire

        result = await db.create_custom_role("db-writer", "Can write DB resources")

        assert result["name"] == "db-writer"
        assert result["permissions"] == []

    async def test_create_without_description(self):
        db = make_db()
        acquire, conn = mock_acquire(_role_row(description=None))
        db.pool.acquire = acquire

        result = await db.create_custom_role("db-reader")
        assert result["id"] == 1


class TestGetCustomRole:
    async def test_found_includes_permissions(self):
        db = make_db()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=_role_row())
        conn.fetch = AsyncMock(return_value=[_perm_row()])

        @asynccontextmanager
        async def acquire():
            yield conn

        db.pool.acquire = acquire

        result = await db.get_custom_role(1)
        assert result is not None
        assert result["name"] == "db-writer"
        assert len(result["permissions"]) == 1
        assert isinstance(result["permissions"][0]["operations"], list)

    async def test_not_found(self):
        db = make_db()
        acquire, conn = mock_acquire(None)
        db.pool.acquire = acquire

        result = await db.get_custom_role(999)
        assert result is None


class TestListCustomRoles:
    async def test_returns_roles_with_permissions(self):
        db = make_db()
        conn = AsyncMock()
        conn.fetch = AsyncMock(
            side_effect=[
                [_role_row(id=1), _role_row(id=2, name="dns-reader")],
                [_perm_row(role_id=1)],
                [],
            ]
        )

        @asynccontextmanager
        async def acquire():
            yield conn

        db.pool.acquire = acquire

        results = await db.list_custom_roles()
        assert len(results) == 2
        assert results[0]["name"] == "db-writer"
        assert len(results[0]["permissions"]) == 1
        assert results[1]["name"] == "dns-reader"
        assert len(results[1]["permissions"]) == 0


class TestUpdateCustomRole:
    async def test_update_name(self):
        db = make_db()
        conn = AsyncMock()
        updated_row = _role_row(name="new-name")
        conn.fetchrow = AsyncMock(return_value=updated_row)
        conn.fetch = AsyncMock(return_value=[])

        @asynccontextmanager
        async def acquire():
            yield conn

        db.pool.acquire = acquire

        result = await db.update_custom_role(1, name="new-name")
        assert result["name"] == "new-name"

    async def test_not_found(self):
        db = make_db()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)

        @asynccontextmanager
        async def acquire():
            yield conn

        db.pool.acquire = acquire

        result = await db.update_custom_role(999, name="x")
        assert result is None


class TestDeleteCustomRole:
    async def test_deleted(self):
        db = make_db()
        acquire, conn = mock_acquire(execute_result="DELETE 1")
        db.pool.acquire = acquire

        result = await db.delete_custom_role(1)
        assert result is True

    async def test_not_found(self):
        db = make_db()
        acquire, conn = mock_acquire(execute_result="DELETE 0")
        db.pool.acquire = acquire

        result = await db.delete_custom_role(999)
        assert result is False


class TestAddRolePermission:
    async def test_returns_permission(self):
        db = make_db()
        acquire, conn = mock_acquire(_perm_row())
        db.pool.acquire = acquire

        result = await db.add_role_permission(
            role_id=1,
            resource_type_name="DatabaseCluster",
            resource_type_version="v1",
            operations=["CREATE", "READ"],
        )
        assert result["role_id"] == 1
        assert isinstance(result["operations"], list)


class TestUpdateRolePermission:
    async def test_updates_operations(self):
        db = make_db()
        acquire, conn = mock_acquire(_perm_row(operations=json.dumps(["READ"])))
        db.pool.acquire = acquire

        result = await db.update_role_permission(1, operations=["READ"])
        assert result is not None
        assert "READ" in result["operations"]

    async def test_not_found(self):
        db = make_db()
        acquire, conn = mock_acquire(None)
        db.pool.acquire = acquire

        result = await db.update_role_permission(999, operations=["READ"])
        assert result is None


class TestDeleteRolePermission:
    async def test_deleted(self):
        db = make_db()
        acquire, conn = mock_acquire(execute_result="DELETE 1")
        db.pool.acquire = acquire

        result = await db.delete_role_permission(1)
        assert result is True

    async def test_not_found(self):
        db = make_db()
        acquire, conn = mock_acquire(execute_result="DELETE 0")
        db.pool.acquire = acquire

        result = await db.delete_role_permission(999)
        assert result is False


class TestGetCustomRolePermissions:
    async def test_returns_list(self):
        db = make_db()
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[_perm_row()])

        @asynccontextmanager
        async def acquire():
            yield conn

        db.pool.acquire = acquire

        results = await db.get_custom_role_permissions(1)
        assert len(results) == 1
        assert isinstance(results[0]["operations"], list)


# ---------------------------------------------------------------------------
# check_resource_permission tests
# ---------------------------------------------------------------------------


class TestCheckResourcePermission:
    async def test_admin_always_allowed(self):
        user = {"is_admin": True}
        db = AsyncMock()
        result = await check_resource_permission(user, db, "Any", "v1", "DELETE")
        assert result is True
        db.get_custom_role_permissions.assert_not_called()

    async def test_no_custom_role_denied(self):
        user = {"is_admin": False, "custom_role_id": None}
        db = AsyncMock()
        result = await check_resource_permission(
            user, db, "DatabaseCluster", "v1", "READ"
        )
        assert result is False

    async def test_wildcard_name_and_version_allowed(self):
        user = {"is_admin": False, "custom_role_id": 1}
        db = AsyncMock()
        db.get_custom_role_permissions = AsyncMock(
            return_value=[
                {
                    "resource_type_name": "*",
                    "resource_type_version": "*",
                    "operations": ["CREATE", "READ", "UPDATE", "DELETE"],
                }
            ]
        )
        result = await check_resource_permission(
            user, db, "DatabaseCluster", "v1", "READ"
        )
        assert result is True

    async def test_specific_type_match(self):
        user = {"is_admin": False, "custom_role_id": 1}
        db = AsyncMock()
        db.get_custom_role_permissions = AsyncMock(
            return_value=[
                {
                    "resource_type_name": "DatabaseCluster",
                    "resource_type_version": "v1",
                    "operations": ["READ"],
                }
            ]
        )
        result = await check_resource_permission(
            user, db, "DatabaseCluster", "v1", "READ"
        )
        assert result is True

    async def test_wrong_type_denied(self):
        user = {"is_admin": False, "custom_role_id": 1}
        db = AsyncMock()
        db.get_custom_role_permissions = AsyncMock(
            return_value=[
                {
                    "resource_type_name": "DatabaseCluster",
                    "resource_type_version": "v1",
                    "operations": ["READ"],
                }
            ]
        )
        result = await check_resource_permission(user, db, "DnsRecord", "v1", "READ")
        assert result is False

    async def test_operation_not_in_subset_denied(self):
        user = {"is_admin": False, "custom_role_id": 1}
        db = AsyncMock()
        db.get_custom_role_permissions = AsyncMock(
            return_value=[
                {
                    "resource_type_name": "DatabaseCluster",
                    "resource_type_version": "v1",
                    "operations": ["READ"],
                }
            ]
        )
        result = await check_resource_permission(
            user, db, "DatabaseCluster", "v1", "DELETE"
        )
        assert result is False

    async def test_wildcard_name_specific_version_allowed(self):
        user = {"is_admin": False, "custom_role_id": 1}
        db = AsyncMock()
        db.get_custom_role_permissions = AsyncMock(
            return_value=[
                {
                    "resource_type_name": "*",
                    "resource_type_version": "v1",
                    "operations": ["CREATE", "READ"],
                }
            ]
        )
        result = await check_resource_permission(user, db, "DnsRecord", "v1", "READ")
        assert result is True

    async def test_version_mismatch_denied(self):
        user = {"is_admin": False, "custom_role_id": 1}
        db = AsyncMock()
        db.get_custom_role_permissions = AsyncMock(
            return_value=[
                {
                    "resource_type_name": "DatabaseCluster",
                    "resource_type_version": "v1",
                    "operations": ["READ"],
                }
            ]
        )
        result = await check_resource_permission(
            user, db, "DatabaseCluster", "v2", "READ"
        )
        assert result is False


# ---------------------------------------------------------------------------
# JWT: custom_role_id included in token
# ---------------------------------------------------------------------------


class TestCreateTokenCustomRoleId:
    def test_custom_role_id_in_payload(self):
        mgr = AuthManager(jwt_secret_key="test-secret")
        user = {
            "id": 1,
            "username": "alice",
            "is_admin": False,
            "source": "manual",
            "custom_role_id": 42,
        }
        token = mgr.create_token(user)
        payload = mgr.decode_token(token)
        assert payload["custom_role_id"] == 42
        assert payload["is_admin"] is False

    def test_custom_role_id_none_when_absent(self):
        mgr = AuthManager(jwt_secret_key="test-secret")
        user = {
            "id": 1,
            "username": "alice",
            "is_admin": True,
            "source": "manual",
        }
        token = mgr.create_token(user)
        payload = mgr.decode_token(token)
        assert payload["custom_role_id"] is None
        assert payload["is_admin"] is True


# ---------------------------------------------------------------------------
# API endpoint tests (using HTTPInputPlugin + TestClient)
# ---------------------------------------------------------------------------


def _auth_headers(mgr, user):
    token = mgr.create_token(user)
    return {"Authorization": f"Bearer {token}"}


class TestCustomRoleEndpoints:
    """API tests for /api/v1/custom-roles endpoints."""

    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret")
        auth_module.set_auth_manager(self.mgr)
        self.admin = {
            "id": 1,
            "username": "admin",
            "is_admin": True,
            "source": "manual",
            "custom_role_id": None,
        }

    async def _make_client(self, db_mock):
        from plugins.inputs.http.api import HTTPInputPlugin

        plugin = HTTPInputPlugin()
        await plugin.initialize({"host": "127.0.0.1", "port": 8000})
        plugin.set_db_manager(db_mock)
        plugin._setup_routes()
        return TestClient(plugin.app, raise_server_exceptions=False)

    def _role_response_data(self):
        return {
            "id": 1,
            "name": "db-writer",
            "description": None,
            "system_permissions": [],
            "permissions": [],
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }

    async def test_create_custom_role(self):
        db = AsyncMock(spec=DatabaseManager)
        db.create_custom_role = AsyncMock(return_value=self._role_response_data())
        client = await self._make_client(db)

        resp = client.post(
            "/api/v1/custom-roles",
            json={"name": "db-writer"},
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "db-writer"

    async def test_create_custom_role_requires_admin(self):
        db = AsyncMock(spec=DatabaseManager)
        client = await self._make_client(db)

        non_admin = dict(self.admin, is_admin=False, id=2)
        resp = client.post(
            "/api/v1/custom-roles",
            json={"name": "x"},
            headers=_auth_headers(self.mgr, non_admin),
        )
        assert resp.status_code == 403

    async def test_list_custom_roles(self):
        db = AsyncMock(spec=DatabaseManager)
        db.list_custom_roles = AsyncMock(return_value=[self._role_response_data()])
        client = await self._make_client(db)

        resp = client.get(
            "/api/v1/custom-roles",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_get_custom_role_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_custom_role = AsyncMock(return_value=None)
        client = await self._make_client(db)

        resp = client.get(
            "/api/v1/custom-roles/999",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404

    async def test_delete_custom_role(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_custom_role = AsyncMock(return_value=True)
        client = await self._make_client(db)

        resp = client.delete(
            "/api/v1/custom-roles/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 204

    async def test_delete_custom_role_not_found(self):
        db = AsyncMock(spec=DatabaseManager)
        db.delete_custom_role = AsyncMock(return_value=False)
        client = await self._make_client(db)

        resp = client.delete(
            "/api/v1/custom-roles/999",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 404


class TestResourceEndpointPermissions:
    """Tests that resource endpoints enforce check_resource_permission."""

    def setup_method(self):
        self.mgr = AuthManager(jwt_secret_key="test-secret")
        auth_module.set_auth_manager(self.mgr)
        self.admin = {
            "id": 1,
            "username": "admin",
            "is_admin": True,
            "source": "manual",
            "custom_role_id": None,
        }
        self.custom_user = {
            "id": 2,
            "username": "custom",
            "is_admin": False,
            "source": "manual",
            "custom_role_id": 5,
        }
        self.no_role_user = {
            "id": 3,
            "username": "norole",
            "is_admin": False,
            "source": "manual",
            "custom_role_id": None,
        }

    def _resource(self):
        return {
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

    async def _make_client(self, db_mock):
        from plugins.inputs.http.api import HTTPInputPlugin

        plugin = HTTPInputPlugin()
        await plugin.initialize({"host": "127.0.0.1", "port": 8000})
        plugin.set_db_manager(db_mock)
        plugin._setup_routes()
        return TestClient(plugin.app, raise_server_exceptions=False)

    async def test_get_resource_admin_always_allowed(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=self._resource())
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await self._make_client(db)

        resp = client.get(
            "/api/v1/resources/1",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200

    async def test_get_resource_no_role_denied(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=self._resource())
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await self._make_client(db)

        resp = client.get(
            "/api/v1/resources/1",
            headers=_auth_headers(self.mgr, self.no_role_user),
        )
        assert resp.status_code == 403

    async def test_get_resource_matching_permission_allowed(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=self._resource())
        db.get_custom_role_permissions = AsyncMock(
            return_value=[
                {
                    "resource_type_name": "DatabaseCluster",
                    "resource_type_version": "v1",
                    "operations": ["READ"],
                }
            ]
        )
        client = await self._make_client(db)

        resp = client.get(
            "/api/v1/resources/1",
            headers=_auth_headers(self.mgr, self.custom_user),
        )
        assert resp.status_code == 200

    async def test_get_resource_wrong_operation_denied(self):
        db = AsyncMock(spec=DatabaseManager)
        db.get_resource = AsyncMock(return_value=self._resource())
        # Permission only has CREATE, not READ
        db.get_custom_role_permissions = AsyncMock(
            return_value=[
                {
                    "resource_type_name": "DatabaseCluster",
                    "resource_type_version": "v1",
                    "operations": ["CREATE"],
                }
            ]
        )
        client = await self._make_client(db)

        resp = client.get(
            "/api/v1/resources/1",
            headers=_auth_headers(self.mgr, self.custom_user),
        )
        assert resp.status_code == 403

    async def test_list_resources_filtered_for_custom_user(self):
        db = AsyncMock(spec=DatabaseManager)
        resources = [
            dict(self._resource(), id=1, resource_type_name="DatabaseCluster"),
            dict(
                self._resource(),
                id=2,
                name="my-dns",
                resource_type_name="DnsRecord",
                resource_type_version="v1",
            ),
        ]
        db.list_resources = AsyncMock(return_value=resources)
        # Custom user can only READ DatabaseCluster
        db.get_custom_role_permissions = AsyncMock(
            return_value=[
                {
                    "resource_type_name": "DatabaseCluster",
                    "resource_type_version": "v1",
                    "operations": ["READ"],
                }
            ]
        )
        client = await self._make_client(db)

        resp = client.get(
            "/api/v1/resources",
            headers=_auth_headers(self.mgr, self.custom_user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["resource_type_name"] == "DatabaseCluster"

    async def test_list_resources_admin_gets_all(self):
        db = AsyncMock(spec=DatabaseManager)
        resources = [
            dict(self._resource(), id=1, resource_type_name="DatabaseCluster"),
            dict(
                self._resource(),
                id=2,
                name="my-dns",
                resource_type_name="DnsRecord",
            ),
        ]
        db.list_resources = AsyncMock(return_value=resources)
        db.get_custom_role_permissions = AsyncMock(return_value=[])
        client = await self._make_client(db)

        resp = client.get(
            "/api/v1/resources",
            headers=_auth_headers(self.mgr, self.admin),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2
