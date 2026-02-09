"""
Database Manager - PostgreSQL schema and operations.

Stores resource definitions, reconciliation history, and metadata.
"""

import asyncpg
import hashlib
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from migrate import run_migrations

logger = logging.getLogger(__name__)


class ResourceStatus(Enum):
    """Status of a resource."""

    PENDING = "pending"
    RECONCILING = "reconciling"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


@dataclass
class ReconciliationResult:
    """Result of a reconciliation operation."""

    success: bool = False
    phase: str = "pending"
    plan_output: str = ""
    apply_output: str = ""
    error_message: Optional[str] = None
    resources_created: int = 0
    resources_updated: int = 0
    resources_deleted: int = 0
    has_changes: bool = False


class DatabaseManager:
    """Manages PostgreSQL database operations for the controller."""

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        min_pool_size: int = 5,
        max_pool_size: int = 20,
    ):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Establish connection pool to PostgreSQL."""
        self.pool = await asyncpg.create_pool(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
            min_size=self.min_pool_size,
            max_size=self.max_pool_size,
            command_timeout=60,  # Query timeout
        )
        logger.info(
            f"Connected to PostgreSQL (pool: {self.min_pool_size}-{self.max_pool_size})"
        )

    async def close(self):
        """Close the connection pool."""
        if self.pool:
            await self.pool.close()
            logger.info("Closed PostgreSQL connection")

    def _ensure_connected(self) -> None:
        """Ensure the database connection pool is established."""
        if self.pool is None:
            raise RuntimeError(
                "Database not connected. Call connect() before performing operations."
            )

    async def initialize_schema(self) -> None:
        """Apply database migrations to bring schema up to date."""
        self._ensure_connected()
        await run_migrations(self.pool)
        logger.info("Database schema initialized")

    # ==================== Resource Type Methods ====================

    async def create_resource_type(
        self,
        name: str,
        version: str,
        schema: Dict[str, Any],
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Create a new resource type.

        Args:
            name: Resource type name (e.g., 'TerraformModule')
            version: Version string (e.g., 'v1', 'v1beta1')
            schema: OpenAPI v3 JSON Schema for validating specs
            description: Optional description
            metadata: Optional metadata
        """
        if metadata is None:
            metadata = {}

        async with self.pool.acquire() as conn:
            resource_type_id = await conn.fetchval(
                """
                INSERT INTO resource_types (name, version, schema, description, metadata)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                name,
                version,
                json.dumps(schema),
                description,
                json.dumps(metadata),
            )

            logger.info(
                f"Created resource type {name}/{version} with ID {resource_type_id}"
            )
            return resource_type_id

    async def get_resource_type(
        self, resource_type_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get a resource type by ID."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM resource_types WHERE id = $1",
                resource_type_id,
            )
            if not row:
                return None
            return self._parse_resource_type_row(row)

    async def get_resource_type_by_name_version(
        self, name: str, version: str
    ) -> Optional[Dict[str, Any]]:
        """Get a resource type by name and version."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM resource_types WHERE name = $1 AND version = $2",
                name,
                version,
            )
            if not row:
                return None
            return self._parse_resource_type_row(row)

    async def list_resource_types(
        self,
        name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List resource types with optional filters."""
        async with self.pool.acquire() as conn:
            query = "SELECT * FROM resource_types WHERE 1=1"
            params = []
            param_count = 0

            if name:
                param_count += 1
                query += f" AND name = ${param_count}"
                params.append(name)

            if status:
                param_count += 1
                query += f" AND status = ${param_count}"
                params.append(status)

            param_count += 1
            query += f" ORDER BY name, version LIMIT ${param_count}"
            params.append(limit)

            rows = await conn.fetch(query, *params)
            return [self._parse_resource_type_row(row) for row in rows]

    async def update_resource_type(
        self,
        resource_type_id: int,
        schema: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update a resource type."""
        async with self.pool.acquire() as conn:
            updates = []
            params = []
            param_count = 0

            if schema is not None:
                param_count += 1
                updates.append(f"schema = ${param_count}")
                params.append(json.dumps(schema))

            if description is not None:
                param_count += 1
                updates.append(f"description = ${param_count}")
                params.append(description)

            if status is not None:
                param_count += 1
                updates.append(f"status = ${param_count}")
                params.append(status)

            if metadata is not None:
                param_count += 1
                updates.append(f"metadata = ${param_count}")
                params.append(json.dumps(metadata))

            if not updates:
                return

            updates.append("updated_at = NOW()")
            param_count += 1
            params.append(resource_type_id)

            query = f"UPDATE resource_types SET {', '.join(updates)} WHERE id = ${param_count}"
            await conn.execute(query, *params)
            logger.info(f"Updated resource type {resource_type_id}")

    async def delete_resource_type(self, resource_type_id: int) -> bool:
        """
        Delete a resource type.

        Returns False if resources still reference this type.
        """
        async with self.pool.acquire() as conn:
            # Get the resource type to check name/version
            rt = await conn.fetchrow(
                "SELECT name, version FROM resource_types WHERE id = $1",
                resource_type_id,
            )
            if not rt:
                return False

            # Check if any resources reference this type
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM resources
                WHERE resource_type_name = $1
                  AND resource_type_version = $2
                  AND deleted_at IS NULL
                """,
                rt["name"],
                rt["version"],
            )

            if count > 0:
                logger.warning(
                    f"Cannot delete resource type {rt['name']}/{rt['version']}: "
                    f"{count} resources still reference it"
                )
                return False

            await conn.execute(
                "DELETE FROM resource_types WHERE id = $1",
                resource_type_id,
            )
            logger.info(f"Deleted resource type {resource_type_id}")
            return True

    def _parse_resource_type_row(self, row: asyncpg.Record) -> Dict[str, Any]:
        """Parse a resource_type row from the database."""
        result = dict(row)
        result["schema"] = json.loads(result["schema"]) if result.get("schema") else {}
        result["metadata"] = (
            json.loads(result["metadata"]) if result.get("metadata") else {}
        )
        return result

    # ==================== Resource Methods ====================

    async def create_resource(
        self,
        name: str,
        resource_type_name: str,
        resource_type_version: str,
        action_plugin: str,
        spec: Optional[Dict[str, Any]] = None,
        plugin_config: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        finalizers: Optional[List[str]] = None,
    ) -> int:
        """
        Create a new resource.

        Args:
            name: Resource name
            resource_type_name: Name of the resource type
            resource_type_version: Version of the resource type
            action_plugin: Which action plugin to use (e.g., 'terraform')
            spec: Resource specification (plugin-specific)
            plugin_config: Plugin configuration (e.g., backend config)
            metadata: Additional metadata
            finalizers: Initial finalizers list (defaults to [action_plugin])
        """
        if spec is None:
            spec = {}

        if plugin_config is None:
            plugin_config = {}

        if metadata is None:
            metadata = {}

        if finalizers is None:
            finalizers = [action_plugin]

        # Calculate spec hash for change detection
        spec_hash = self._calculate_spec_hash(spec)

        async with self.pool.acquire() as conn:
            resource_id = await conn.fetchval(
                """
                INSERT INTO resources (
                    name, resource_type_name, resource_type_version,
                    action_plugin, spec, plugin_config, metadata,
                    spec_hash, status, next_reconcile_time, finalizers
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), $10)
                RETURNING id
                """,
                name,
                resource_type_name,
                resource_type_version,
                action_plugin,
                json.dumps(spec),
                json.dumps(plugin_config),
                json.dumps(metadata),
                spec_hash,
                ResourceStatus.PENDING.value,
                json.dumps(finalizers),
            )

            logger.info(
                f"Created resource {name} ({resource_type_name}/{resource_type_version}) "
                f"with ID {resource_id} using {action_plugin} plugin"
            )
            return resource_id

    async def update_resource(
        self,
        resource_id: int,
        spec: Optional[Dict[str, Any]] = None,
        plugin_config: Optional[Dict[str, Any]] = None,
    ):
        """Update a resource's specification."""
        async with self.pool.acquire() as conn:
            # Get current resource
            resource = await conn.fetchrow(
                "SELECT spec, plugin_config, generation FROM resources WHERE id = $1",
                resource_id,
            )

            if not resource:
                raise ValueError(f"Resource {resource_id} not found")

            # Get current values
            current_spec = json.loads(resource["spec"]) if resource["spec"] else {}
            current_plugin_config = (
                json.loads(resource["plugin_config"])
                if resource["plugin_config"]
                else {}
            )

            # Merge updates
            new_spec = spec if spec is not None else current_spec
            new_plugin_config = (
                plugin_config if plugin_config is not None else current_plugin_config
            )

            # Calculate new spec hash
            new_spec_hash = self._calculate_spec_hash(new_spec)
            new_generation = resource["generation"] + 1

            await conn.execute(
                """
                UPDATE resources
                SET spec = $1,
                    plugin_config = $2,
                    spec_hash = $3,
                    generation = $4,
                    status = $5,
                    next_reconcile_time = NOW(),
                    updated_at = NOW()
                WHERE id = $6
                """,
                json.dumps(new_spec),
                json.dumps(new_plugin_config),
                new_spec_hash,
                new_generation,
                ResourceStatus.PENDING.value,
                resource_id,
            )

            logger.info(
                f"Updated resource {resource_id} to generation {new_generation}"
            )

    async def delete_resource(self, resource_id: int):
        """Mark a resource for deletion (soft delete)."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE resources
                SET status = $1,
                    deleted_at = NOW(),
                    next_reconcile_time = NOW()
                WHERE id = $2
                """,
                ResourceStatus.DELETING.value,
                resource_id,
            )

            logger.info(f"Marked resource {resource_id} for deletion")

    async def get_resource(self, resource_id: int) -> Optional[Dict[str, Any]]:
        """Get a resource by ID."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM resources WHERE id = $1 AND deleted_at IS NULL",
                resource_id,
            )
            if not row:
                return None

            return self._parse_resource_row(row)

    async def get_resource_by_name(
        self,
        name: str,
        resource_type_name: str,
        resource_type_version: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a resource by name and resource type."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM resources
                WHERE name = $1
                  AND resource_type_name = $2
                  AND resource_type_version = $3
                  AND deleted_at IS NULL
                """,
                name,
                resource_type_name,
                resource_type_version,
            )
            if not row:
                return None

            return self._parse_resource_row(row)

    async def list_resources(
        self,
        status: Optional[str] = None,
        action_plugin: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List resources with optional filters."""
        async with self.pool.acquire() as conn:
            query = "SELECT * FROM resources WHERE deleted_at IS NULL"
            params = []
            param_count = 0

            if status:
                param_count += 1
                query += f" AND status = ${param_count}"
                params.append(status)

            if action_plugin:
                param_count += 1
                query += f" AND action_plugin = ${param_count}"
                params.append(action_plugin)

            param_count += 1
            query += f" ORDER BY created_at DESC LIMIT ${param_count}"
            params.append(limit)

            rows = await conn.fetch(query, *params)
            return [self._parse_resource_row(row) for row in rows]

    async def get_resources_needing_reconciliation(
        self, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get resources that need reconciliation.

        Similar to Kubernetes informers - finds resources where desired != observed state.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM resources
                WHERE (deleted_at IS NULL OR status = 'deleting')
                  AND (
                    -- Never reconciled
                    last_reconcile_time IS NULL
                    -- Generation changed
                    OR generation > observed_generation
                    -- Scheduled for reconciliation
                    OR next_reconcile_time <= NOW()
                    -- Failed and ready for retry
                    OR (status = 'failed' AND next_reconcile_time <= NOW())
                    -- Marked for deletion
                    OR status = 'deleting'
                  )
                  AND status != 'reconciling'
                ORDER BY
                    CASE status
                        WHEN 'deleting' THEN 0
                        WHEN 'pending' THEN 1
                        WHEN 'failed' THEN 2
                        ELSE 3
                    END,
                    next_reconcile_time ASC NULLS FIRST
                LIMIT $1
                """,
                limit,
            )

            return [self._parse_resource_row(row) for row in rows]

    async def update_resource_status(
        self,
        resource_id: int,
        status: ResourceStatus,
        message: Optional[str] = None,
        observed_generation: Optional[int] = None,
    ):
        """Update the status of a resource."""
        async with self.pool.acquire() as conn:
            query_parts = [
                "UPDATE resources SET status = $1, status_message = $2, updated_at = NOW()"
            ]
            params = [status.value, message]
            param_count = 2

            if observed_generation is not None:
                param_count += 1
                query_parts.append(f"observed_generation = ${param_count}")
                params.append(observed_generation)

            # Set next reconcile time based on status
            if status == ResourceStatus.READY:
                # Reconcile again in 5 minutes for drift detection
                query_parts.append(
                    "next_reconcile_time = NOW() + INTERVAL '5 minutes', "
                    "last_reconcile_time = NOW(), "
                    "retry_count = 0"
                )
            elif status == ResourceStatus.FAILED:
                # Will be handled by requeue logic with exponential backoff
                query_parts.append("retry_count = retry_count + 1")

            param_count += 1
            query_parts.append(f"WHERE id = ${param_count}")
            params.append(resource_id)

            query = (
                ", ".join(query_parts[1:]) if len(query_parts) > 2 else query_parts[1]
            )
            full_query = f"{query_parts[0]}, {query}"

            await conn.execute(full_query, *params)

    async def record_reconciliation(
        self,
        resource_id: int,
        success: bool,
        phase: str,
        plan_output: Optional[str] = None,
        apply_output: Optional[str] = None,
        error_message: Optional[str] = None,
        resources_created: int = 0,
        resources_updated: int = 0,
        resources_deleted: int = 0,
        duration_seconds: Optional[float] = None,
        trigger_reason: Optional[str] = None,
        drift_detected: bool = False,
    ):
        """Record a reconciliation attempt in history."""
        async with self.pool.acquire() as conn:
            # Get current generation
            generation = await conn.fetchval(
                "SELECT generation FROM resources WHERE id = $1", resource_id
            )

            await conn.execute(
                """
                INSERT INTO reconciliation_history (
                    resource_id, generation, success, phase,
                    plan_output, apply_output, error_message,
                    resources_created, resources_updated, resources_deleted,
                    duration_seconds, trigger_reason, drift_detected
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                """,
                resource_id,
                generation,
                success,
                phase,
                plan_output,
                apply_output,
                error_message,
                resources_created,
                resources_updated,
                resources_deleted,
                duration_seconds,
                trigger_reason,
                drift_detected,
            )

    async def requeue_failed_resources(
        self,
        base_delay: int = 60,
        max_delay: int = 3600,
        jitter_factor: float = 0.1,
    ):
        """
        Requeue failed resources with exponential backoff and jitter.

        Args:
            base_delay: Base delay in seconds (default 60)
            max_delay: Maximum delay in seconds (default 3600 = 1 hour)
            jitter_factor: Jitter factor ±X (default 0.1 = ±10%)
        """
        async with self.pool.acquire() as conn:
            # Calculate delay with exponential backoff, capped at max_delay
            # Add jitter of ±jitter_factor to prevent thundering herd
            await conn.execute(
                """
                UPDATE resources
                SET next_reconcile_time = NOW() + (
                    INTERVAL '1 second' * LEAST(
                        $1 * POWER(2, LEAST(retry_count, 10)),
                        $2
                    ) * (1 + (random() * 2 - 1) * $3)
                )
                WHERE status = 'failed'
                  AND next_reconcile_time < NOW()
                """,
                base_delay,
                max_delay,
                jitter_factor,
            )

    async def hard_delete_resource(self, resource_id: int) -> bool:
        """
        Permanently delete a resource from the database.

        Only succeeds if the resource has been soft-deleted (deleted_at set)
        and all finalizers have been removed.

        Args:
            resource_id: The resource ID to permanently delete

        Returns:
            True if the resource was deleted, False if not found,
            not soft-deleted, or finalizers remain
        """
        async with self.pool.acquire() as conn:
            result = await conn.fetchval(
                """
                DELETE FROM resources
                WHERE id = $1
                  AND deleted_at IS NOT NULL
                  AND finalizers = '[]'::jsonb
                RETURNING id
                """,
                resource_id,
            )
            if result:
                logger.info(f"Hard-deleted resource {resource_id}")
                return True
            return False

    async def add_finalizer(self, resource_id: int, finalizer: str) -> None:
        """
        Add a finalizer to a resource.

        No-op if the finalizer already exists.

        Args:
            resource_id: The resource ID
            finalizer: Finalizer name to add
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE resources
                SET finalizers = CASE
                        WHEN NOT finalizers @> to_jsonb($2::text)
                        THEN finalizers || to_jsonb($2::text)
                        ELSE finalizers
                    END,
                    updated_at = NOW()
                WHERE id = $1
                """,
                resource_id,
                finalizer,
            )

    async def remove_finalizer(self, resource_id: int, finalizer: str) -> None:
        """
        Remove a finalizer from a resource.

        Args:
            resource_id: The resource ID
            finalizer: Finalizer name to remove
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE resources
                SET finalizers = COALESCE(
                        (SELECT jsonb_agg(elem)
                         FROM jsonb_array_elements(finalizers) AS elem
                         WHERE elem #>> '{}' != $2),
                        '[]'::jsonb
                    ),
                    updated_at = NOW()
                WHERE id = $1
                """,
                resource_id,
                finalizer,
            )

    async def get_finalizers(self, resource_id: int) -> List[str]:
        """
        Get the finalizers list for a resource.

        Args:
            resource_id: The resource ID

        Returns:
            List of finalizer names, or empty list if resource not found
        """
        async with self.pool.acquire() as conn:
            result = await conn.fetchval(
                "SELECT finalizers FROM resources WHERE id = $1",
                resource_id,
            )
            if result is None:
                return []
            return json.loads(result) if isinstance(result, str) else result

    async def mark_resource_for_reconciliation(self, resource_id: int):
        """Manually trigger reconciliation for a resource."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE resources
                SET next_reconcile_time = NOW()
                WHERE id = $1
                """,
                resource_id,
            )

    async def update_resource_outputs(
        self, resource_id: int, outputs: Dict[str, Any]
    ) -> None:
        """
        Update the outputs for a resource.

        Args:
            resource_id: The resource ID
            outputs: Dictionary of output key-value pairs
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE resources
                SET outputs = $1, updated_at = NOW()
                WHERE id = $2
                """,
                json.dumps(outputs),
                resource_id,
            )

    async def get_reconciliation_history(
        self, resource_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get reconciliation history for a resource."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM reconciliation_history
                WHERE resource_id = $1
                ORDER BY reconcile_time DESC
                LIMIT $2
                """,
                resource_id,
                limit,
            )

            return [dict(row) for row in rows]

    def _parse_resource_row(self, row: asyncpg.Record) -> Dict[str, Any]:
        """
        Parse a resource row from the database, converting JSON fields.

        Converts the asyncpg Record to a regular dictionary and parses
        JSON-stored fields (spec, plugin_config, metadata) into Python dicts.

        Args:
            row: An asyncpg.Record from a database query

        Returns:
            A dictionary with the resource data, with JSON fields parsed
        """
        result = dict(row)
        # Parse JSON fields
        result["spec"] = json.loads(result["spec"]) if result.get("spec") else {}
        result["plugin_config"] = (
            json.loads(result["plugin_config"]) if result.get("plugin_config") else {}
        )
        result["metadata"] = (
            json.loads(result["metadata"]) if result.get("metadata") else {}
        )
        result["outputs"] = (
            json.loads(result["outputs"]) if result.get("outputs") else {}
        )
        result["finalizers"] = (
            json.loads(result["finalizers"])
            if isinstance(result.get("finalizers"), str)
            else result.get("finalizers", []) or []
        )
        return result

    def _calculate_spec_hash(self, spec: Dict[str, Any]) -> str:
        """Calculate a hash of the resource specification for change detection."""
        spec_string = json.dumps(spec, sort_keys=True)
        return hashlib.sha256(spec_string.encode()).hexdigest()
