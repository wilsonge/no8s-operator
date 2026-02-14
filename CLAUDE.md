# Operator Controller

A Kubernetes-style controller for managing infrastructure. The operator receives resource events via input plugins (HTTP API, queue listeners, polling), caches resource state, and delegates reconciliation to **3rd party reconciler plugins** — each responsible for one or more resource types.

Reconciler plugins are installed as separate pip packages and auto-discovered via Python entry points. Each reconciler owns its own reconciliation loop and may optionally use the operator's action plugin system (GitHub Actions, GitLab Pipelines, etc.) to execute changes.

## Architecture

The system follows a delegated controller pattern inspired by Kubernetes:

```
┌──────────────────────────────────────────────────────────────────┐
│                      Operator Controller                         │
│                                                                  │
│  ┌────────────────┐    ┌──────────────────────────────────────┐  │
│  │  Input Plugins │───▶│      Main Loop (controller.py)       │  │
│  │  (HTTP, SQS,   │    │                                      │  │
│  │   Polling)     │    │  1. Receive resource events          │  │
│  └────────────────┘    │  2. Cache resource state             │  │
│                        │  3. Dispatch to reconciler plugin    │  │
│                        │  4. Update status and metadata       │  │
│                        └──────────┬───────────────────────────┘  │
│                                   │                              │
│              ┌────────────────────┼─────────────────────┐        │
│              │                                          │        │
│              ▼                                          ▼        │
│  ┌───────────────────┐                       ┌─────────────────┐ │
│  │ Reconciler Plugin │                       │Reconciler Plugin│ │
│  │ (pip: no8s-db)    │                       │(pip: no8s-dns)  │ │
│  │                   │                       │                 │ │
│  │ ResourceType:     │                       │ ResourceType:   │ │
│  │  DatabaseCluster  │                       │  DnsRecord      │ │
│  └────────┬──────────┘                       └───────┬─────────┘ │
│           │ (optional)                               │ (direct)  │
└───────────┼──────────────────────────────────────────┼───────────┘
            ▼                                          ▼
    ┌──────────────┐                           ┌────────────┐
    │ Action Plugin│                           │  External  │
    │ (GitHub      │                           │  API       │
    │  Actions)    │                           │            │
    └──────────────┘                           └────────────┘
            │
            ▼
    ┌──────────┐
    │PostgreSQL│
    │ Resource │
    │  Store   │
    └──────────┘
```

### Components

1. **Main Loop (`controller.py`)** - Receives resource events, caches state, dispatches to reconciler plugins. Manages lifecycle, status tracking, and audit history.
2. **Database Manager (`db.py`)** - PostgreSQL operations for resource definitions, cached state, and metadata.
3. **Input Plugins** - Pluggable event sources. Currently: **HTTP API (`plugins/inputs/http/`)**.
4. **Reconciler Plugins** - 3rd party pip packages discovered via entry points, owning reconciliation logic per resource type.
5. **Action Plugins** - Optional executors for reconcilers. Currently: **GitHub Actions (`plugins/actions/github_actions/`)**.

## Key Features

- **Declarative Infrastructure**: Define desired state; reconciler plugins ensure it matches reality
- **Resource Types with Schema Validation**: OpenAPI v3 schemas (similar to Kubernetes CRDs)
- **3rd Party Reconcilers**: Auto-discovered via Python entry points
- **Finalizers**: Kubernetes-style deletion protection — resources cannot be hard-deleted until all finalizers are cleared
- **Admission Webhooks**: HTTP callback-based validating and mutating webhooks before persistence
- **Event Streaming**: Server-Sent Events (SSE) for real-time watch semantics
- **PostgreSQL Metadata**: Resource definitions, state, history, and locks (equivalent of etcd)
- **Automatic Reconciliation**: Continuous drift detection and correction
- **Exponential Backoff**: Failed reconciliations retry with intelligent backoff
- **Concurrent Reconciliation**: Multiple resources reconciled in parallel
- **Audit History**: Complete history of all reconciliation attempts

## Plugin Architecture

Three plugin types: **input plugins** (event sources), **reconciler plugins** (reconciliation per resource type), and **action plugins** (optional executors).

### Input Plugins

- **HTTP API** (implemented) - REST API for creating/updating resources
- **HTTP Polling** (planned) - Poll external APIs for state changes
- **Queue Listeners** (planned) - SQS, RabbitMQ, etc.

### Reconciler Plugins

