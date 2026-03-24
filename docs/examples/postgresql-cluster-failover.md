# Example: Cross-Region PostgreSQL Cluster Failover

This example walks through building two plugins that work together to automatically trigger a cross-region failover for a Patroni-managed PostgreSQL cluster running on EC2, driven by AWS Health events.

## What We're Building

- A `PostgreSQLCluster` resource type representing a 3-node Patroni cluster (1 leader + 2 replicas) in a single AWS region
- Two `PostgreSQLCluster` resources — one primary region, one standby region — linked together
- An **input plugin** (`no8s-input-aws-health`) that polls an SQS queue for AWS Health events and marks the affected cluster for reconciliation
- A **reconciler plugin** (`no8s-reconciler-postgresql-cluster`) that evaluates whether Patroni can self-heal within the region and, if not, drives a cross-region failover via the Patroni REST API

## Architecture

```
AWS Health ──▶ EventBridge ──▶ SQS ──▶ Input Plugin ──▶ mark cluster for reconciliation
                                                                    │
                                                                    ▼
                                                           Reconciler Plugin
                                                                    │
                                                    ┌───────────────┴───────────────┐
                                                    ▼                               ▼
                                           Query Patroni REST API         If regional failover needed:
                                           (can it self-heal?)            1. Pause primary cluster
                                                                          2. Promote standby cluster
                                                                          3. Update Route53
                                                                          4. Swap roles on both resources
```

### What Patroni handles vs. what the operator handles

Patroni manages **intra-cluster HA automatically**. If a single node fails, Patroni promotes one of the two replicas within the same region without any operator involvement. The operator only needs to act when a regional event is severe enough that Patroni cannot recover — for example, when two or more nodes receive simultaneous health events, or when a regional issue prevents the cluster from forming quorum.

This means the reconciler must check Patroni's cluster state before taking any cross-region action, and should wait to see if Patroni self-heals first.

## AWS Infrastructure Setup

### 1. SQS Queue

Create an SQS queue to receive health events:

```bash
aws sqs create-queue \
  --queue-name no8s-health-events \
  --attributes VisibilityTimeout=60
```

### 2. EventBridge Rule

Create a rule that captures EC2 health events and routes them to the SQS queue:

```json
{
  "source": ["aws.health"],
  "detail-type": ["AWS Health Event"],
  "detail": {
    "service": ["EC2"],
    "eventTypeCategory": ["issue", "scheduledChange"]
  }
}
```

```bash
aws events put-rule \
  --name no8s-ec2-health \
  --event-pattern file://rule.json \
  --state ENABLED

aws events put-targets \
  --rule no8s-ec2-health \
  --targets "Id=SQSTarget,Arn=arn:aws:sqs:us-east-1:123456789:no8s-health-events"
```

### 3. SQS Queue Policy

Allow EventBridge to publish to the queue:

```json
{
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "events.amazonaws.com"},
    "Action": "sqs:SendMessage",
    "Resource": "arn:aws:sqs:us-east-1:123456789:no8s-health-events",
    "Condition": {
      "ArnEquals": {
        "aws:SourceArn": "arn:aws:events:us-east-1:123456789:rule/no8s-ec2-health"
      }
    }
  }]
}
```

### 4. IAM Permissions for the Operator

The operator's IAM role needs:

```json
{
  "Effect": "Allow",
  "Action": [
    "sqs:ReceiveMessage",
    "sqs:DeleteMessage",
    "sqs:GetQueueAttributes"
  ],
  "Resource": "arn:aws:sqs:us-east-1:123456789:no8s-health-events"
}
```

Standard AWS credential resolution applies — instance profile, environment variables, or `~/.aws/credentials`.

## Resource Type Definition

Register the `PostgreSQLCluster` resource type before creating any cluster resources:

```bash
curl -X POST http://localhost:8000/api/v1/resource-types \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "PostgreSQLCluster",
    "version": "v1",
    "schema": {
      "type": "object",
      "required": ["region", "role", "patroni_scope", "patroni_members", "dns_record", "hosted_zone_id"],
      "properties": {
        "region": {
          "type": "string",
          "description": "AWS region this cluster runs in"
        },
        "role": {
          "type": "string",
          "enum": ["primary", "standby"],
          "description": "Whether this cluster is currently serving as primary or standby"
        },
        "patroni_scope": {
          "type": "string",
          "description": "Patroni cluster scope name — must match patroni.yml on each node"
        },
        "patroni_members": {
          "type": "array",
          "minItems": 3,
          "maxItems": 3,
          "items": {
            "type": "object",
            "required": ["name", "instance_id", "ip", "patroni_port"],
            "properties": {
              "name":         {"type": "string"},
              "instance_id":  {"type": "string"},
              "ip":           {"type": "string"},
              "patroni_port": {"type": "integer", "default": 8008}
            }
          },
          "description": "The three EC2 nodes forming this cluster"
        },
        "failover_target_resource": {
          "type": "string",
          "description": "Name of the paired PostgreSQLCluster resource in the other region"
        },
        "dns_record": {
          "type": "string",
          "description": "DNS name that should always point to the primary cluster leader"
        },
        "hosted_zone_id": {
          "type": "string",
          "description": "Route53 hosted zone ID for the dns_record"
        },
        "port": {
          "type": "integer",
          "default": 5432
        }
      }
    }
  }'
```

## Creating the Cluster Resources

Create one resource per region. The `failover_target_resource` field links each cluster to its counterpart.

**Primary cluster (us-east-1):**

```bash
curl -X POST http://localhost:8000/api/v1/resources \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "pg-cluster-us-east-1",
    "resource_type_name": "PostgreSQLCluster",
    "resource_type_version": "v1",
    "spec": {
      "region": "us-east-1",
      "role": "primary",
      "patroni_scope": "pg-prod",
      "patroni_members": [
        {"name": "pg-node-1", "instance_id": "i-0abc001", "ip": "10.0.1.10", "patroni_port": 8008},
        {"name": "pg-node-2", "instance_id": "i-0abc002", "ip": "10.0.1.11", "patroni_port": 8008},
        {"name": "pg-node-3", "instance_id": "i-0abc003", "ip": "10.0.1.12", "patroni_port": 8008}
      ],
      "failover_target_resource": "pg-cluster-eu-west-1",
      "dns_record": "db.example.com",
      "hosted_zone_id": "Z1234567890",
      "port": 5432
    }
  }'
```

**Standby cluster (eu-west-1):**

```bash
curl -X POST http://localhost:8000/api/v1/resources \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "pg-cluster-eu-west-1",
    "resource_type_name": "PostgreSQLCluster",
    "resource_type_version": "v1",
    "spec": {
      "region": "eu-west-1",
      "role": "standby",
      "patroni_scope": "pg-prod-standby",
      "patroni_members": [
        {"name": "pg-node-4", "instance_id": "i-0def001", "ip": "10.1.1.10", "patroni_port": 8008},
        {"name": "pg-node-5", "instance_id": "i-0def002", "ip": "10.1.1.11", "patroni_port": 8008},
        {"name": "pg-node-6", "instance_id": "i-0def003", "ip": "10.1.1.12", "patroni_port": 8008}
      ],
      "failover_target_resource": "pg-cluster-us-east-1",
      "dns_record": "db.example.com",
      "hosted_zone_id": "Z1234567890",
      "port": 5432
    }
  }'
```

## The Input Plugin: `no8s-input-aws-health`

The input plugin polls SQS, maps affected EC2 instance IDs to `PostgreSQLCluster` resources, and marks them for reconciliation.

### Project Structure

```
no8s-input-aws-health/
├── pyproject.toml
└── src/
    └── no8s_aws_health/
        ├── __init__.py
        └── plugin.py
```

### Plugin Implementation

