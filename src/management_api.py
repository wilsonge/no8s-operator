"""
Management API router.

Exposes all platform/management endpoints as a factory function following the
same pattern as cluster_status.py.  The returned APIRouter can be mounted onto
any FastAPI application via ``app.include_router(router)``.

Endpoints included:
- Auth: POST /api/v1/auth/login, GET /api/v1/auth/me
- Users: CRUD /api/v1/users, POST /api/v1/users/ldap-sync
- Custom Roles: CRUD /api/v1/custom-roles and permissions sub-resource
- Resource Types: CRUD /api/v1/resource-types
- Resource reads + auxiliary: GET/PUT(finalizers)/POST(reconcile)/history/outputs
- Plugin discovery: GET /api/v1/plugins/actions, GET /api/v1/plugins/inputs
- Admission Webhooks: CRUD /api/v1/admission-webhooks
- SSE event streams: GET /api/v1/events, GET /api/v1/resources/{id}/events
"""

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from admission import AdmissionChain
from api_models import (
    AdmissionWebhookCreate,
    AdmissionWebhookResponse,
    AdmissionWebhookUpdate,
    CustomRoleCreate,
    CustomRoleResponse,
    CustomRoleUpdate,
    FinalizersUpdate,
    LDAPSyncResponse,
    LoginRequest,
    LoginResponse,
    PluginInfo,
    ReconciliationHistoryResponse,
    ResourceResponse,
    ResourceTypeCreate,
    ResourceTypeResponse,
    ResourceTypeUpdate,
    RolePermissionCreate,
    RolePermissionResponse,
    RolePermissionUpdate,
    UserCreate,
    UserResponse,
    UserUpdate,
)
from auth import (
    AuthManager,
    check_resource_permission,
    check_system_permission,
    get_current_user,
    require_admin,
)
from db import DatabaseManager
from events import EventBus, ResourceEvent
from ldap_sync import LDAPSyncManager

logger = logging.getLogger(__name__)


