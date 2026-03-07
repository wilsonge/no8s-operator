"""
Cluster status endpoints for HA cluster reporting.

Two endpoints are provided:

- GET /api/v1/cluster/health  — public, no auth required. Returns a minimal
  liveness response safe for load balancers and external health checks.

- GET /api/v1/cluster/status  — admin only. Returns full leadership detail:
  which instance holds the lock, when it was acquired, and whether it is
  still valid.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from auth import require_admin
from db import DatabaseManager
from leader_election import LeaderElection

logger = logging.getLogger(__name__)


class ClusterHealthResponse(BaseModel):
    """Public liveness response."""

    status: str


class LeaderInfo(BaseModel):
    """Information about the current lock holder."""

    holder_id: str
    acquired_at: datetime
    expires_at: datetime
    is_valid: bool


class ClusterStatusResponse(BaseModel):
    """Admin-only HA cluster status response."""

    instance_id: str
    is_leader: bool
    leader: Optional[LeaderInfo]


def create_cluster_status_router(
    leader_election: LeaderElection,
    db_manager: DatabaseManager,
) -> APIRouter:
    """Return an APIRouter exposing the cluster health and status endpoints."""

    router = APIRouter(tags=["cluster"])

    @router.get(
        "/api/v1/cluster/health",
        response_model=ClusterHealthResponse,
        summary="Cluster liveness",
        description=(
            "Public liveness check. Safe for load balancers; requires no authentication. "
            "Returns 503 if this node cannot reach the database."
        ),
    )
    async def cluster_health(response: Response) -> ClusterHealthResponse:
        if await db_manager.ping():
            return ClusterHealthResponse(status="ok")
        response.status_code = 503
        return ClusterHealthResponse(status="disconnected")

    @router.get(
        "/api/v1/cluster/status",
        response_model=ClusterStatusResponse,
        summary="HA cluster status",
        description=(
            "Admin-only. Returns leadership state for this instance and the "
            "current lock holder as recorded in the database."
        ),
    )
    async def cluster_status(
        _admin: dict = Depends(require_admin),
    ) -> ClusterStatusResponse:
        leader_info: Optional[LeaderInfo] = None

        try:
            row = await db_manager.get_leader_lock_info(
                leader_election._config.lock_name
            )
            if row:
                acquired_at: datetime = row["acquired_at"]
                if acquired_at.tzinfo is None:
                    acquired_at = acquired_at.replace(tzinfo=timezone.utc)
                expires_at = acquired_at + timedelta(
                    seconds=row["lease_duration_seconds"]
                )
                leader_info = LeaderInfo(
                    holder_id=row["holder_id"],
                    acquired_at=acquired_at,
                    expires_at=expires_at,
                    is_valid=datetime.now(timezone.utc) < expires_at,
                )
        except Exception as exc:
            logger.warning("Failed to fetch leader lock info: %s", exc)

        return ClusterStatusResponse(
            instance_id=leader_election.holder_id,
            is_leader=leader_election.is_leader,
            leader=leader_info,
        )

    return router
