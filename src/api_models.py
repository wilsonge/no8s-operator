"""
Shared Pydantic request/response models and validation helpers for the API.

This module contains all model classes and validation utilities used by both
the HTTP input plugin and the management API router.
"""

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from validation import validate_openapi_schema

# Validation constants
# Kubernetes-style name pattern: lowercase alphanumeric, hyphens, max 63 chars
NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
MAX_NAME_LENGTH = 63
MAX_SPEC_SIZE = 1024 * 1024  # 1MB max for spec/plugin_config


def validate_name_format(value: str, field_name: str) -> str:
    """Validate that a name follows Kubernetes naming conventions."""
    if not value:
        raise ValueError(f"{field_name} cannot be empty")
    if len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"{field_name} cannot exceed {MAX_NAME_LENGTH} characters")
    if not NAME_PATTERN.match(value):
        raise ValueError(
            f"{field_name} must consist of lowercase alphanumeric characters or '-', "
            f"must start and end with an alphanumeric character"
        )
    return value


def validate_json_size(
    value: Optional[Dict[str, Any]], field_name: str
) -> Optional[Dict[str, Any]]:
    """Validate that JSON data doesn't exceed size limits."""
    if value is not None:
        json_str = json.dumps(value)
        if len(json_str) > MAX_SPEC_SIZE:
            raise ValueError(
                f"{field_name} exceeds maximum size of {MAX_SPEC_SIZE // 1024}KB"
            )
    return value


# Resource Type models