def create_management_router(
    db_manager: DatabaseManager,
    auth_manager: AuthManager,
    ldap_manager: LDAPSyncManager,
    event_bus: EventBus,
    admission_chain: AdmissionChain,
) -> APIRouter:
    """Return an APIRouter exposing all management/platform endpoints."""

    router = APIRouter(tags=["management"])

    # ==================== Auth Endpoints ====================

    @router.post("/api/v1/auth/login", response_model=LoginResponse)
    async def login(body: LoginRequest):
        """Issue a JWT for valid credentials."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")
        if not auth_manager:
            raise HTTPException(status_code=503, detail="Auth not configured")

        user = await db_manager.get_user_by_username(body.username)
        if not user or user.get("status") != "active":
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if user["source"] == "manual":
            if not user.get("password_hash") or not auth_manager.verify_password(
                body.password, user["password_hash"]
            ):
                raise HTTPException(status_code=401, detail="Invalid credentials")
        else:
            # LDAP user — bind against directory
            if not ldap_manager or not ldap_manager.authenticate(
                user["ldap_dn"], body.password
            ):
                raise HTTPException(status_code=401, detail="Invalid credentials")

        await db_manager.update_user_last_login(user["id"])
        token = auth_manager.create_token(user)
        return LoginResponse(
            access_token=token,
            username=user["username"],
            is_admin=bool(user.get("is_admin", False)),
        )

    @router.get("/api/v1/auth/me", response_model=UserResponse)
    async def auth_me(current_user: dict = Depends(get_current_user)):
        """Return the currently authenticated user."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")
        user = await db_manager.get_user(int(current_user["sub"]))
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return UserResponse(**user)

    # ==================== User Management Endpoints ====================

    @router.post("/api/v1/users", response_model=UserResponse, status_code=201)
    async def create_user(body: UserCreate, _: dict = Depends(require_admin)):
        """Create a new manual user (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")
        if not auth_manager:
            raise HTTPException(status_code=503, detail="Auth not configured")

        try:
            pw_hash = auth_manager.hash_password(body.password)
            user = await db_manager.create_user(
                username=body.username,
                is_admin=body.is_admin,
                password_hash=pw_hash,
                email=body.email,
                display_name=body.display_name,
                source="manual",
            )
            return UserResponse(**user)
        except Exception as e:
            if "unique constraint" in str(e).lower():
                raise HTTPException(
                    status_code=409,
                    detail=f"User '{body.username}' already exists",
                )
            logger.error(f"Error creating user: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/v1/users", response_model=List[UserResponse])
    async def list_users(
        source: Optional[str] = None,
        is_admin: Optional[bool] = None,
        status: Optional[str] = None,
        limit: int = 100,
        _: dict = Depends(require_admin),
    ):
        """List users with optional filters (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            users = await db_manager.list_users(
                source=source, is_admin=is_admin, status=status, limit=limit
            )
            return [UserResponse(**u) for u in users]
        except Exception as e:
            logger.error(f"Error listing users: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/v1/users/{user_id}", response_model=UserResponse)
    async def get_user(user_id: int, _: dict = Depends(require_admin)):
        """Get a user by ID (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        user = await db_manager.get_user(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return UserResponse(**user)

    @router.put("/api/v1/users/{user_id}", response_model=UserResponse)
    async def update_user(
        user_id: int, body: UserUpdate, _: dict = Depends(require_admin)
    ):
        """Update a user (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            user = await db_manager.update_user(
                user_id,
                email=body.email,
                display_name=body.display_name,
                is_admin=body.is_admin,
                status=body.status,
                custom_role_id=body.custom_role_id,
            )
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            return UserResponse(**user)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating user: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/api/v1/users/{user_id}", status_code=204)
    async def delete_user(user_id: int, _: dict = Depends(require_admin)):
        """Suspend a user (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        deleted = await db_manager.delete_user(user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="User not found")
        return None

    @router.post("/api/v1/users/ldap-sync", response_model=LDAPSyncResponse)
    async def ldap_sync(_: dict = Depends(require_admin)):
        """Trigger an LDAP sync (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")
        if not ldap_manager or not ldap_manager.is_configured():
            raise HTTPException(status_code=503, detail="LDAP is not configured")

        try:
            stats = await ldap_manager.sync_to_db(db_manager)
            return LDAPSyncResponse(**stats)
        except Exception as e:
            logger.error(f"Error during LDAP sync: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ==================== Custom Role Endpoints ====================

    @router.post(
        "/api/v1/custom-roles",
        response_model=CustomRoleResponse,
        status_code=201,
    )
    async def create_custom_role(
        body: CustomRoleCreate, _: dict = Depends(require_admin)
    ):
        """Create a custom role (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            role = await db_manager.create_custom_role(
                name=body.name,
                description=body.description,
                system_permissions=body.system_permissions,
            )
            for perm in body.permissions:
                p = await db_manager.add_role_permission(
                    role_id=role["id"],
                    resource_type_name=perm.resource_type_name,
                    resource_type_version=perm.resource_type_version,
                    operations=list(perm.operations),
                )
                role["permissions"].append(p)
            return CustomRoleResponse(**role)
        except Exception as e:
            if "unique constraint" in str(e).lower():
                raise HTTPException(
                    status_code=409,
                    detail=f"Role '{body.name}' already exists",
                )
            logger.error(f"Error creating custom role: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/v1/custom-roles", response_model=List[CustomRoleResponse])
    async def list_custom_roles(_: dict = Depends(require_admin)):
        """List custom roles (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            roles = await db_manager.list_custom_roles()
            return [CustomRoleResponse(**r) for r in roles]
        except Exception as e:
            logger.error(f"Error listing custom roles: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/v1/custom-roles/{role_id}", response_model=CustomRoleResponse)
    async def get_custom_role(role_id: int, _: dict = Depends(require_admin)):
        """Get a custom role by ID (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        role = await db_manager.get_custom_role(role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        return CustomRoleResponse(**role)

    @router.put("/api/v1/custom-roles/{role_id}", response_model=CustomRoleResponse)
    async def update_custom_role(
        role_id: int, body: CustomRoleUpdate, _: dict = Depends(require_admin)
    ):
        """Update a custom role's name/description (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            role = await db_manager.update_custom_role(
                role_id,
                name=body.name,
                description=body.description,
                system_permissions=body.system_permissions,
            )
            if not role:
                raise HTTPException(status_code=404, detail="Role not found")
            return CustomRoleResponse(**role)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating custom role: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/api/v1/custom-roles/{role_id}", status_code=204)
    async def delete_custom_role(role_id: int, _: dict = Depends(require_admin)):
        """Delete a custom role (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        deleted = await db_manager.delete_custom_role(role_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Role not found")
        return None

    @router.post(
        "/api/v1/custom-roles/{role_id}/permissions",
        response_model=RolePermissionResponse,
        status_code=201,
    )
    async def add_role_permission(
        role_id: int,
        body: RolePermissionCreate,
        _: dict = Depends(require_admin),
    ):
        """Add a permission to a custom role (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        role = await db_manager.get_custom_role(role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")

        try:
            perm = await db_manager.add_role_permission(
                role_id=role_id,
                resource_type_name=body.resource_type_name,
                resource_type_version=body.resource_type_version,
                operations=list(body.operations),
            )
            return RolePermissionResponse(**perm)
        except Exception as e:
            if "unique constraint" in str(e).lower():
                raise HTTPException(
                    status_code=409,
                    detail="Permission for this resource type/version already exists",
                )
            logger.error(f"Error adding role permission: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put(
        "/api/v1/custom-roles/{role_id}/permissions/{perm_id}",
        response_model=RolePermissionResponse,
    )
    async def update_role_permission(
        role_id: int,
        perm_id: int,
        body: RolePermissionUpdate,
        _: dict = Depends(require_admin),
    ):
        """Update a permission's operations (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            perm = await db_manager.update_role_permission(
                perm_id, operations=list(body.operations)
            )
            if not perm:
                raise HTTPException(status_code=404, detail="Permission not found")
            return RolePermissionResponse(**perm)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating role permission: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete(
        "/api/v1/custom-roles/{role_id}/permissions/{perm_id}",
        status_code=204,
    )
    async def delete_role_permission(
        role_id: int,
        perm_id: int,
        _: dict = Depends(require_admin),
    ):
        """Remove a permission from a custom role (admin only)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        deleted = await db_manager.delete_role_permission(perm_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Permission not found")
        return None

    # ==================== Resource Type Endpoints ====================

    @router.post(
        "/api/v1/resource-types",
        response_model=ResourceTypeResponse,
        status_code=201,
    )
    async def create_resource_type(
        rt: ResourceTypeCreate, _: dict = Depends(require_admin)
    ):
        """Create a new resource type."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            rt_id = await db_manager.create_resource_type(
                name=rt.name,
                version=rt.version,
                schema=rt.resource_schema,
                description=rt.description,
                metadata=rt.metadata,
            )
            created = await db_manager.get_resource_type(rt_id)
            return ResourceTypeResponse(**created)
        except Exception as e:
            if "unique constraint" in str(e).lower():
                raise HTTPException(
                    status_code=409,
                    detail=f"Resource type {rt.name}/{rt.version} already exists",
                )
            logger.error(f"Error creating resource type: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/v1/resource-types", response_model=List[ResourceTypeResponse])
    async def list_resource_types(
        name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        _: dict = Depends(get_current_user),
    ):
        """List resource types with optional filters."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            rts = await db_manager.list_resource_types(
                name=name, status=status, limit=limit
            )
            return [ResourceTypeResponse(**rt) for rt in rts]
        except Exception as e:
            logger.error(f"Error listing resource types: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get(
        "/api/v1/resource-types/{resource_type_id}",
        response_model=ResourceTypeResponse,
    )
    async def get_resource_type_by_id(
        resource_type_id: int, _: dict = Depends(get_current_user)
    ):
        """Get a resource type by ID."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            rt = await db_manager.get_resource_type(resource_type_id)
            if not rt:
                raise HTTPException(status_code=404, detail="Resource type not found")
            return ResourceTypeResponse(**rt)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting resource type: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get(
        "/api/v1/resource-types/{name}/{version}",
        response_model=ResourceTypeResponse,
    )
    async def get_resource_type_by_name_version(
        name: str, version: str, _: dict = Depends(get_current_user)
    ):
        """Get a resource type by name and version."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            rt = await db_manager.get_resource_type_by_name_version(name, version)
            if not rt:
                raise HTTPException(status_code=404, detail="Resource type not found")
            return ResourceTypeResponse(**rt)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting resource type: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put(
        "/api/v1/resource-types/{resource_type_id}",
        response_model=ResourceTypeResponse,
    )
    async def update_resource_type(
        resource_type_id: int,
        update: ResourceTypeUpdate,
        _: dict = Depends(require_admin),
    ):
        """Update a resource type."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            await db_manager.update_resource_type(
                resource_type_id=resource_type_id,
                schema=update.resource_schema,
                description=update.description,
                status=update.status,
                metadata=update.metadata,
            )
            updated = await db_manager.get_resource_type(resource_type_id)
            if not updated:
                raise HTTPException(status_code=404, detail="Resource type not found")
            return ResourceTypeResponse(**updated)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating resource type: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/api/v1/resource-types/{resource_type_id}", status_code=204)
    async def delete_resource_type(
        resource_type_id: int, _: dict = Depends(require_admin)
    ):
        """Delete a resource type (fails if resources still reference it)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            deleted = await db_manager.delete_resource_type(resource_type_id)
            if not deleted:
                raise HTTPException(
                    status_code=409,
                    detail="Cannot delete: resources still reference this type",
                )
            return None
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting resource type: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ==================== Resource Read Endpoints ====================

    @router.get("/api/v1/resources", response_model=List[ResourceResponse])
    async def list_resources(
        status: Optional[str] = None,
        action_plugin: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        limit: int = 100,
    ):
        """List all resources with optional filters."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            resources = await db_manager.list_resources(
                status=status,
                action_plugin=action_plugin,
                limit=limit,
            )
            if current_user.get("role") != "admin":
                filtered = []
                for r in resources:
                    if await check_resource_permission(
                        current_user,
                        db_manager,
                        r["resource_type_name"],
                        r["resource_type_version"],
                        "READ",
                    ):
                        filtered.append(r)
                resources = filtered
            return [ResourceResponse(**r) for r in resources]
        except Exception as e:
            logger.error(f"Error listing resources: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/v1/resources/{resource_id}", response_model=ResourceResponse)
    async def get_resource_by_id(
        resource_id: int,
        current_user: dict = Depends(get_current_user),
    ):
        """Get a resource by ID."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            resource = await db_manager.get_resource(resource_id)
            if not resource:
                raise HTTPException(status_code=404, detail="Resource not found")
            allowed = await check_resource_permission(
                current_user,
                db_manager,
                resource["resource_type_name"],
                resource["resource_type_version"],
                "READ",
            )
            if not allowed:
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            return ResourceResponse(**resource)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting resource: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get(
        "/api/v1/resources/by-name/{resource_type_name}/{resource_type_version}/{name}",
        response_model=ResourceResponse,
    )
    async def get_resource_by_name(
        resource_type_name: str,
        resource_type_version: str,
        name: str,
        current_user: dict = Depends(get_current_user),
    ):
        """Get a resource by resource type and name."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        allowed = await check_resource_permission(
            current_user,
            db_manager,
            resource_type_name,
            resource_type_version,
            "READ",
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        try:
            resource = await db_manager.get_resource_by_name(
                name, resource_type_name, resource_type_version
            )
            if not resource:
                raise HTTPException(status_code=404, detail="Resource not found")
            return ResourceResponse(**resource)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting resource: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/api/v1/resources/{resource_id}/finalizers")
    async def update_finalizers(
        resource_id: int,
        update: FinalizersUpdate,
        current_user: dict = Depends(get_current_user),
    ):
        """Add or remove finalizers from a resource."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            resource = await db_manager.get_resource(resource_id)
            if not resource:
                raise HTTPException(status_code=404, detail="Resource not found")

            allowed = await check_resource_permission(
                current_user,
                db_manager,
                resource["resource_type_name"],
                resource["resource_type_version"],
                "UPDATE",
            )
            if not allowed:
                raise HTTPException(status_code=403, detail="Insufficient permissions")

            for finalizer in update.add:
                await db_manager.add_finalizer(resource_id, finalizer)
            for finalizer in update.remove:
                await db_manager.remove_finalizer(resource_id, finalizer)

            # If deleting and all finalizers cleared, hard-delete
            if resource.get("status") == "deleting":
                remaining = await db_manager.get_finalizers(resource_id)
                if not remaining:
                    await db_manager.hard_delete_resource(resource_id)
                    return {
                        "message": "All finalizers removed, " "resource deleted",
                        "resource_id": resource_id,
                    }

            updated = await db_manager.get_resource(resource_id)
            if not updated:
                raise HTTPException(status_code=404, detail="Resource not found")
            return ResourceResponse(**updated)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating finalizers: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/v1/resources/{resource_id}/reconcile", status_code=202)
    async def trigger_reconciliation(
        resource_id: int,
        current_user: dict = Depends(get_current_user),
    ):
        """Manually trigger reconciliation for a resource."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            resource = await db_manager.get_resource(resource_id)
            if not resource:
                raise HTTPException(status_code=404, detail="Resource not found")
            allowed = await check_resource_permission(
                current_user,
                db_manager,
                resource["resource_type_name"],
                resource["resource_type_version"],
                "UPDATE",
            )
            if not allowed:
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            await db_manager.mark_resource_for_reconciliation(resource_id)
            return {
                "message": "Reconciliation triggered",
                "resource_id": resource_id,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error triggering reconciliation: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get(
        "/api/v1/resources/{resource_id}/history",
        response_model=List[ReconciliationHistoryResponse],
    )
    async def get_reconciliation_history(
        resource_id: int,
        limit: int = 10,
        current_user: dict = Depends(get_current_user),
    ):
        """Get reconciliation history for a resource."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            resource = await db_manager.get_resource(resource_id)
            if not resource:
                raise HTTPException(status_code=404, detail="Resource not found")
            allowed = await check_resource_permission(
                current_user,
                db_manager,
                resource["resource_type_name"],
                resource["resource_type_version"],
                "READ",
            )
            if not allowed:
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            history = await db_manager.get_reconciliation_history(resource_id, limit)
            return [ReconciliationHistoryResponse(**record) for record in history]
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting reconciliation history: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/api/v1/resources/{resource_id}/outputs")
    async def get_resource_outputs(
        resource_id: int,
        current_user: dict = Depends(get_current_user),
    ):
        """Get action outputs for a resource."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            resource = await db_manager.get_resource(resource_id)
            if not resource:
                raise HTTPException(status_code=404, detail="Resource not found")

            allowed = await check_resource_permission(
                current_user,
                db_manager,
                resource["resource_type_name"],
                resource["resource_type_version"],
                "READ",
            )
            if not allowed:
                raise HTTPException(status_code=403, detail="Insufficient permissions")

            return {"outputs": resource.get("outputs", {})}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting outputs: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Plugin discovery endpoints
    @router.get("/api/v1/plugins/actions", response_model=List[PluginInfo])
    async def list_action_plugins(current_user: dict = Depends(get_current_user)):
        """List available action plugins (requires view_plugins permission)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")
        allowed = await check_system_permission(
            current_user, db_manager, "view_plugins"
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        from plugins.registry import get_registry

        registry = get_registry()
        plugins = []
        for name in registry.list_action_plugins():
            info = registry.get_action_plugin_info(name)
            if info:
                plugins.append(PluginInfo(**info))
        return plugins

    @router.get("/api/v1/plugins/inputs", response_model=List[PluginInfo])
    async def list_input_plugins(current_user: dict = Depends(get_current_user)):
        """List available input plugins (requires view_plugins permission)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")
        allowed = await check_system_permission(
            current_user, db_manager, "view_plugins"
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        from plugins.registry import get_registry

        registry = get_registry()
        plugins = []
        for name in registry.list_input_plugins():
            info = registry.get_input_plugin_info(name)
            if info:
                plugins.append(PluginInfo(**info))
        return plugins

    # ==================== Admission Webhook Endpoints ====================

    @router.post(
        "/api/v1/admission-webhooks",
        response_model=AdmissionWebhookResponse,
        status_code=201,
    )
    async def create_admission_webhook(
        webhook: AdmissionWebhookCreate, _: dict = Depends(require_admin)
    ):
        """Register an admission webhook."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            wh_id = await db_manager.create_admission_webhook(
                name=webhook.name,
                webhook_url=webhook.webhook_url,
                webhook_type=webhook.webhook_type,
                operations=webhook.operations,
                resource_type_name=webhook.resource_type_name,
                resource_type_version=webhook.resource_type_version,
                timeout_seconds=webhook.timeout_seconds,
                failure_policy=webhook.failure_policy,
                ordering=webhook.ordering,
            )
            created = await db_manager.get_admission_webhook(wh_id)
            return AdmissionWebhookResponse(**created)
        except Exception as e:
            if "unique constraint" in str(e).lower():
                raise HTTPException(
                    status_code=409,
                    detail=f"Admission webhook '{webhook.name}' " f"already exists",
                )
            logger.error(f"Error creating admission webhook: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get(
        "/api/v1/admission-webhooks",
        response_model=List[AdmissionWebhookResponse],
    )
    async def list_admission_webhooks(
        resource_type_name: Optional[str] = None,
        resource_type_version: Optional[str] = None,
        webhook_type: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
    ):
        """List admission webhooks (requires view_webhooks permission)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")
        allowed = await check_system_permission(
            current_user, db_manager, "view_webhooks"
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        try:
            webhooks = await db_manager.list_admission_webhooks(
                resource_type_name=resource_type_name,
                resource_type_version=resource_type_version,
                webhook_type=webhook_type,
            )
            return [AdmissionWebhookResponse(**w) for w in webhooks]
        except Exception as e:
            logger.error(f"Error listing admission webhooks: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get(
        "/api/v1/admission-webhooks/{webhook_id}",
        response_model=AdmissionWebhookResponse,
    )
    async def get_admission_webhook(
        webhook_id: int, current_user: dict = Depends(get_current_user)
    ):
        """Get an admission webhook by ID (requires view_webhooks permission)."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")
        allowed = await check_system_permission(
            current_user, db_manager, "view_webhooks"
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        try:
            webhook = await db_manager.get_admission_webhook(webhook_id)
            if not webhook:
                raise HTTPException(
                    status_code=404,
                    detail="Admission webhook not found",
                )
            return AdmissionWebhookResponse(**webhook)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting admission webhook: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put(
        "/api/v1/admission-webhooks/{webhook_id}",
        response_model=AdmissionWebhookResponse,
    )
    async def update_admission_webhook(
        webhook_id: int,
        update: AdmissionWebhookUpdate,
        _: dict = Depends(require_admin),
    ):
        """Update an admission webhook."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            await db_manager.update_admission_webhook(
                webhook_id=webhook_id,
                webhook_url=update.webhook_url,
                webhook_type=update.webhook_type,
                operations=update.operations,
                resource_type_name=update.resource_type_name,
                resource_type_version=update.resource_type_version,
                timeout_seconds=update.timeout_seconds,
                failure_policy=update.failure_policy,
                ordering=update.ordering,
            )
            updated = await db_manager.get_admission_webhook(webhook_id)
            if not updated:
                raise HTTPException(
                    status_code=404,
                    detail="Admission webhook not found",
                )
            return AdmissionWebhookResponse(**updated)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating admission webhook: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/api/v1/admission-webhooks/{webhook_id}", status_code=204)
    async def delete_admission_webhook(
        webhook_id: int, _: dict = Depends(require_admin)
    ):
        """Delete an admission webhook."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")

        try:
            deleted = await db_manager.delete_admission_webhook(webhook_id)
            if not deleted:
                raise HTTPException(
                    status_code=404,
                    detail="Admission webhook not found",
                )
            return None
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting admission webhook: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ==================== Event Streaming Endpoints ====================

    @router.get("/api/v1/events")
    async def stream_all_events(
        resource_type: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
    ):
        """SSE stream of all resource events.

        Admins see all events. Non-admins see only events for resource
        types they have READ permission on via their custom role.
        Optionally filter by resource type name.
        """
        if not event_bus:
            raise HTTPException(
                status_code=503,
                detail="Event streaming not available",
            )

        if current_user.get("is_admin"):
            # Admins: apply only the optional resource_type filter
            if resource_type:
                rt_name = resource_type

                def filter_fn(event: ResourceEvent) -> bool:
                    return event.resource_type_name == rt_name

            else:
                filter_fn = None
        else:
            # Non-admins: filter by allowed resource types from custom role
            custom_role_id = current_user.get("custom_role_id")
            if custom_role_id and db_manager:
                perms = await db_manager.get_custom_role_permissions(custom_role_id)
                wildcard = any(
                    p["resource_type_name"] == "*" and "READ" in p["operations"]
                    for p in perms
                )
                if wildcard:
                    allowed_types: Optional[set] = None  # all types allowed
                else:
                    allowed_types = {
                        p["resource_type_name"]
                        for p in perms
                        if "READ" in p["operations"]
                    }
            else:
                allowed_types = set()  # no permissions → empty stream

            rt_filter = resource_type

            def filter_fn(event: ResourceEvent) -> bool:  # type: ignore[misc]
                if (
                    allowed_types is not None
                    and event.resource_type_name not in allowed_types
                ):
                    return False
                if rt_filter and event.resource_type_name != rt_filter:
                    return False
                return True

        subscriber_id, subscription = await event_bus.subscribe(filter_fn)

        async def event_generator():
            try:
                async for event in subscription:
                    yield event.to_sse()
            except asyncio.CancelledError:
                pass
            finally:
                await event_bus.unsubscribe(subscriber_id)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/api/v1/resources/{resource_id}/events")
    async def stream_resource_events(
        resource_id: int,
        current_user: dict = Depends(get_current_user),
    ):
        """SSE stream for a specific resource."""
        if not db_manager:
            raise HTTPException(status_code=503, detail="Database not available")
        if not event_bus:
            raise HTTPException(
                status_code=503,
                detail="Event streaming not available",
            )

        resource = await db_manager.get_resource(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        allowed = await check_resource_permission(
            current_user,
            db_manager,
            resource["resource_type_name"],
            resource["resource_type_version"],
            "READ",
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        def filter_fn(event: ResourceEvent) -> bool:
            return event.resource_id == resource_id

        subscriber_id, subscription = await event_bus.subscribe(filter_fn)

        async def event_generator():
            try:
                async for event in subscription:
                    yield event.to_sse()
            except asyncio.CancelledError:
                pass
            finally:
                await event_bus.unsubscribe(subscriber_id)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router