```python
# src/no8s_aws_health/plugin.py
import asyncio
import json
import logging
import os
from typing import Any, Callable, Awaitable, Dict, Optional

import boto3

from no8s_operator.plugins.inputs.base import InputPlugin
from no8s_operator.plugins.base import ResourceSpec

logger = logging.getLogger(__name__)

# AWS Health events that warrant a closer look at the cluster
ACTIONABLE_EVENT_TYPES = {
    "AWS_EC2_INSTANCE_STORE_DRIVE_PERFORMANCE_DEGRADED",
    "AWS_EC2_UNDERLYING_SYSTEM_MAINTENANCE_SCHEDULED",
    "AWS_EC2_INSTANCE_REBOOT_MAINTENANCE_SCHEDULED",
    "AWS_EC2_OPERATIONAL_ISSUE",
}

ResourceCallback = Callable[[str, ResourceSpec], Awaitable[None]]


class AWSHealthInputPlugin(InputPlugin):
    """Polls an SQS queue for AWS Health events and marks affected
    PostgreSQLCluster resources for reconciliation."""

    def __init__(self) -> None:
        self._queue_url: str = ""
        self._region: str = ""
        self._sqs = None
        self._db_manager = None
        self._running = False

    @property
    def name(self) -> str:
        return "aws_health"

    @property
    def version(self) -> str:
        return "1.0.0"

    @classmethod
    def load_config_from_env(cls) -> Dict[str, Any]:
        return {
            "queue_url": os.environ["AWS_HEALTH_SQS_QUEUE_URL"],
            "region":    os.environ.get("AWS_REGION", "us-east-1"),
        }

    async def initialize(self, config: Dict[str, Any]) -> None:
        self._queue_url = config["queue_url"]
        self._region = config["region"]
        self._sqs = boto3.client("sqs", region_name=self._region)

    def set_db_manager(self, db_manager: Any) -> None:
        self._db_manager = db_manager

    async def start(self, on_resource_event: ResourceCallback) -> None:
        self._running = True
        logger.info("AWS Health input plugin started, polling %s", self._queue_url)

        while self._running:
            try:
                await self._poll(on_resource_event)
            except Exception:
                logger.exception("Error polling SQS")
                await asyncio.sleep(5)

    async def _poll(self, on_resource_event: ResourceCallback) -> None:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._sqs.receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,  # long polling
            ),
        )

        for message in response.get("Messages", []):
            try:
                await self._handle_message(message, on_resource_event)
                # Delete only after successful processing
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._sqs.delete_message(
                        QueueUrl=self._queue_url,
                        ReceiptHandle=message["ReceiptHandle"],
                    ),
                )
            except Exception:
                logger.exception("Failed to process SQS message %s", message.get("MessageId"))

    async def _handle_message(
        self, message: Dict[str, Any], on_resource_event: ResourceCallback
    ) -> None:
        body = json.loads(message["Body"])

        # EventBridge wraps the Health event — unwrap if needed
        if body.get("source") == "aws.health":
            event = body
        else:
            event = json.loads(body.get("Message", message["Body"]))

        event_type_code = event.get("detail", {}).get("eventTypeCode", "")
        if event_type_code not in ACTIONABLE_EVENT_TYPES:
            logger.debug("Ignoring event type %s", event_type_code)
            return

        # Extract affected EC2 instance IDs
        affected_entities = event.get("detail", {}).get("affectedEntities", [])
        instance_ids = [e["entityValue"] for e in affected_entities]

        if not instance_ids:
            return

        logger.info("Health event %s affects instances: %s", event_type_code, instance_ids)

        # Find PostgreSQLCluster resources that contain any of these instance IDs
        resources = await self._find_affected_clusters(instance_ids)

        for resource in resources:
            # Attach the raw health event to the resource metadata so the
            # reconciler can inspect it during reconciliation
            await self._db_manager.update_resource_metadata(
                resource["id"],
                {
                    **resource.get("metadata", {}),
                    "health_event": {
                        "event_type_code": event_type_code,
                        "affected_instance_ids": instance_ids,
                        "event_arn": event.get("detail", {}).get("eventArn", ""),
                    },
                    "failover_state": "health_event_received",
                },
            )

            spec = ResourceSpec(
                name=resource["name"],
                action_plugin="",
                spec=resource["spec"],
                metadata=resource.get("metadata", {}),
            )
            await on_resource_event("updated", spec)
            logger.info("Marked cluster %s for reconciliation", resource["name"])

    async def _find_affected_clusters(self, instance_ids: list) -> list:
        """Find PostgreSQLCluster resources whose patroni_members contain
        any of the given EC2 instance IDs."""
        # Uses a JSONB containment query — see DatabaseManager.get_resources_by_member_instance_id
        results = []
        for instance_id in instance_ids:
            resources = await self._db_manager.get_resources_by_member_instance_id(
                resource_type_name="PostgreSQLCluster",
                instance_id=instance_id,
            )
            for r in resources:
                if r["id"] not in {x["id"] for x in results}:
                    results.append(r)
        return results

    async def stop(self) -> None:
        self._running = False

    async def health_check(self) -> tuple[bool, str]:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._sqs.get_queue_attributes(
                    QueueUrl=self._queue_url, AttributeNames=["QueueArn"]
                ),
            )
            return True, "SQS reachable"
        except Exception as e:
            return False, str(e)
```

