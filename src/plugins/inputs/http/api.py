"""
HTTP Input Plugin - REST API for resource mutation.

This plugin provides a FastAPI-based REST API for creating, updating,
and deleting infrastructure resources.  All other management/platform
endpoints (auth, users, custom roles, resource types, resource reads,
admission webhooks, event streams, plugin discovery) are served via the
management router created in ``management_api.py`` and mounted through
``mount_router()``.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException

from admission import AdmissionChain, AdmissionError, AdmissionRequest

# Re-export validation helpers so existing test imports continue to work
from api_models import (  # noqa: F401
    MAX_NAME_LENGTH,
    MAX_SPEC_SIZE,
    NAME_PATTERN,
    ResourceCreate,
    ResourceResponse,
    ResourceUpdate,
    validate_json_size,
    validate_name_format,
)
from auth import (
    check_resource_permission,
    get_current_user,
)
from events import EventBus, EventType, ResourceEvent
from plugins.base import ResourceSpec
from plugins.inputs.base import InputPlugin, ResourceCallback, validate_action_plugin
from validation import validate_spec_against_schema

logger = logging.getLogger(__name__)


class HTTPInputPlugin(InputPlugin):
    """
    Input plugin that provides a REST API for resource mutation.

    Implements the standard InputPlugin interface using FastAPI.
    Handles only three write operations:
    - POST /api/v1/resources   (create_resource)
    - PUT  /api/v1/resources/{resource_id}  (update_resource)
    - DELETE /api/v1/resources/{resource_id} (delete_resource)

    All management/platform routes are served by the management router
    mounted via mount_router().
    """

    def __init__(self):
        self.app: Optional[FastAPI] = None
        self.host: str = "0.0.0.0"
        self.port: int = 8000
        self.server = None
        self._on_resource_event: Optional[ResourceCallback] = None
        self._db_manager = None
        self._admission_chain: Optional[AdmissionChain] = None
        self._event_bus: Optional[EventBus] = None
        self._config: Dict[str, Any] = {}
        self._extra_routers: List = []

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

    def set_event_bus(self, event_bus: EventBus) -> None:
        """Set the event bus instance for publishing and streaming events."""
        self._event_bus = event_bus

    def set_admission_chain(self, chain: AdmissionChain) -> None:
        """Set the admission chain (constructed externally in main.py)."""
        self._admission_chain = chain

    def mount_router(self, router) -> None:
        """Queue an additional APIRouter to be mounted when the server starts."""
        self._extra_routers.append(router)

    def _setup_routes(self) -> None:
        """
        Set up resource mutation routes for the REST API.

        Configures the three write endpoints that live in this plugin:
        - POST   /api/v1/resources          (create_resource)
        - PUT    /api/v1/resources/{id}     (update_resource)
        - DELETE /api/v1/resources/{id}     (delete_resource)

        All other routes are provided by the management router and the
        cluster status router, both mounted via mount_router().

        Raises:
            RuntimeError: If the FastAPI app has not been initialized
        """
        if not self.app:
            raise RuntimeError("App not initialized")

        # ==================== Resource Endpoints ====================

        @self.app.post(
            "/api/v1/resources", response_model=ResourceResponse, status_code=201
        )
        async def create_resource(
            resource: ResourceCreate,
            current_user: dict = Depends(get_current_user),
        ):
            """Create a new resource."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            allowed = await check_resource_permission(
                current_user,
                self._db_manager,
                resource.resource_type_name,
                resource.resource_type_version,
                "CREATE",
            )
            if not allowed:
                raise HTTPException(status_code=403, detail="Insufficient permissions")

            from plugins.registry import get_registry

            registry = get_registry()

            # Check if a reconciler handles this resource type
            has_reconciler = registry.has_reconciler_for_resource_type(
                resource.resource_type_name
            )

            # Validate that either a reconciler or action plugin is available
            if resource.action_plugin:
                validation = validate_action_plugin(resource.action_plugin)
                if not validation.is_valid:
                    raise HTTPException(
                        status_code=400, detail=validation.error_message
                    )
            elif not has_reconciler:
                raise HTTPException(
                    status_code=400,
                    detail=f"No reconciler plugin registered for resource "
                    f"type '{resource.resource_type_name}' and no "
                    f"action_plugin specified",
                )

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

                # Run admission webhooks
                spec_to_use = resource.spec
                if self._admission_chain:
                    try:
                        admission_req = AdmissionRequest(
                            operation="CREATE",
                            resource={
                                "name": resource.name,
                                "resource_type_name": resource.resource_type_name,
                                "resource_type_version": resource.resource_type_version,
                                "spec": resource.spec,
                            },
                        )
                        spec_to_use = await self._admission_chain.run(admission_req)
                    except AdmissionError as e:
                        raise HTTPException(status_code=403, detail=e.message)

                # Build finalizers list
                finalizers = []
                if resource.action_plugin:
                    finalizers.append(resource.action_plugin)
                elif has_reconciler:
                    reconciler = registry.get_reconciler_for_resource_type(
                        resource.resource_type_name
                    )
                    finalizers.append(reconciler.name)

                resource_id = await self._db_manager.create_resource(
                    name=resource.name,
                    resource_type_name=resource.resource_type_name,
                    resource_type_version=resource.resource_type_version,
                    action_plugin=resource.action_plugin or "",
                    spec=spec_to_use,
                    plugin_config=resource.plugin_config,
                    metadata=resource.metadata,
                    finalizers=finalizers,
                )

                # Notify controller of new resource
                if self._on_resource_event:
                    spec = ResourceSpec(
                        name=resource.name,
                        action_plugin=resource.action_plugin or "",
                        spec=spec_to_use,
                        plugin_config=resource.plugin_config,
                        metadata=resource.metadata,
                    )
                    await self._on_resource_event("created", spec)

                created = await self._db_manager.get_resource(resource_id)

                # Publish CREATED event
                if self._event_bus and created:
                    event = ResourceEvent.from_resource(EventType.CREATED, created)
                    await self._event_bus.publish(event)

                return ResourceResponse(**created)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error creating resource: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put(
            "/api/v1/resources/{resource_id}", response_model=ResourceResponse
        )
        async def update_resource(
            resource_id: int,
            update: ResourceUpdate,
            current_user: dict = Depends(get_current_user),
        ):
            """Update a resource's specification."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                # Get current resource to fetch resource type
                current = await self._db_manager.get_resource(resource_id)
                if not current:
                    raise HTTPException(status_code=404, detail="Resource not found")

                allowed = await check_resource_permission(
                    current_user,
                    self._db_manager,
                    current["resource_type_name"],
                    current["resource_type_version"],
                    "UPDATE",
                )
                if not allowed:
                    raise HTTPException(
                        status_code=403, detail="Insufficient permissions"
                    )

                # If spec is being updated, validate against schema
                spec_to_use = update.spec
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

                    # Run admission webhooks
                    if self._admission_chain:
                        try:
                            admission_req = AdmissionRequest(
                                operation="UPDATE",
                                resource={
                                    "name": current["name"],
                                    "resource_type_name": current["resource_type_name"],
                                    "resource_type_version": current[
                                        "resource_type_version"
                                    ],
                                    "spec": update.spec,
                                },
                                old_resource=current,
                            )
                            spec_to_use = await self._admission_chain.run(admission_req)
                        except AdmissionError as e:
                            raise HTTPException(status_code=403, detail=e.message)

                await self._db_manager.update_resource(
                    resource_id=resource_id,
                    spec=spec_to_use,
                    plugin_config=update.plugin_config,
                )

                updated = await self._db_manager.get_resource(resource_id)

                # Notify controller
                if self._on_resource_event:
                    spec = ResourceSpec(
                        name=updated["name"],
                        action_plugin=updated.get("action_plugin", "github_actions"),
                        spec=updated.get("spec", {}),
                        plugin_config=updated.get("plugin_config"),
                    )
                    await self._on_resource_event("updated", spec)

                # Publish MODIFIED event
                if self._event_bus and updated:
                    event = ResourceEvent.from_resource(EventType.MODIFIED, updated)
                    await self._event_bus.publish(event)

                return ResourceResponse(**updated)

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error updating resource: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/api/v1/resources/{resource_id}", status_code=202)
        async def delete_resource(
            resource_id: int,
            current_user: dict = Depends(get_current_user),
        ):
            """Delete a resource (triggers destroy)."""
            if not self._db_manager:
                raise HTTPException(status_code=503, detail="Database not available")

            try:
                resource = await self._db_manager.get_resource(resource_id)
                if not resource:
                    raise HTTPException(status_code=404, detail="Resource not found")

                allowed = await check_resource_permission(
                    current_user,
                    self._db_manager,
                    resource["resource_type_name"],
                    resource["resource_type_version"],
                    "DELETE",
                )
                if not allowed:
                    raise HTTPException(
                        status_code=403, detail="Insufficient permissions"
                    )

                # Run admission webhooks
                if self._admission_chain:
                    try:
                        admission_req = AdmissionRequest(
                            operation="DELETE",
                            resource={
                                "name": resource["name"],
                                "resource_type_name": resource["resource_type_name"],
                                "resource_type_version": resource[
                                    "resource_type_version"
                                ],
                                "spec": resource.get("spec", {}),
                            },
                        )
                        await self._admission_chain.run(admission_req)
                    except AdmissionError as e:
                        raise HTTPException(status_code=403, detail=e.message)

                await self._db_manager.delete_resource(resource_id)

                # Notify controller
                if self._on_resource_event:
                    spec = ResourceSpec(
                        name=resource["name"],
                        action_plugin=resource.get("action_plugin", "github_actions"),
                        spec=resource.get("spec", {}),
                    )
                    await self._on_resource_event("deleted", spec)

                # Publish DELETED event
                if self._event_bus:
                    event = ResourceEvent.from_resource(EventType.DELETED, resource)
                    await self._event_bus.publish(event)

                return {
                    "message": "Resource marked for deletion",
                    "resource_id": resource_id,
                }

            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error deleting resource: {e}")
                raise HTTPException(status_code=500, detail=str(e))

    async def start(self, on_resource_event: ResourceCallback) -> None:
        """Start the HTTP server."""
        self._on_resource_event = on_resource_event
        self._setup_routes()
        for router in self._extra_routers:
            self.app.include_router(router)

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
