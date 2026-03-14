"""
Cluster status endpoints for HA cluster reporting.

Two endpoints are provided:

- GET /api/v1/cluster/health  — public, no auth required. Returns a minimal
  liveness response safe for load balancers and external health checks.

- GET /api/v1/cluster/nodes  — admin only. Returns all known operator nodes
  with their status (Ready/NotReady), role (leader/follower), and heartbeat
  timestamps — similar to `kubectl get nodes`. Also includes the identity and
  in-memory leadership state of the instance that served the request, plus
  the current leader lock details from the database.
"""

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Optional

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


class NodeStatus(str, Enum):
    READY = "Ready"
    NOT_READY = "NotReady"


class NodeRole(str, Enum):
    LEADER = "leader"
    FOLLOWER = "follower"


class NodeInfo(BaseModel):
    """Information about a single operator cluster node."""

    node_id: str
    hostname: str
    pid: str
    status: NodeStatus
    role: NodeRole
    age: str
    first_seen: datetime
    last_heartbeat: datetime


class ClusterNodesResponse(BaseModel):
    """Admin-only cluster nodes response."""

    this_instance_id: str
    this_instance_is_leader: bool
    leader_lock: Optional[LeaderInfo]
    nodes: List[NodeInfo]


def create_cluster_status_router(
    leader_election: LeaderElection,
    db_manager: DatabaseManager,
) -> APIRouter:
    """Return an APIRouter exposing the cluster health and nodes endpoints."""

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
        "/api/v1/cluster/nodes",
        response_model=ClusterNodesResponse,
        summary="Cluster nodes",
        description=(
            "Admin-only. Lists all operator instances that have registered a heartbeat, "
            "their current status (Ready/NotReady), role (leader/follower), and age — "
            "similar to `kubectl get nodes`. Also exposes the identity and in-memory "
            "leadership state of the instance serving the request, and the current "
            "leader lock details from the database."
        ),
    )
    async def cluster_nodes(
        _admin: dict = Depends(require_admin),
    ) -> ClusterNodesResponse:
        now = datetime.now(timezone.utc)

        leader_lock: Optional[LeaderInfo] = None
        leader_id: Optional[str] = None
        try:
            lock_row = await db_manager.get_leader_lock_info(
                leader_election._config.lock_name
            )
            if lock_row:
                acquired_at: datetime = lock_row["acquired_at"]
                if acquired_at.tzinfo is None:
                    acquired_at = acquired_at.replace(tzinfo=timezone.utc)
                expires_at = acquired_at + timedelta(
                    seconds=lock_row["lease_duration_seconds"]
                )
                is_valid = now < expires_at
                leader_lock = LeaderInfo(
                    holder_id=lock_row["holder_id"],
                    acquired_at=acquired_at,
                    expires_at=expires_at,
                    is_valid=is_valid,
                )
                if is_valid:
                    leader_id = lock_row["holder_id"]
        except Exception as exc:
            logger.warning("Failed to fetch leader lock info: %s", exc)

        rows: list = []
        try:
            rows = await db_manager.get_cluster_nodes()
        except Exception as exc:
            logger.warning("Failed to fetch cluster nodes: %s", exc)

        nodes: List[NodeInfo] = []
        for row in rows:
            last_heartbeat: datetime = row["last_heartbeat"]
            if last_heartbeat.tzinfo is None:
                last_heartbeat = last_heartbeat.replace(tzinfo=timezone.utc)
            first_seen: datetime = row["first_seen"]
            if first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=timezone.utc)

            expires_at = last_heartbeat + timedelta(
                seconds=row["lease_duration_seconds"]
            )
            status = NodeStatus.READY if now < expires_at else NodeStatus.NOT_READY
            role = NodeRole.LEADER if row["node_id"] == leader_id else NodeRole.FOLLOWER
            age = _format_age(now - first_seen)

            nodes.append(
                NodeInfo(
                    node_id=row["node_id"],
                    hostname=row["hostname"],
                    pid=row["pid"] or "",
                    status=status,
                    role=role,
                    age=age,
                    first_seen=first_seen,
                    last_heartbeat=last_heartbeat,
                )
            )

        return ClusterNodesResponse(
            this_instance_id=leader_election.holder_id,
            this_instance_is_leader=leader_election.is_leader,
            leader_lock=leader_lock,
            nodes=nodes,
        )

    return router


def _format_age(delta: timedelta) -> str:
    """Format a timedelta as a human-readable age string like kubectl (e.g. '2d5h', '45m')."""
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "0s"
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d{hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m{seconds}s" if seconds else f"{minutes}m"
    return f"{seconds}s"