### Database Method Required

The plugin relies on `get_resources_by_member_instance_id()` on `DatabaseManager`. This uses a PostgreSQL JSONB containment query to find clusters by instance ID without scanning every resource:

```sql
SELECT * FROM resources
WHERE resource_type_name = 'PostgreSQLCluster'
  AND deleted_at IS NULL
  AND spec->'patroni_members' @> '[{"instance_id": $1}]'::jsonb
```

### Installation and Registration

```toml
# pyproject.toml
[project]
name = "no8s-input-aws-health"
version = "1.0.0"
dependencies = [
    "no8s-operator",
    "boto3",
]

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
```

Register alongside the HTTP plugin in `src/plugins/registry.py`:

```python
from no8s_aws_health.plugin import AWSHealthInputPlugin
registry.register_input_plugin(AWSHealthInputPlugin)
```

Set the environment variable before starting the operator:

```bash
export AWS_HEALTH_SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789/no8s-health-events
export AWS_REGION=us-east-1
```

## The Reconciler: `no8s-reconciler-postgresql-cluster`

The reconciler owns the `PostgreSQLCluster` resource type and drives cross-region failover when the input plugin marks a cluster as affected.

### Project Structure

```
no8s-reconciler-postgresql-cluster/
├── pyproject.toml
└── src/
    └── no8s_pg_cluster/
        ├── __init__.py
        ├── reconciler.py
        └── patroni.py
```

### Patroni Client

```python
# src/no8s_pg_cluster/patroni.py
import asyncio
import logging
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)


class PatroniClient:
    """Thin async wrapper around the Patroni REST API."""

    def __init__(self, ip: str, port: int = 8008) -> None:
        self._base = f"http://{ip}:{port}"

    async def get_cluster(self) -> Dict[str, Any]:
        """GET /cluster — returns all members and their state."""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self._base}/cluster", timeout=aiohttp.ClientTimeout(total=5)) as r:
                r.raise_for_status()
                return await r.json()

    async def get_leader(self) -> Optional[Dict[str, Any]]:
        """GET / on each member — returns the one that reports role=master."""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self._base}/", timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("role") == "master":
                        return data
                return None

    async def pause(self) -> None:
        """POST /pause — tells Patroni to stop automatic failover.
        Call this on the primary cluster before promoting the standby
        to prevent split-brain."""
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self._base}/pause", timeout=aiohttp.ClientTimeout(total=10)) as r:
                r.raise_for_status()

    async def promote(self) -> None:
        """POST /promote — promotes this node's cluster to primary.
        Call this on the standby cluster's leader."""
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self._base}/promote", timeout=aiohttp.ClientTimeout(total=10)) as r:
                r.raise_for_status()

    async def get_replication_lag(self) -> Optional[int]:
        """Returns replication lag in bytes from GET /, or None if unavailable."""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self._base}/", timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status in (200, 503):
                    data = await r.json()
                    return data.get("replication_lag")
                return None


async def find_reachable_member(members: list) -> Optional[PatroniClient]:
    """Return a PatroniClient for the first reachable cluster member."""
    for member in members:
        client = PatroniClient(member["ip"], member["patroni_port"])
        try:
            await client.get_cluster()
            return client
        except Exception:
            continue
    return None
```

### Reconciler Implementation

