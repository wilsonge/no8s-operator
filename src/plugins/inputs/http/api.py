"""
HTTP Input Plugin - REST API for resource management.

This plugin provides a FastAPI-based REST API for creating, updating,
and managing infrastructure resources.
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from plugins.base import ResourceSpec
from plugins.inputs.base import InputPlugin, ResourceCallback, validate_action_plugin
from validation import validate_openapi_schema, validate_spec_against_schema

logger = logging.getLogger(__name__)

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

    name: str = Field(..., description="Resource type name", example="TerraformModule")
    version: str = Field(..., description="Version string", example="v1")
    schema: Dict[str, Any] = Field(..., description="OpenAPI v3 JSON Schema")
    description: Optional[str] = Field(None, description="Description of resource type")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")

    @field_validator("schema")
    @classmethod
    def validate_schema(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error = validate_openapi_schema(v)
        if not is_valid:
            raise ValueError(error)
        return v


class ResourceTypeUpdate(BaseModel):
    """Request model for updating a resource type."""

    schema: Optional[Dict[str, Any]] = Field(None, description="Updated schema")
    description: Optional[str] = Field(None, description="Updated description")
    status: Optional[str] = Field(None, description="Status (active/deprecated)")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Updated metadata")

    @field_validator("schema")
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
    schema: Dict[str, Any]
    description: Optional[str] = None
    status: str = "active"
    metadata: Dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Resource models


class ResourceCreate(BaseModel):
    """Request model for creating a resource."""

    name: str = Field(..., description="Resource name", example="my-vpc")
    resource_type_name: str = Field(
        ..., description="Resource type name", example="TerraformModule"
    )
    resource_type_version: str = Field(
        ..., description="Resource type version", example="v1"
    )
    action_plugin: str = Field(
        default="terraform", description="Action plugin to use for reconciliation"
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


class ResourceResponse(BaseModel):
    """Response model for a resource."""

    id: int
    name: str
    resource_type_name: str
    resource_type_version: str
    action_plugin: str = "terraform"
    status: str
    status_message: Optional[str] = None
    generation: int
    observed_generation: int
    finalizers: List[str] = []
    created_at: datetime
    updated_at: datetime
    last_reconcile_time: Optional[datetime] = None

    class Config:
        from_attributes = True


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


class PluginInfo(BaseModel):
    """Response model for plugin information."""

    name: str
    version: str


class HTTPInputPlugin(InputPlugin):
    """
    Input plugin that provides a REST API for resource management.

    Implements the standard InputPlugin interface using FastAPI.
    """

    def __init__(self):
        self.app: Optional[FastAPI] = None
        self.host: str = "0.0.0.0"
        self.port: int = 8000
        self.server = None
        self._on_resource_event: Optional[ResourceCallback] = None
        self._db_manager = None
        self._config: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "http"

    @property
    def version(self) -> str:
        return "1.0.0"

    @classmethod
    def load_config_from_env(cls) -> Dict[str, Any]:
        """Load HTTP plugin configuration from environment variables."""
        return {
            "host": os.getenv("API_HOST", "0.0.0.0"),
            "port": int(os.getenv("API_PORT", "8000")),
        }

    async def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize the HTTP API plugin."""
        self._config = config
        self.host = config.get("host", "0.0.0.0")
        self.port = config.get("port", 8000)

        # Create FastAPI app
        self.app = FastAPI(
            title="Infrastructure Controller API",
            description="Plugin-based controller for managing infrastructure resources",
            version="2.0.0",
        )

        logger.info(f"HTTP input plugin initialized on {self.host}:{self.port}")

    def set_db_manager(self, db_manager) -> None:
        """Set the database manager instance."""
        self._db_manager = db_manager

    def _setup_routes(self) -> None:
        """
        Set up all FastAPI routes for the REST API.

        Configures the following endpoint groups:
        - Health check: GET /
        - Resource Types CRUD: /api/v1/resource-types
        - Resources CRUD: /api/v1/resources
        - Resource by name: /api/v1/resources/by-name/{name}
        - Reconciliation: POST /api/v1/resources/{id}/reconcile
        - History: GET /api/v1/resources/{id}/history
        - Outputs: GET /api/v1/resources/{id}/outputs
        - Plugin discovery: /api/v1/plugins/{actions,inputs}

        Raises:
            RuntimeError: If the FastAPI app has not been initialized
        """
        if not self.app:
            raise RuntimeError("App not initialized")

        @self.app.get("/")
        async def health_check():
            """Health check endpoint."""
            return {"status": "ok", "service": "infrastructure-controller"}

        # ==================== Resource Type Endpoints ====================

        @self.app.post(
            "/api/v1/resource-types",
            response_model=ResourceTypeResponse,
            status_code=201,
        )
        async def create_resource_type(rt: ResourceTypeCreate):
            """Create a new resource type."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                rt_id = await self._db_manager.create_resource_type(
                    name=rt.name,
                    version=rt.version,
                    schema=rt.schema,
                    description=rt.description,
                    metadata=rt.metadata,
                )
                created = await self._db_manager.get_resource_type(rt_id)
                return ResourceTypeResponse(**created)
            except Exception as e:
                if "unique constraint" in str(e).lower():
                    raise HTTPException(
                        status_code=409,
                        detail=f"Resource type {rt.name}/{rt.version} already exists",
                    )
                logger.error(f"Error creating resource type: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get(
            "/api/v1/resource-types", response_model=List[ResourceTypeResponse]
        )
        async def list_resource_types(
            name: Optional[str] = None,
            status: Optional[str] = None,
            limit: int = 100,
        ):
            """List resource types with optional filters."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                rts = await self._db_manager.list_resource_types(
                    name=name, status=status, limit=limit
                )
                return [ResourceTypeResponse(**rt) for rt in rts]
            except Exception as e:
                logger.error(f"Error listing resource types: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get(
            "/api/v1/resource-types/{resource_type_id}",
            response_model=ResourceTypeResponse,
        )
        async def get_resource_type_by_id(resource_type_id: int):
            """Get a resource type by ID."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                rt = await self._db_manager.get_resource_type(resource_type_id)
                if not rt:
                    raise HTTPException(
                        status_code=404, detail="Resource type not found"
                    )
                return ResourceTypeResponse(**rt)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting resource type: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get(
            "/api/v1/resource-types/{name}/{version}",
            response_model=ResourceTypeResponse,
        )
        async def get_resource_type_by_name_version(name: str, version: str):
            """Get a resource type by name and version."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                rt = await self._db_manager.get_resource_type_by_name_version(
                    name, version
                )
                if not rt:
                    raise HTTPException(
                        status_code=404, detail="Resource type not found"
                    )
                return ResourceTypeResponse(**rt)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting resource type: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put(
            "/api/v1/resource-types/{resource_type_id}",
            response_model=ResourceTypeResponse,
        )
        async def update_resource_type(
            resource_type_id: int, update: ResourceTypeUpdate
        ):
            """Update a resource type."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                await self._db_manager.update_resource_type(
                    resource_type_id=resource_type_id,
                    schema=update.schema,
                    description=update.description,
                    status=update.status,
                    metadata=update.metadata,
                )
                updated = await self._db_manager.get_resource_type(resource_type_id)
                if not updated:
                    raise HTTPException(
                        status_code=404, detail="Resource type not found"
                    )
                return ResourceTypeResponse(**updated)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error updating resource type: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/api/v1/resource-types/{resource_type_id}", status_code=204)
        async def delete_resource_type(resource_type_id: int):
            """Delete a resource type (fails if resources still reference it)."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                deleted = await self._db_manager.delete_resource_type(resource_type_id)
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

        # ==================== Resource Endpoints ====================

        @self.app.post(
            "/api/v1/resources", response_model=ResourceResponse, status_code=201
        )
        async def create_resource(resource: ResourceCreate):
            """Create a new resource."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            # Validate action plugin exists
            validation = validate_action_plugin(resource.action_plugin)
            if not validation.is_valid:
                raise HTTPException(status_code=400, detail=validation.error_message)

            try:
                # Validate spec is present
                if not resource.spec:
                    raise HTTPException(
                        status_code=400,
                        detail="spec is required",
                    )

                # Fetch resource type and validate spec against schema
                rt = await self._db_manager.get_resource_type_by_name_version(
                    resource.resource_type_name, resource.resource_type_version
                )
                if not rt:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Resource type {resource.resource_type_name}/"
                        f"{resource.resource_type_version} not found",
                    )

                # Validate spec against resource type schema
                is_valid, error = validate_spec_against_schema(
                    resource.spec, rt["schema"]
                )
                if not is_valid:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Spec validation failed: {error}",
                    )

                resource_id = await self._db_manager.create_resource(
                    name=resource.name,
                    resource_type_name=resource.resource_type_name,
                    resource_type_version=resource.resource_type_version,
                    action_plugin=resource.action_plugin,
                    spec=resource.spec,
                    plugin_config=resource.plugin_config,
                    metadata=resource.metadata,
                    finalizers=[resource.action_plugin],
                )

                # Notify controller of new resource
                if self._on_resource_event:
                    spec = ResourceSpec(
                        name=resource.name,
                        action_plugin=resource.action_plugin,
                        spec=resource.spec,
                        plugin_config=resource.plugin_config,
                        metadata=resource.metadata,
                    )
                    await self._on_resource_event("created", spec)

                created = await self._db_manager.get_resource(resource_id)
                return ResourceResponse(**created)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error creating resource: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/v1/resources", response_model=List[ResourceResponse])
        async def list_resources(
            status: Optional[str] = None,
            action_plugin: Optional[str] = None,
            limit: int = 100,
        ):
            """List all resources with optional filters."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                resources = await self._db_manager.list_resources(
                    status=status,
                    action_plugin=action_plugin,
                    limit=limit,
                )
                return [ResourceResponse(**r) for r in resources]
            except Exception as e:
                logger.error(f"Error listing resources: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get(
            "/api/v1/resources/{resource_id}", response_model=ResourceResponse
        )
        async def get_resource_by_id(resource_id: int):
            """Get a resource by ID."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                resource = await self._db_manager.get_resource(resource_id)
                if not resource:
                    raise HTTPException(status_code=404, detail="Resource not found")
                return ResourceResponse(**resource)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting resource: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get(
            "/api/v1/resources/by-name/{resource_type_name}/{resource_type_version}/{name}",
            response_model=ResourceResponse,
        )
        async def get_resource_by_name(
            resource_type_name: str, resource_type_version: str, name: str
        ):
            """Get a resource by resource type and name."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                resource = await self._db_manager.get_resource_by_name(
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

        @self.app.put(
            "/api/v1/resources/{resource_id}", response_model=ResourceResponse
        )
        async def update_resource(resource_id: int, update: ResourceUpdate):
            """Update a resource's specification."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                # Get current resource to fetch resource type
                current = await self._db_manager.get_resource(resource_id)
                if not current:
                    raise HTTPException(status_code=404, detail="Resource not found")

                # If spec is being updated, validate against schema
                if update.spec is not None:
                    rt = await self._db_manager.get_resource_type_by_name_version(
                        current["resource_type_name"],
                        current["resource_type_version"],
                    )
                    if rt:
                        is_valid, error = validate_spec_against_schema(
                            update.spec, rt["schema"]
                        )
                        if not is_valid:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Spec validation failed: {error}",
                            )

                await self._db_manager.update_resource(
                    resource_id=resource_id,
                    spec=update.spec,
                    plugin_config=update.plugin_config,
                )

                updated = await self._db_manager.get_resource(resource_id)

                # Notify controller
                if self._on_resource_event:
                    spec = ResourceSpec(
                        name=updated["name"],
                        action_plugin=updated.get("action_plugin", "terraform"),
                        spec=updated.get("spec", {}),
                        plugin_config=updated.get("plugin_config"),
                    )
                    await self._on_resource_event("updated", spec)

                return ResourceResponse(**updated)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error updating resource: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/api/v1/resources/{resource_id}", status_code=202)
        async def delete_resource(resource_id: int):
            """Delete a resource (triggers destroy)."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                resource = await self._db_manager.get_resource(resource_id)
                if not resource:
                    raise HTTPException(status_code=404, detail="Resource not found")

                await self._db_manager.delete_resource(resource_id)

                # Notify controller
                if self._on_resource_event:
                    spec = ResourceSpec(
                        name=resource["name"],
                        action_plugin=resource.get("action_plugin", "terraform"),
                        spec=resource.get("spec", {}),
                    )
                    await self._on_resource_event("deleted", spec)

                return {
                    "message": "Resource marked for deletion",
                    "resource_id": resource_id,
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error deleting resource: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put("/api/v1/resources/{resource_id}/finalizers")
        async def update_finalizers(resource_id: int, update: FinalizersUpdate):
            """Add or remove finalizers from a resource."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                resource = await self._db_manager.get_resource(resource_id)
                if not resource:
                    raise HTTPException(status_code=404, detail="Resource not found")

                for finalizer in update.add:
                    await self._db_manager.add_finalizer(resource_id, finalizer)
                for finalizer in update.remove:
                    await self._db_manager.remove_finalizer(resource_id, finalizer)

                # If deleting and all finalizers cleared, hard-delete
                if resource.get("status") == "deleting":
                    remaining = await self._db_manager.get_finalizers(resource_id)
                    if not remaining:
                        await self._db_manager.hard_delete_resource(resource_id)
                        return {
                            "message": "All finalizers removed, " "resource deleted",
                            "resource_id": resource_id,
                        }

                updated = await self._db_manager.get_resource(resource_id)
                if not updated:
                    raise HTTPException(status_code=404, detail="Resource not found")
                return ResourceResponse(**updated)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error updating finalizers: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/v1/resources/{resource_id}/reconcile", status_code=202)
        async def trigger_reconciliation(resource_id: int):
            """Manually trigger reconciliation for a resource."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                await self._db_manager.mark_resource_for_reconciliation(resource_id)
                return {
                    "message": "Reconciliation triggered",
                    "resource_id": resource_id,
                }
            except Exception as e:
                logger.error(f"Error triggering reconciliation: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get(
            "/api/v1/resources/{resource_id}/history",
            response_model=List[ReconciliationHistoryResponse],
        )
        async def get_reconciliation_history(resource_id: int, limit: int = 10):
            """Get reconciliation history for a resource."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                history = await self._db_manager.get_reconciliation_history(
                    resource_id, limit
                )
                return [ReconciliationHistoryResponse(**record) for record in history]
            except Exception as e:
                logger.error(f"Error getting reconciliation history: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/v1/resources/{resource_id}/outputs")
        async def get_resource_outputs(resource_id: int):
            """Get action outputs for a resource."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                resource = await self._db_manager.get_resource(resource_id)
                if not resource:
                    raise HTTPException(status_code=404, detail="Resource not found")

                return {"outputs": resource.get("outputs", {})}

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting outputs: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # Plugin discovery endpoints
        @self.app.get("/api/v1/plugins/actions", response_model=List[PluginInfo])
        async def list_action_plugins():
            """List available action plugins."""
            from plugins.registry import get_registry

            registry = get_registry()
            plugins = []
            for name in registry.list_action_plugins():
                info = registry.get_action_plugin_info(name)
                if info:
                    plugins.append(PluginInfo(**info))
            return plugins

        @self.app.get("/api/v1/plugins/inputs", response_model=List[PluginInfo])
        async def list_input_plugins():
            """List available input plugins."""
            from plugins.registry import get_registry

            registry = get_registry()
            plugins = []
            for name in registry.list_input_plugins():
                info = registry.get_input_plugin_info(name)
                if info:
                    plugins.append(PluginInfo(**info))
            return plugins

    async def start(self, on_resource_event: ResourceCallback) -> None:
        """Start the HTTP server."""
        self._on_resource_event = on_resource_event
        self._setup_routes()

        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
        self.server = uvicorn.Server(config)

        logger.info(f"Starting HTTP input plugin on {self.host}:{self.port}")
        await self.server.serve()

    async def stop(self) -> None:
        """Stop the HTTP server gracefully."""
        logger.info("Stopping HTTP input plugin")
        if self.server:
            self.server.should_exit = True

    async def health_check(self) -> tuple[bool, str]:
        """Check if the HTTP API is healthy."""
        if self.server and self.server.started:
            return True, "HTTP API is running"
        return False, "HTTP API is not running"