3rd party pip packages discovered via the `no8s.reconcilers` entry point group. See [Developing a Reconciler Plugin](#developing-a-reconciler-plugin) for details.

Key characteristics:
- Each reconciler declares which resource type(s) it handles
- Reconcilers run their own continuous loop, reading from the operator's resource cache
- May use action plugins or implement reconciliation directly
- The operator starts/stops reconciler loops alongside its own main loop

### Action Plugins

Optional executors available to reconcilers:

- **GitHub Actions** (implemented) - Trigger workflows and monitor completion
- **GitLab Pipelines** (planned)
- **HTTP API** (planned)

## Resource Types

Resource Types are similar to Kubernetes CRDs — they define schemas using OpenAPI v3 JSON Schema. All resources must reference a resource type, and specs are validated against the schema.

### Creating a Resource Type

```bash
curl -X POST http://localhost:8000/api/v1/resource-types \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DatabaseCluster",
    "version": "v1",
    "description": "Managed database cluster",
    "schema": {
      "type": "object",
      "required": ["engine", "instance_class", "storage_gb"],
      "properties": {
        "engine": {"type": "string", "enum": ["postgres", "mysql"]},
        "instance_class": {"type": "string"},
        "storage_gb": {"type": "integer", "minimum": 10, "maximum": 10000},
        "replicas": {"type": "integer", "minimum": 0, "default": 0},
        "high_availability": {"type": "boolean", "default": false}
      }
    }
  }'
```

### Other Resource Type Operations

```bash
# List (optionally filter by name)
curl http://localhost:8000/api/v1/resource-types?name=DatabaseCluster

# Get by ID or by name/version
curl http://localhost:8000/api/v1/resource-types/1
curl http://localhost:8000/api/v1/resource-types/DatabaseCluster/v1
```

Resource types support versioning (v1, v1beta1, v2) — each version can have a different schema.

## Installation

### Prerequisites

- Python 3.11+
- PostgreSQL 16+
- GitHub personal access token with `repo` and `workflow` scopes (for GitHub Actions plugin)

### Quick Start with Docker

```bash
git clone <repo-url>
docker-compose up -d
docker-compose logs -f controller-api
```

### Manual Installation

```bash
pip install .
pip install no8s-database-reconciler  # Install reconciler plugins

createdb operator_controller

export DB_HOST=localhost DB_PORT=5432 DB_NAME=operator_controller
export DB_USER=operator DB_PASSWORD=operator
export GITHUB_TOKEN=ghp_your_token_here  # If reconcilers use GitHub Actions

python src/main.py
```

## Usage

### Creating a Resource

Requires a resource type and a reconciler plugin installed for that type:

```bash
curl -X POST http://localhost:8000/api/v1/resources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "production-pg",
    "resource_type_name": "DatabaseCluster",
    "resource_type_version": "v1",
    "spec": {
      "engine": "postgres",
      "instance_class": "db.large",
      "storage_gb": 500,
      "replicas": 2,
      "high_availability": true
    }
  }'
```

The spec is validated against the resource type's schema (400 on failure). If no reconciler exists for the resource type, the request is rejected.

### Checking Resource Status

```bash
curl http://localhost:8000/api/v1/resources/1
curl http://localhost:8000/api/v1/resources/by-name/DatabaseCluster/v1/production-pg
```

Response includes: `id`, `name`, `resource_type_name`, `resource_type_version`, `status`, `status_message`, `generation`, `observed_generation`, `created_at`, `updated_at`, `last_reconcile_time`.

### Updating a Resource

```bash
curl -X PUT http://localhost:8000/api/v1/resources/1 \
  -H "Content-Type: application/json" \
  -d '{"spec": {"engine": "postgres", "instance_class": "db.xlarge", "storage_gb": 1000}}'
```

Triggers reconciliation: generation increments, reconciler executes changes, status updates to `ready` on completion.

### Other Resource Operations

```bash
# Reconciliation history
curl http://localhost:8000/api/v1/resources/1/history

# Reconciler outputs (e.g. GitHub Actions job/artifact info)
curl http://localhost:8000/api/v1/resources/1/outputs

# Delete (soft-delete, then destroy via reconciler, then hard-delete when finalizers clear)
curl -X DELETE http://localhost:8000/api/v1/resources/1

# Manage finalizers
curl -X PUT http://localhost:8000/api/v1/resources/1/finalizers \
  -H "Content-Type: application/json" \
  -d '{"add": ["external-controller"]}'

# Manual reconciliation trigger
curl -X POST http://localhost:8000/api/v1/resources/1/reconcile
```

## Reconciliation Flow

Two-tier model:

**Tier 1 — Main Loop (Operator):** Receives events from input plugins, caches state in PostgreSQL, starts/stops reconciler loops, tracks status and history.

**Tier 2 — Reconciler Plugin (3rd Party):** Runs its own loop per resource type — watches cache, reconciles (directly or via action plugin), reports status back.

```
Main Loop                          Reconciler Plugin Loop
─────────                          ──────────────────────
Receive event ──▶ Cache state      Watch cache ──▶ Reconcile ──▶ (Action Plugin)
                       │                  │                │
                       └──────────────────┘                │
Update status ◀── Record history ◀─────── Report ◀────────┘
```

### Resource Lifecycle

```
pending → reconciling → ready
                      ↓
                    failed → (exponential backoff) → reconciling

Deletion:
ready/failed → deleting → (destroy) → remove finalizer → hard delete (if no finalizers remain)
```

- **Pending**: Created, awaiting first reconciliation
- **Reconciling**: Reconciler executing
- **Ready**: Matches desired state
- **Failed**: Will retry with backoff
- **Deleting**: Awaiting destroy and finalizer removal

## Configuration

### Database Schema

PostgreSQL tables:

- **resource_types**: Resource schemas with OpenAPI v3 validation
- **resources**: Desired state, status, outputs, finalizers (`JSONB []`, must be empty for hard-delete)
- **admission_webhooks**: Webhook endpoints for validating/mutating before persistence
- **reconciliation_history**: Audit log of reconciliation attempts
- **locks**: Distributed locking (for future multi-controller support)

### Controller Settings

```python
controller = OperatorController(
    reconcile_interval=60,           # Check every 60 seconds
    max_concurrent_reconciles=5      # Max 5 parallel reconciliations
)
```

## Advanced Features

### Finalizers

Kubernetes-style deletion protection. A resource cannot be hard-deleted until its `finalizers` JSONB array is empty.

**Lifecycle:**
1. On creation, the action plugin name is added as a finalizer (e.g. `["github_actions"]`)
2. External controllers can add finalizers via `PUT /api/v1/resources/{id}/finalizers`
3. On `DELETE`, the resource is soft-deleted (`deleted_at` set, `status='deleting'`)
4. Controller runs the action plugin's `destroy()`, then removes its finalizer
5. Hard-deleted when no finalizers remain; stays in `deleting` if external finalizers exist

The `hard_delete_resource()` DB method includes a guard: `WHERE finalizers = '[]'::jsonb`.

### Admission Webhooks

HTTP callback webhooks that intercept resource mutations before persistence, similar to Kubernetes admission controllers.

**Types:**
- **Mutating**: Modifies the spec via JSON Patch operations. Called first, in `ordering` order.
- **Validating**: Accepts or rejects. Called after mutating webhooks. Chain stops on first denial.

**Webhook configuration fields:** `name`, `resource_type_name` (nullable = all types), `resource_type_version` (nullable = all versions), `webhook_url`, `webhook_type` (`validating`/`mutating`), `operations` (JSONB: `["CREATE", "UPDATE", "DELETE"]`), `timeout_seconds` (default 10), `failure_policy` (`Fail`/`Ignore`), `ordering` (lower = first).

**Request/Response:**

```json
// Request POST to webhook_url
{"operation": "CREATE", "resource": {"name": "...", "spec": {...}}, "old_resource": null}

// Response
{"allowed": true, "message": "Approved", "patches": [{"op": "add", "path": "/spec/key", "value": 7}]}
```

- `old_resource` is populated for `UPDATE` operations
- `patches` only applies to mutating webhooks
- `Fail` policy rejects on HTTP errors; `Ignore` proceeds as allowed

**Admission chain (`src/admission.py`):** Fetches matching webhooks from DB, runs mutating webhooks (accumulating patches), then validating webhooks (stopping on denial). Raises `AdmissionError` on denial. Called in the HTTP API handlers after validation, before persistence. Denial returns HTTP 403.

**API:** Full CRUD at `/api/v1/admission-webhooks` and `/api/v1/admission-webhooks/{id}`.

### Event Streaming

Real-time watch semantics via SSE, similar to `kubectl get --watch`.

**Event types:** `CREATED`, `MODIFIED`, `DELETED` (emitted by HTTP API), `RECONCILED` (emitted by controller).

**Architecture (`src/events.py`):** `EventBus` class provides in-memory pub/sub using `asyncio.Queue` per subscriber. Non-blocking publish drops events on full queues to prevent backpressure. `EventSubscription` is an async iterator with optional filter function.

**SSE endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/events` | All events (optional `resource_type` filter) |
| GET | `/api/v1/resources/{id}/events` | Events for a single resource |

Both use FastAPI's `StreamingResponse` with `text/event-stream` content type.

### Drift Detection

Re-reconciliation every 5 minutes for `ready` resources. The reconciler determines if drift occurred and reconciles automatically.

### Exponential Backoff (TODO)

Failed reconciliations retry: 1min, 2min, 4min, ... up to ~17 hours.

### Generation Tracking

- **generation**: Increments on spec change
- **observed_generation**: Last successfully reconciled generation

Reconciliation triggers when `generation > observed_generation`.

## Comparison to Kubernetes

| Kubernetes                       | Operator Controller                                             |
|----------------------------------|-----------------------------------------------------------------|
| etcd                             | PostgreSQL                                                      |
| CustomResourceDefinitions (CRDs) | Resource Types with OpenAPI v3 schemas                          |
| Custom Resources                 | Resources (validated against resource type schema)              |
| Controller Manager               | Main loop (controller.py) — event handling, caching, dispatch   |
| Controllers/Operators            | Reconciler plugins (3rd party pip packages)                     |
| kubectl apply                    | POST /api/v1/resources                                          |
| kubectl get                      | GET /api/v1/resources                                           |
| kubectl get --watch              | GET /api/v1/events (SSE)                                        |
| Finalizers                       | JSONB finalizers array, cleared before hard-delete              |
| Admission Webhooks               | HTTP callback webhooks with mutating/validating support         |
| Status conditions                | status + status_message fields                                  |

## Monitoring

```bash
curl http://localhost:8000/health
```

**Metrics (TODO):** Prometheus endpoint, reconciliation duration histograms, success/failure rates, queue depth.

## Troubleshooting

```bash
# Resource stuck in "reconciling" — check history
curl http://localhost:8000/api/v1/resources/{id}/history

# Force re-reconciliation
curl -X POST http://localhost:8000/api/v1/resources/{id}/reconcile

# Database connectivity
psql -h localhost -U operator -d operator_controller -c "SELECT 1;"
```

## Development

### Codestyle

After every commit run `flake8` and `black .` to ensure codestyle compliance. Classes with parameter and return typehints are preferred.

### Running Tests

```bash
pytest tests/
```

### Code Structure

```
src/
├── controller.py           # Main loop: event handling, caching, dispatch
├── db.py                   # PostgreSQL database manager
├── validation.py           # OpenAPI v3 schema validation
├── admission.py            # Admission webhook chain
├── events.py               # EventBus and SSE event streaming
├── plugins/
│   ├── inputs/http/        # HTTP Input plugin
│   ├── actions/github_actions/  # GitHub Actions plugin
│   └── reconcilers/base.py     # Base class for reconciler plugins
└── migrations/             # SQL migration files
tests/                      # Test suite
```

## Developing a Reconciler Plugin

Reconciler plugins are separate pip packages that integrate via Python entry points.

### Base Class

Subclass `ReconcilerPlugin` and implement the required methods:

```python
from no8s_operator.plugins.reconcilers.base import ReconcilerPlugin


class DatabaseClusterReconciler(ReconcilerPlugin):
    """Reconciler for DatabaseCluster resources."""

    @property
    def name(self) -> str:
        return "database_cluster"

    @property
    def resource_types(self) -> list[str]:
        return ["DatabaseCluster"]

    async def start(self, ctx: ReconcilerContext) -> None:
        while not ctx.shutdown_event.is_set():
            resources = await ctx.get_resources_needing_reconciliation()
            for resource in resources:
                await self.reconcile(resource, ctx)
            await asyncio.sleep(self.reconcile_interval)

    async def reconcile(self, resource: dict, ctx: ReconcilerContext) -> None:
        await ctx.update_status(resource["id"], "reconciling")

        # Option A: Use an action plugin
        github = ctx.get_action_plugin("github_actions")
        await github.apply(action_ctx, workspace)

        # Option B: Call an API directly
        await httpx.post("https://api.example.com/clusters", json=resource["spec"])

        await ctx.update_status(resource["id"], "ready")

    async def stop(self) -> None:
        ...
```

### Entry Point Registration

```toml
[project.entry-points.'no8s.reconcilers']
database_cluster = 'no8s_database:DatabaseClusterReconciler'
```

After `pip install`, the operator auto-discovers and registers the reconciler at startup via `importlib.metadata.entry_points(group='no8s.reconcilers')`.

### ReconcilerContext

Passed to each reconciler on startup, providing access to:

```python
resources = await ctx.get_resources_needing_reconciliation()  # Read cache
await ctx.update_status(resource_id, "ready", message="Done")  # Report status
plugin = ctx.get_action_plugin("github_actions")  # Optional action plugin
```

## Future Enhancements

- [ ] Multi-controller support with leader election
- [ ] Roles and authentication
- [ ] Plan approval workflow
- [ ] Prometheus metrics
- [ ] GitOps integration (watch Git repos for changes)
- [ ] Policy enforcement (OPA integration)
- [ ] Slack/email notifications
- [ ] Stable plugin API with backwards-compatibility guarantees
- [ ] Runtime reconciler hot-reload (detect newly installed packages without restart)