```python
# src/no8s_pg_cluster/reconciler.py
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import boto3
import aiohttp

from no8s_operator.plugins.reconcilers.base import (
    BaseReconciler,
    ReconcilerContext,
    ReconcileResult,
)

from .patroni import PatroniClient, find_reachable_member

logger = logging.getLogger(__name__)

# How long to wait for Patroni to self-heal before escalating to cross-region failover
PATRONI_SELF_HEAL_GRACE_SECONDS = 120

# Maximum replication lag (bytes) we'll accept before refusing to promote the standby
MAX_ACCEPTABLE_LAG_BYTES = 50 * 1024 * 1024  # 50 MB


class PostgreSQLClusterReconciler(BaseReconciler):
    """Reconciles PostgreSQLCluster resources.

    Patroni handles intra-cluster HA automatically. This reconciler only
    acts when a regional AWS Health event suggests the primary cluster
    cannot recover on its own, and drives a cross-region failover via
    the Patroni REST API.
    """

    reconcile_interval = 30

    @property
    def name(self) -> str:
        return "postgresql_cluster"

    @property
    def resource_types(self) -> List[str]:
        return ["PostgreSQLCluster"]

    async def reconcile(
        self, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> ReconcileResult:
        resource_id = resource["id"]
        spec = resource["spec"]
        metadata = resource.get("metadata") or {}
        failover_state = metadata.get("failover_state")

        if resource.get("status") == "deleting":
            return await self._handle_delete(resource, ctx)

        # No health event — ensure we're in a clean ready state
        if not metadata.get("health_event"):
            await ctx.update_status(resource_id, "ready",
                                    message="Cluster healthy",
                                    observed_generation=resource["generation"])
            await ctx.update_outputs(resource_id, await self._get_outputs(spec))
            return ReconcileResult(success=True)

        # Route to the correct stage of the failover state machine
        if failover_state == "health_event_received":
            return await self._evaluate_health_event(resource, ctx)

        if failover_state == "awaiting_patroni_self_heal":
            return await self._check_patroni_self_heal(resource, ctx)

        if failover_state == "initiating_failover":
            return await self._initiate_failover(resource, ctx)

        if failover_state == "promoting_standby":
            return await self._check_promotion(resource, ctx)

        if failover_state == "updating_dns":
            return await self._update_dns_and_finalise(resource, ctx)

        logger.warning("Unknown failover_state %r on %s", failover_state, resource["name"])
        return ReconcileResult(success=False, message=f"Unknown failover_state: {failover_state}")

    # -------------------------------------------------------------------------
    # Failover state machine
    # -------------------------------------------------------------------------

    async def _evaluate_health_event(
        self, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> ReconcileResult:
        """Stage 1: A health event has arrived. Check how many of our members
        are affected. If only one node is affected, Patroni will self-heal —
        wait and re-evaluate. If two or more are affected, escalate immediately."""
        resource_id = resource["id"]
        spec = resource["spec"]
        metadata = resource.get("metadata") or {}
        health_event = metadata["health_event"]

        affected = set(health_event["affected_instance_ids"])
        our_instances = {m["instance_id"] for m in spec["patroni_members"]}
        our_affected = affected & our_instances

        logger.info(
            "%s: health event affects %d/%d of our members: %s",
            resource["name"], len(our_affected), len(our_instances), our_affected,
        )

        await ctx.set_condition(
            resource_id, "Degraded", "True", "AWSHealthEvent",
            f"AWS Health event affecting {len(our_affected)} cluster member(s): "
            f"{health_event['event_type_code']}",
        )

        if len(our_affected) == 1:
            # Single node — Patroni will handle it. Give it time to self-heal.
            logger.info("%s: single member affected, waiting for Patroni to self-heal", resource["name"])
            await self._update_failover_state(resource_id, "awaiting_patroni_self_heal", ctx)
            await ctx.update_status(resource_id, "reconciling",
                                    message="Waiting for Patroni to self-heal")
            return ReconcileResult(
                success=True,
                message="Waiting for Patroni self-heal",
                requeue_after=PATRONI_SELF_HEAL_GRACE_SECONDS,
            )

        # Two or more members affected — escalate immediately
        logger.warning("%s: %d members affected, escalating to cross-region failover",
                       resource["name"], len(our_affected))
        await self._update_failover_state(resource_id, "initiating_failover", ctx)
        return await self._initiate_failover(resource, ctx)

    async def _check_patroni_self_heal(
        self, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> ReconcileResult:
        """Stage 2: Check if Patroni recovered on its own after the grace period."""
        resource_id = resource["id"]
        spec = resource["spec"]

        client = await find_reachable_member(spec["patroni_members"])
        if client is None:
            # Can't reach any member — escalate
            logger.warning("%s: no Patroni members reachable, escalating", resource["name"])
            await self._update_failover_state(resource_id, "initiating_failover", ctx)
            return await self._initiate_failover(resource, ctx)

        try:
            cluster = await client.get_cluster()
        except Exception as e:
            logger.warning("%s: Patroni cluster query failed: %s", resource["name"], e)
            await self._update_failover_state(resource_id, "initiating_failover", ctx)
            return await self._initiate_failover(resource, ctx)

        members = cluster.get("members", [])
        leader = next((m for m in members if m.get("role") == "Leader"), None)

        if leader and all(m.get("state") in ("running", "streaming") for m in members):
            # Patroni recovered — clear the health event and return to ready
            logger.info("%s: Patroni self-healed, clearing health event", resource["name"])
            await self._clear_health_event(resource_id, resource, ctx)
            return ReconcileResult(success=True, message="Patroni self-healed")

        # Still degraded — escalate to cross-region failover
        logger.warning("%s: Patroni did not self-heal (leader=%s), escalating", resource["name"], leader)
        await self._update_failover_state(resource_id, "initiating_failover", ctx)
        return await self._initiate_failover(resource, ctx)

    async def _initiate_failover(
        self, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> ReconcileResult:
        """Stage 3: Begin cross-region failover.
        - Pause the primary cluster (prevents split-brain)
        - Verify standby replication lag is acceptable
        - Trigger promotion of the standby cluster's Patroni leader
        """
        resource_id = resource["id"]
        spec = resource["spec"]

        if spec["role"] != "primary":
            logger.info("%s: not primary, skipping failover initiation", resource["name"])
            return ReconcileResult(success=True)

        await ctx.set_condition(resource_id, "FailoverInProgress", "True",
                                "FailoverInitiated", "Cross-region failover initiated")
        await ctx.update_status(resource_id, "reconciling", message="Initiating cross-region failover")

        # Pause the primary cluster to prevent split-brain
        primary_client = await find_reachable_member(spec["patroni_members"])
        if primary_client:
            try:
                await primary_client.pause()
                logger.info("%s: primary cluster paused", resource["name"])
            except Exception as e:
                logger.warning("%s: could not pause primary cluster: %s", resource["name"], e)
                # Continue anyway — fencing is best-effort if nodes are already down

        # Find the standby cluster resource
        standby_resource = await self._get_peer_resource(resource, ctx)
        if standby_resource is None:
            await ctx.update_status(resource_id, "failed",
                                    message=f"Standby resource '{spec['failover_target_resource']}' not found")
            return ReconcileResult(success=False, message="Standby resource not found")

        standby_spec = standby_resource["spec"]

        # Check replication lag on the standby cluster
        standby_client = await find_reachable_member(standby_spec["patroni_members"])
        if standby_client is None:
            await ctx.update_status(resource_id, "failed",
                                    message="Cannot reach standby cluster members")
            return ReconcileResult(success=False, message="Standby unreachable", requeue_after=30)

        lag = await standby_client.get_replication_lag()
        if lag is not None and lag > MAX_ACCEPTABLE_LAG_BYTES:
            logger.warning("%s: standby lag %d bytes exceeds threshold, waiting", resource["name"], lag)
            return ReconcileResult(
                success=True,
                message=f"Standby lag {lag // 1024 // 1024}MB — waiting for catchup",
                requeue_after=15,
            )

        # Promote the standby cluster
        try:
            await standby_client.promote()
            logger.info("%s: promotion request sent to standby cluster", resource["name"])
        except Exception as e:
            await ctx.update_status(resource_id, "failed",
                                    message=f"Failed to promote standby: {e}")
            return ReconcileResult(success=False, message=str(e), requeue_after=30)

        await self._update_failover_state(resource_id, "promoting_standby", ctx)
        return ReconcileResult(success=True, message="Promotion request sent", requeue_after=15)

    async def _check_promotion(
        self, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> ReconcileResult:
        """Stage 4: Poll the standby cluster until Patroni reports it has promoted."""
        resource_id = resource["id"]
        spec = resource["spec"]

        standby_resource = await self._get_peer_resource(resource, ctx)
        if standby_resource is None:
            return ReconcileResult(success=False, message="Standby resource not found")

        standby_spec = standby_resource["spec"]
        standby_client = await find_reachable_member(standby_spec["patroni_members"])
        if standby_client is None:
            return ReconcileResult(success=False, message="Standby unreachable", requeue_after=15)

        leader = await standby_client.get_leader()
        if leader is None:
            logger.info("%s: standby not yet promoted, checking again in 15s", resource["name"])
            return ReconcileResult(success=True, message="Waiting for promotion", requeue_after=15)

        logger.info("%s: standby cluster promoted successfully", resource["name"])
        await self._update_failover_state(resource_id, "updating_dns", ctx)
        return await self._update_dns_and_finalise(resource, ctx)

    async def _update_dns_and_finalise(
        self, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> ReconcileResult:
        """Stage 5: Update Route53 and swap roles on both cluster resources."""
        resource_id = resource["id"]
        spec = resource["spec"]

        standby_resource = await self._get_peer_resource(resource, ctx)
        if standby_resource is None:
            return ReconcileResult(success=False, message="Standby resource not found")

        standby_spec = standby_resource["spec"]

        # Find the new leader's IP in the standby cluster
        standby_client = await find_reachable_member(standby_spec["patroni_members"])
        leader_data = await standby_client.get_leader()
        if leader_data is None:
            return ReconcileResult(success=False, message="No leader found after promotion", requeue_after=15)

        # Identify the leader node by matching name to member IP
        leader_name = leader_data.get("name")
        leader_member = next(
            (m for m in standby_spec["patroni_members"] if m["name"] == leader_name), None
        )
        new_primary_ip = leader_member["ip"] if leader_member else standby_spec["patroni_members"][0]["ip"]

        # Update Route53
        try:
            route53 = boto3.client("route53")
            route53.change_resource_record_sets(
                HostedZoneId=spec["hosted_zone_id"],
                ChangeBatch={
                    "Changes": [{
                        "Action": "UPSERT",
                        "ResourceRecordSet": {
                            "Name": spec["dns_record"],
                            "Type": "A",
                            "TTL": 60,
                            "ResourceRecords": [{"Value": new_primary_ip}],
                        },
                    }]
                },
            )
            logger.info("%s: Route53 updated to %s", resource["name"], new_primary_ip)
        except Exception as e:
            logger.error("%s: Route53 update failed: %s", resource["name"], e)
            await ctx.update_status(resource_id, "failed", message=f"DNS update failed: {e}")
            return ReconcileResult(success=False, message=str(e), requeue_after=30)

        # Swap roles: old primary → standby, old standby → primary
        # Update our own resource's metadata to clear the failover state
        await ctx.db.update_resource_spec_field(resource_id, "role", "standby")
        await ctx.db.update_resource_spec_field(standby_resource["id"], "role", "primary")
        await self._clear_health_event(resource_id, resource, ctx)
        await self._clear_health_event(standby_resource["id"], standby_resource, ctx)

        await ctx.set_condition(resource_id, "FailoverInProgress", "False",
                                "FailoverComplete", "Cross-region failover completed successfully")
        await ctx.set_condition(resource_id, "Degraded", "False",
                                "FailoverComplete", "")
        await ctx.update_status(resource_id, "ready",
                                message=f"Failover complete — now standby. New primary: {new_primary_ip}",
                                observed_generation=resource["generation"])
        await ctx.update_outputs(resource_id, {"role": "standby", "new_primary_ip": new_primary_ip})

        logger.info("%s: failover complete. New primary IP: %s", resource["name"], new_primary_ip)
        return ReconcileResult(success=True, message="Failover complete")

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    async def _get_peer_resource(
        self, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> Optional[Dict[str, Any]]:
        """Fetch the paired cluster resource by name."""
        peer_name = resource["spec"].get("failover_target_resource")
        if not peer_name:
            return None
        return await ctx.get_resource_by_name(peer_name, "PostgreSQLCluster")

    async def _update_failover_state(
        self, resource_id: int, state: str, ctx: ReconcilerContext
    ) -> None:
        await ctx.db.update_resource_metadata_field(resource_id, "failover_state", state)

    async def _clear_health_event(
        self, resource_id: int, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> None:
        metadata = {k: v for k, v in (resource.get("metadata") or {}).items()
                    if k not in ("health_event", "failover_state")}
        await ctx.db.update_resource_metadata(resource_id, metadata)

    async def _get_outputs(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "role": spec["role"],
            "dns_record": spec["dns_record"],
            "port": spec.get("port", 5432),
        }

    async def _handle_delete(
        self, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> ReconcileResult:
        resource_id = resource["id"]
        await ctx.remove_finalizer(resource_id, self.name)
        remaining = await ctx.get_finalizers(resource_id)
        if not remaining:
            await ctx.hard_delete_resource(resource_id)
        return ReconcileResult(success=True, message="Deleted")
```