class ResourceTypeCreate(BaseModel):
    """Request model for creating a resource type."""

    name: str = Field(
        ...,
        description="Resource type name",
        json_schema_extra={"example": "PostgresCluster"},
    )
    version: str = Field(
        ..., description="Version string", json_schema_extra={"example": "v1"}
    )
    resource_schema: Dict[str, Any] = Field(
        ..., alias="schema", description="OpenAPI v3 JSON Schema"
    )
    description: Optional[str] = Field(None, description="Description of resource type")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("resource_schema")
    @classmethod
    def validate_schema(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error = validate_openapi_schema(v)
        if not is_valid:
            raise ValueError(error)
        return v


class ResourceTypeUpdate(BaseModel):
    """Request model for updating a resource type."""

    resource_schema: Optional[Dict[str, Any]] = Field(
        None, alias="schema", description="Updated schema"
    )
    description: Optional[str] = Field(None, description="Updated description")
    status: Optional[str] = Field(None, description="Status (active/deprecated)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Updated metadata")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("resource_schema")
    @classmethod
    def validate_schema(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if v is not None:
            is_valid, error = validate_openapi_schema(v)
            if not is_valid:
                raise ValueError(error)
        return v


class ResourceTypeResponse(BaseModel):
    """Response model for a resource type."""

    id: int
    name: str
    version: str
    resource_schema: Dict[str, Any] = Field(alias="schema")
    description: Optional[str] = None
    status: str = "active"
    metadata: Dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# Resource models


class ResourceCreate(BaseModel):
    """Request model for creating a resource."""

    name: str = Field(
        ..., description="Resource name", json_schema_extra={"example": "my-vpc"}
    )
    resource_type_name: str = Field(
        ...,
        description="Resource type name",
        json_schema_extra={"example": "PostgresCluster"},
    )
    resource_type_version: str = Field(
        ..., description="Resource type version", json_schema_extra={"example": "v1"}
    )
    action_plugin: Optional[str] = Field(
        default=None,
        description="Action plugin to use for reconciliation "
        "(optional if a reconciler plugin handles this resource type)",
    )
    spec: Optional[Dict[str, Any]] = Field(
        default=None, description="Resource specification (plugin-specific)"
    )
    plugin_config: Optional[Dict[str, Any]] = Field(
        default=None, description="Plugin-specific configuration"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Resource metadata"
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_name_format(v, "name")

    @field_validator("spec")
    @classmethod
    def validate_spec_size(
        cls, v: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        return validate_json_size(v, "spec")

    @field_validator("plugin_config")
    @classmethod
    def validate_plugin_config_size(
        cls, v: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        return validate_json_size(v, "plugin_config")


class ResourceUpdate(BaseModel):
    """Request model for updating a resource."""

    spec: Optional[Dict[str, Any]] = Field(
        None, description="Updated resource specification"
    )
    plugin_config: Optional[Dict[str, Any]] = Field(
        None, description="Updated plugin configuration"
    )

    @field_validator("spec")
    @classmethod
    def validate_spec_size(
        cls, v: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        return validate_json_size(v, "spec")

    @field_validator("plugin_config")
    @classmethod
    def validate_plugin_config_size(
        cls, v: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        return validate_json_size(v, "plugin_config")


class ConditionResponse(BaseModel):
    """A single Kubernetes-style status condition."""

    type: str
    status: str
    reason: str
    message: str
    last_transition_time: datetime = Field(alias="lastTransitionTime")
    observed_generation: int = Field(default=0, alias="observedGeneration")

    model_config = {"populate_by_name": True}


class ResourceResponse(BaseModel):
    """Response model for a resource."""

    id: int
    name: str
    resource_type_name: str
    resource_type_version: str
    action_plugin: Optional[str] = None
    status: str
    status_message: Optional[str] = None
    generation: int
    observed_generation: int
    finalizers: List[str] = []
    conditions: List[ConditionResponse] = []
    created_at: datetime
    updated_at: datetime
    last_reconcile_time: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class FinalizersUpdate(BaseModel):
    """Request model for updating finalizers on a resource."""

    add: List[str] = Field(default_factory=list, description="Finalizers to add")
    remove: List[str] = Field(default_factory=list, description="Finalizers to remove")


class ReconciliationHistoryResponse(BaseModel):
    """Response model for reconciliation history."""

    id: int
    resource_id: int
    generation: int
    success: bool
    phase: str
    error_message: Optional[str] = None
    resources_created: int
    resources_updated: int
    resources_deleted: int
    reconcile_time: datetime


# Admission Webhook models


class AdmissionWebhookCreate(BaseModel):
    """Request model for creating an admission webhook."""

    name: str = Field(..., description="Unique webhook name")
    webhook_url: str = Field(..., description="HTTP endpoint to call")
    webhook_type: str = Field(
        ..., description="Webhook type: 'validating' or 'mutating'"
    )
    operations: List[str] = Field(
        ..., description="Operations to intercept: CREATE, UPDATE, DELETE"
    )
    resource_type_name: Optional[str] = Field(
        None, description="Target resource type (null = all types)"
    )
    resource_type_version: Optional[str] = Field(
        None, description="Target version (null = all versions)"
    )
    timeout_seconds: int = Field(default=10, description="HTTP timeout")
    failure_policy: str = Field(
        default="Fail", description="'Fail' or 'Ignore' on webhook error"
    )
    ordering: int = Field(default=0, description="Execution order (lower = first)")

    @field_validator("webhook_type")
    @classmethod
    def validate_webhook_type(cls, v: str) -> str:
        if v not in ("validating", "mutating"):
            raise ValueError("webhook_type must be 'validating' or 'mutating'")
        return v

    @field_validator("operations")
    @classmethod
    def validate_operations(cls, v: List[str]) -> List[str]:
        valid = {"CREATE", "UPDATE", "DELETE"}
        for op in v:
            if op not in valid:
                raise ValueError(
                    f"Invalid operation '{op}'. Must be one of: CREATE, UPDATE, DELETE"
                )
        return v

    @field_validator("failure_policy")
    @classmethod
    def validate_failure_policy(cls, v: str) -> str:
        if v not in ("Fail", "Ignore"):
            raise ValueError("failure_policy must be 'Fail' or 'Ignore'")
        return v


class AdmissionWebhookUpdate(BaseModel):
    """Request model for updating an admission webhook."""

    webhook_url: Optional[str] = None
    webhook_type: Optional[str] = None
    operations: Optional[List[str]] = None
    resource_type_name: Optional[str] = None
    resource_type_version: Optional[str] = None
    timeout_seconds: Optional[int] = None
    failure_policy: Optional[str] = None
    ordering: Optional[int] = None

    @field_validator("webhook_type")
    @classmethod
    def validate_webhook_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("validating", "mutating"):
            raise ValueError("webhook_type must be 'validating' or 'mutating'")
        return v

    @field_validator("operations")
    @classmethod
    def validate_operations(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            valid = {"CREATE", "UPDATE", "DELETE"}
            for op in v:
                if op not in valid:
                    raise ValueError(
                        f"Invalid operation '{op}'. "
                        f"Must be one of: CREATE, UPDATE, DELETE"
                    )
        return v

    @field_validator("failure_policy")
    @classmethod
    def validate_failure_policy(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("Fail", "Ignore"):
            raise ValueError("failure_policy must be 'Fail' or 'Ignore'")
        return v


class AdmissionWebhookResponse(BaseModel):
    """Response model for an admission webhook."""

    id: int
    name: str
    webhook_url: str
    webhook_type: str
    operations: List[str]
    resource_type_name: Optional[str] = None
    resource_type_version: Optional[str] = None
    timeout_seconds: int = 10
    failure_policy: str = "Fail"
    ordering: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PluginInfo(BaseModel):
    """Response model for plugin information."""

    name: str
    version: str


# Auth models


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    is_admin: bool


# User management models


class UserCreate(BaseModel):
    username: str
    password: str = Field(..., min_length=8)
    email: Optional[str] = None
    display_name: Optional[str] = None
    is_admin: bool = False
    custom_role_id: Optional[int] = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        return validate_name_format(v, "username")


class UserUpdate(BaseModel):
    email: Optional[str] = None
    display_name: Optional[str] = None
    is_admin: Optional[bool] = None
    status: Optional[Literal["active", "suspended"]] = None
    custom_role_id: Optional[int] = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    source: str
    is_admin: bool
    status: str
    created_at: datetime
    updated_at: datetime
    last_login_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class LDAPSyncResponse(BaseModel):
    created: int
    updated: int
    total: int


# Custom role models

_CRUD_OPS = Literal["CREATE", "READ", "UPDATE", "DELETE"]


class RolePermissionCreate(BaseModel):
    resource_type_name: str = "*"
    resource_type_version: str = "*"
    operations: List[_CRUD_OPS] = ["CREATE", "READ", "UPDATE", "DELETE"]


class RolePermissionUpdate(BaseModel):
    operations: List[_CRUD_OPS]


class RolePermissionResponse(BaseModel):
    id: int
    role_id: int
    resource_type_name: str
    resource_type_version: str
    operations: List[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


_VALID_SYSTEM_PERMISSIONS = {"view_webhooks", "view_plugins"}


class CustomRoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    system_permissions: List[str] = []
    permissions: List[RolePermissionCreate] = []

    @field_validator("system_permissions")
    @classmethod
    def validate_system_permissions(cls, v: List[str]) -> List[str]:
        unknown = set(v) - _VALID_SYSTEM_PERMISSIONS
        if unknown:
            raise ValueError(
                f"Unknown system_permissions: {unknown}. "
                f"Valid values: {_VALID_SYSTEM_PERMISSIONS}"
            )
        return v


class CustomRoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_permissions: Optional[List[str]] = None

    @field_validator("system_permissions")
    @classmethod
    def validate_system_permissions(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            unknown = set(v) - _VALID_SYSTEM_PERMISSIONS
            if unknown:
                raise ValueError(
                    f"Unknown system_permissions: {unknown}. "
                    f"Valid values: {_VALID_SYSTEM_PERMISSIONS}"
                )
        return v


class CustomRoleResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    system_permissions: List[str] = []
    permissions: List[RolePermissionResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
