# Example: Cross-Region PostgreSQL Cluster Failover

This example walks through building two plugins that work together to automatically trigger a cross-region failover for a Patroni-managed PostgreSQL cluster running on EC2, driven by AWS Health events.

## What We're Building

- A `PostgreSQLCluster` resource type representing a 3-node Patroni cluster (1 leader + 2 replicas) in a single AWS region
- Two `PostgreSQLCluster` resources — one primary region, one standby region — linked together
- Initial provisioning via the existing **`github_actions` action plugin**: creating a resource triggers a GitHub Actions workflow with two jobs — Terraform (EC2 instances) then Ansible (PostgreSQL install) — which calls back to the operator API with the provisioned instance details
- A third provisioning stage handled **natively by the reconciler**: once the workflow completes and instance IPs are known from outputs, the reconciler configures Patroni directly via the Patroni REST API, including cross-region replication setup for the standby cluster
- An **input plugin** (`no8s-input-aws-health`) that polls an SQS queue for AWS Health events and marks the affected cluster for reconciliation
- A **reconciler plugin** (`no8s-reconciler-postgresql-cluster`) that handles all three lifecycle paths: initial provisioning, Patroni configuration, and cross-region failover

## Architecture

**Initial provisioning** (triggered by HTTP POST):

```
POST /api/v1/resources ──▶ HTTP Input Plugin ──▶ Resource created (pending)
                                                          │
                                                          ▼
                                                 Reconciler Plugin
                                                 ctx.get_action_plugin("github_actions")
                                                          │
                                             ┌────────────┴────────────┐
                                             ▼                         ▼
                                    Job 1: Terraform          Job 2: Ansible
                                    (3 EC2 instances)         (PostgreSQL install)
                                             └────────────┬────────────┘
                                                          │ PUT /api/v1/resources/{id}/outputs
                                                          │ (instance IDs, IPs, member list)
                                                          ▼
                                                 Stage 3: Reconciler native
                                                 Patroni REST API:
                                                 - verify cluster formed
                                                 - configure cross-region replication
                                                          │
                                                          ▼
                                                   Resource → ready
```