### Failover State Machine

The reconciler progresses through five stages, persisted in `metadata.failover_state` so it survives operator restarts and requeue cycles:

```
health_event_received
        │
        ▼
  (1 member affected?)
   Yes ──▶ awaiting_patroni_self_heal ──▶ (Patroni self-healed?) ──▶ clear event → ready
   No  ──────────────────────────────────────────────────────────┐
                                                                 ▼
                                                      initiating_failover
                                                      (pause primary, check lag)
                                                                 │
                                                                 ▼
                                                       promoting_standby
                                                       (poll until leader appears)
                                                                 │
                                                                 ▼
                                                         updating_dns
                                                  (Route53 + swap roles → ready)
```

### Package Registration

```toml
# pyproject.toml
[project]
name = "no8s-reconciler-postgresql-cluster"
version = "1.0.0"
dependencies = [
    "no8s-operator",
    "boto3",
    "aiohttp",
]

[project.entry-points.'no8s.reconcilers']
postgresql_cluster = "no8s_pg_cluster.reconciler:PostgreSQLClusterReconciler"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
```

## Observing a Failover

Watch the event stream for both resources during a failover:

```bash
curl -N http://localhost:8000/api/v1/events?resource_type=PostgreSQLCluster \
  -H "Authorization: Bearer $TOKEN"
```

Check the current state of both clusters at any point:

```bash
curl http://localhost:8000/api/v1/resources?resource_type=PostgreSQLCluster \
  -H "Authorization: Bearer $TOKEN" | jq '.[].conditions'
```

During failover, the primary cluster will report:

```json
[
  {"type": "Degraded",           "status": "True",    "reason": "AWSHealthEvent"},
  {"type": "FailoverInProgress", "status": "True",    "reason": "FailoverInitiated"},
  {"type": "Ready",              "status": "Unknown", "reason": "Reconciling"}
]
```

After completion:

```json
[
  {"type": "Degraded",           "status": "False", "reason": "FailoverComplete"},
  {"type": "FailoverInProgress", "status": "False", "reason": "FailoverComplete"},
  {"type": "Ready",              "status": "True",  "reason": "ReconcileSuccess"}
]
```

## Operator Changes Required

Two methods must be added to `ReconcilerContext` and `DatabaseManager` for this example to work:

| Method | Where | Purpose |
|---|---|---|
| `ctx.get_resource_by_name(name, resource_type)` | `ReconcilerContext` | Fetch the paired cluster resource |
| `db.get_resources_by_member_instance_id(resource_type, instance_id)` | `DatabaseManager` | JSONB array lookup used by the input plugin |
| `db.update_resource_metadata_field(id, key, value)` | `DatabaseManager` | Update a single metadata key without overwriting others |
| `db.update_resource_spec_field(id, key, value)` | `DatabaseManager` | Swap `role` on both resources after failover |

These are thin wrappers over existing PostgreSQL queries and do not require schema changes.