**Health event failover**:

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
      "required": ["region", "role", "patroni_scope", "postgres_version",
                   "instance_type", "storage_gb", "subnet_ids",
                   "dns_record", "hosted_zone_id"],
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
        "postgres_version": {
          "type": "string",
          "description": "PostgreSQL major version to install, e.g. \"16\""
        },
        "instance_type": {
          "type": "string",
          "description": "EC2 instance type for all three nodes, e.g. \"r6g.xlarge\""
        },
        "storage_gb": {
          "type": "integer",
          "description": "EBS volume size in GB per node"
        },
        "subnet_ids": {
          "type": "array",
          "minItems": 3,
          "maxItems": 3,
          "items": {"type": "string"},
          "description": "One subnet ID per node — spread across AZs for HA within the region"
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

Create one resource per region using the HTTP API. Submitting the resource is all that is required — the operator picks it up and drives provisioning automatically.

The `action_plugin` field tells the operator to use the `github_actions` plugin for stages 1 and 2. The `plugin_config` field tells it which repository and workflow to use, plus the Terraform backend configuration. The `spec` describes the desired cluster state — EC2 instance shape, PostgreSQL version, and networking. Instance IDs and IPs are not known yet; the GitHub Actions workflow will populate them in `outputs` once Terraform completes.

**Primary cluster (us-east-1):**

```bash
curl -X POST http://localhost:8000/api/v1/resources \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "pg-cluster-us-east-1",
    "resource_type_name": "PostgreSQLCluster",
    "resource_type_version": "v1",
    "action_plugin": "github_actions",
    "plugin_config": {
      "owner": "my-org",
      "repo": "infrastructure",
      "workflow": "provision-pg-cluster.yml",
      "ref": "main",
      "tf_state_bucket": "my-tf-state",
      "tf_state_key_prefix": "pg-clusters"
    },
    "spec": {
      "region": "us-east-1",
      "role": "primary",
      "patroni_scope": "pg-prod",
      "postgres_version": "16",
      "instance_type": "r6g.xlarge",
      "storage_gb": 500,
      "subnet_ids": ["subnet-aaa001", "subnet-aaa002", "subnet-aaa003"],
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
    "action_plugin": "github_actions",
    "plugin_config": {
      "owner": "my-org",
      "repo": "infrastructure",
      "workflow": "provision-pg-cluster.yml",
      "ref": "main",
      "tf_state_bucket": "my-tf-state",
      "tf_state_key_prefix": "pg-clusters"
    },
    "spec": {
      "region": "eu-west-1",
      "role": "standby",
      "patroni_scope": "pg-prod-standby",
      "postgres_version": "16",
      "instance_type": "r6g.xlarge",
      "storage_gb": 500,
      "subnet_ids": ["subnet-bbb001", "subnet-bbb002", "subnet-bbb003"],
      "failover_target_resource": "pg-cluster-us-east-1",
      "dns_record": "db.example.com",
      "hosted_zone_id": "Z1234567890",
      "port": 5432
    }
  }'
```

!!! note
    The standby cluster resource can be created before or after the primary. The reconciler's Patroni configuration stage (stage 3) waits until both clusters have completed provisioning before setting up cross-region replication, so creation order does not matter.

## Provisioning via GitHub Actions

When a `PostgreSQLCluster` resource is created, the reconciler detects that `outputs` is empty (no `patroni_members` yet) and kicks off provisioning. It calls the `github_actions` action plugin, which triggers the workflow and polls until it completes.

### Stage 1 and 2: GitHub Actions Workflow

The workflow (`provision-pg-cluster.yml`) receives the cluster spec as inputs and runs two sequential jobs:

```yaml
# .github/workflows/provision-pg-cluster.yml
name: Provision PostgreSQL Cluster
on:
  workflow_dispatch:
    inputs:
      resource_id:      { required: true }
      region:           { required: true }
      patroni_scope:    { required: true }
      postgres_version: { required: true }
      instance_type:    { required: true }
      storage_gb:       { required: true }
      subnet_ids:       { required: true }  # JSON array
      tf_state_bucket:  { required: true }
      tf_state_key_prefix: { required: true }
      operator_api_url: { required: true }
      operator_token:   { required: true }

jobs:
  terraform:
    name: Provision EC2 Instances
    runs-on: ubuntu-latest
    outputs:
      patroni_members: ${{ steps.tf_output.outputs.patroni_members }}
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3

      - name: Terraform Init
        run: terraform init -backend-config="bucket=${{ inputs.tf_state_bucket }}" \
               -backend-config="key=${{ inputs.tf_state_key_prefix }}/${{ inputs.patroni_scope }}.tfstate"
        working-directory: terraform/pg-cluster

      - name: Terraform Apply
        run: terraform apply -auto-approve \
               -var="region=${{ inputs.region }}" \
               -var="instance_type=${{ inputs.instance_type }}" \
               -var="storage_gb=${{ inputs.storage_gb }}" \
               -var='subnet_ids=${{ inputs.subnet_ids }}'
        working-directory: terraform/pg-cluster

      - name: Capture outputs
        id: tf_output
        run: |
          members=$(terraform output -json patroni_members)
          echo "patroni_members=$members" >> $GITHUB_OUTPUT
        working-directory: terraform/pg-cluster

  ansible:
    name: Install PostgreSQL
    runs-on: ubuntu-latest
    needs: terraform
    steps:
      - uses: actions/checkout@v4

      - name: Install Ansible
        run: pip install ansible boto3

      - name: Build inventory from Terraform output
        run: |
          echo '${{ needs.terraform.outputs.patroni_members }}' \
            | python scripts/build_inventory.py > inventory.yml

      - name: Run Ansible playbook
        run: ansible-playbook -i inventory.yml playbooks/postgresql.yml \
               --extra-vars "postgres_version=${{ inputs.postgres_version }} \
                             patroni_scope=${{ inputs.patroni_scope }}"

      - name: Report outputs to operator
        run: |
          curl -X PUT ${{ inputs.operator_api_url }}/api/v1/resources/${{ inputs.resource_id }}/outputs \
            -H "Authorization: Bearer ${{ inputs.operator_token }}" \
            -H "Content-Type: application/json" \
            -d '{"patroni_members": ${{ needs.terraform.outputs.patroni_members }}}'
```

The final step calls back to the operator API to store the provisioned member list. This is the standard pattern for returning structured data from a GitHub Actions workflow — see [Action Plugins](../action-plugins.md).

The `patroni_members` output from Terraform has the shape the reconciler and input plugin expect:

```json
[
  {"name": "pg-node-1", "instance_id": "i-0abc001", "ip": "10.0.1.10", "patroni_port": 8008},
  {"name": "pg-node-2", "instance_id": "i-0abc002", "ip": "10.0.1.11", "patroni_port": 8008},
  {"name": "pg-node-3", "instance_id": "i-0abc003", "ip": "10.0.1.12", "patroni_port": 8008}
]
```

### Stage 3: Reconciler-Native Patroni Configuration

Once the workflow completes and `outputs.patroni_members` is populated, the reconciler's next run detects that EC2 instances exist but Patroni has not yet been configured (no `PatroniReady` condition). It then:

1. **Waits for all 3 Patroni nodes to be reachable** — polls `GET http://<ip>:8008/` on each member until all respond
2. **Verifies cluster formation** — calls `GET /cluster` and confirms one leader and two replicas have formed
3. **Configures cross-region replication** (standby clusters only) — fetches the primary cluster's leader IP from the primary resource's `outputs`, then calls the Patroni API to configure streaming replication from the primary region's leader
4. **Sets the `PatroniReady` condition** — marks the cluster ready

This stage runs entirely within the reconciler using the Patroni client shown below — no additional action plugin is needed. The cross-region replication step is done here rather than in Ansible because the standby cluster needs to know the primary cluster's actual leader IP, which is only available after both Terraform runs have completed.

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

### Plugin Behaviour

The plugin subclasses `InputPlugin` and runs a SQS long-poll loop in `start()`. For each message received:

1. **Filter** — ignore event type codes that don't warrant action (e.g. informational notices). Relevant types include `AWS_EC2_INSTANCE_STORE_DRIVE_PERFORMANCE_DEGRADED`, `AWS_EC2_UNDERLYING_SYSTEM_MAINTENANCE_SCHEDULED`, `AWS_EC2_OPERATIONAL_ISSUE`.
2. **Extract instance IDs** — parse `detail.affectedEntities[].entityValue` from the EventBridge event payload.
3. **Look up affected clusters** — query the database for `PostgreSQLCluster` resources whose `outputs.patroni_members` contains any of those instance IDs (instance IDs live in `outputs`, not `spec`, because they are populated by Terraform after provisioning).
4. **Stamp metadata** — write the health event details (`event_type_code`, `affected_instance_ids`, `event_arn`) into the resource's `metadata.health_event` field and set `metadata.failover_state = "health_event_received"`.
5. **Trigger reconciliation** — call `on_resource_event("updated", spec)` to wake the reconciler immediately.
6. **Acknowledge** — delete the SQS message only after successful processing.

The plugin calls `set_db_manager()` during startup and uses it for both the instance ID lookup and the metadata update.

### Database Method Required

The plugin relies on `get_resources_by_member_instance_id()` on `DatabaseManager`. This uses a PostgreSQL JSONB containment query to find clusters by instance ID without scanning every resource:

```sql
SELECT * FROM resources
WHERE resource_type_name = 'PostgreSQLCluster'
  AND deleted_at IS NULL
  AND outputs->'patroni_members' @> '[{"instance_id": $1}]'::jsonb
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

The reconciler owns the `PostgreSQLCluster` resource type and handles three distinct lifecycle paths, dispatched from a single `reconcile()` method.

### Lifecycle Path 1: Initial Provisioning (GitHub Actions)

On the first reconciliation, `outputs.patroni_members` will be empty — the GitHub Actions workflow hasn't completed yet. The reconciler requeues with a short delay (`requeue_after=30`) and returns. Once the workflow calls back to the operator API and `outputs.patroni_members` is populated, the reconciler moves to the Patroni configuration path.

This check runs before any other logic in `reconcile()` — if there are no outputs, nothing else proceeds.

### Lifecycle Path 2: Patroni Configuration (Reconciler Native)

When `outputs.patroni_members` is populated but the `PatroniReady` condition is absent or `False`, the reconciler drives stage 3 directly using the Patroni REST API:

1. Poll `GET http://<ip>:8008/` on each member until all three respond
2. Call `GET /cluster` and confirm one leader and two replicas have elected
3. **Standby clusters only** — fetch the primary cluster's leader IP from the primary resource's `outputs`, then configure streaming replication. This step is done here rather than in Ansible because the standby needs the primary's actual leader IP, which is only available after both Terraform runs complete
4. Set `PatroniReady = True` condition and mark the resource `ready`

Patroni uses `GET /` to report member state and `GET /cluster` for the full topology. The reconciler only reads from these endpoints during this stage — Patroni bootstraps itself via its configured DCS (etcd/Consul/ZooKeeper).

### Lifecycle Path 3: Health Event Failover

When `metadata.health_event` is present, the reconciler runs the failover state machine. The current state is persisted in `metadata.failover_state` so it survives operator restarts and requeue cycles.

The key design principle: **don't escalate to cross-region failover if Patroni can handle it within the region**. A single-node health event means Patroni will promote a replica automatically — the reconciler just needs to wait and verify. Only when two or more nodes are affected, or Patroni fails to recover after a grace period, does cross-region failover begin.

Member lookups during failover use `outputs.patroni_members` (not `spec`).

### Failover State Machine

The failover path progresses through five stages, persisted in `metadata.failover_state`:

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

### Package Structure

```
no8s-reconciler-postgresql-cluster/
├── pyproject.toml
└── src/
    └── no8s_pg_cluster/
        ├── __init__.py
        ├── reconciler.py   # PostgreSQLClusterReconciler (BaseReconciler subclass)
        └── patroni.py      # Thin async client for the Patroni REST API
```

```toml
# pyproject.toml
[project]
name = "no8s-reconciler-postgresql-cluster"
version = "1.0.0"
dependencies = [
    "no8s-operator",
    "boto3",    # Route53 updates during failover
    "aiohttp",  # Patroni REST API calls
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

The following methods need to be added to `ReconcilerContext` and `DatabaseManager`:

| Method                                                               | Where               | Purpose                                                                                                                              |
|----------------------------------------------------------------------|---------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `ctx.get_resource_by_name(name, resource_type)`                      | `ReconcilerContext` | Fetch the paired cluster resource by name — used to look up the peer cluster during failover and Patroni configuration               |
| `db.get_resources_by_member_instance_id(resource_type, instance_id)` | `DatabaseManager`   | JSONB containment query on `outputs->'patroni_members'` — used by the input plugin to map an EC2 instance ID to its cluster resource |
| `db.update_resource_metadata_field(id, key, value)`                  | `DatabaseManager`   | Update a single `metadata` key without overwriting the rest — used to advance `failover_state` between reconcile calls               |
| `db.update_resource_spec_field(id, key, value)`                      | `DatabaseManager`   | Update a single `spec` field — used to swap `role` on both resources after failover completes                                        |

These are thin wrappers over existing PostgreSQL JSONB queries and do not require schema changes.