# Operator Controller

A Kubernetes-style controller for managing infrastructure. The operator acts as a central coordinator: it receives resource events via input plugins (HTTP API, queue listeners, polling), caches resource state, and delegates reconciliation to **3rd party reconciler plugins** — each responsible for one or more resource types.

Reconciler plugins are installed as separate pip packages and auto-discovered via Python entry points. Each reconciler owns its own reconciliation loop for its resource types and may optionally use the operator's action plugin system (GitHub Actions, GitLab Pipelines, etc.) to execute changes.

Each method for receiving events (inputs) and performing reconciliation (reconcilers) is plugin driven.

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
│                                    │                             │
│              ┌─────────────────────┼─────────────────────┐       │
│              │                     │                     │       │
│              ▼                     ▼                     ▼       │
│  ┌───────────────────┐ ┌───────────────────┐ ┌─────────────────┐ │
│  │ Reconciler Plugin │ │ Reconciler Plugin │ │Reconciler Plugin│ │
│  │ (pip: no8s-db)    │ │ (pip: no8s-k8s)   │ │(pip: no8s-dns)  │ │
│  │                   │ │                   │ │                 │ │
│  │ ResourceType:     │ │ ResourceType:     │ │ ResourceType:   │ │
│  │  DatabaseCluster  │ │  K8sCluster       │ │  DnsRecord      │ │
│  └────────┬──────────┘ └────────┬──────────┘ └───────┬─────────┘ │
│           │ (optional)          │ (optional)         │ (direct)  │
└───────────┼─────────────────────┼────────────────────┼───────────┘
            ▼                     ▼                    ▼
    ┌──────────────┐     ┌──────────────┐      ┌────────────┐
    │ Action Plugin│     │ Action Plugin│      │  External  │
    │ (GitHub      │     │ (Terraform)  │      │  API       │
    │  Actions)    │     │              │      │            │
    └──────────────┘     └──────────────┘      └────────────┘
            │
            ▼
    ┌──────────┐
    │PostgreSQL│
    │ Resource │
    │  Store   │
    └──────────┘
```

### Components

1. **Main Loop (`controller.py`)** - Receives resource events from input plugins, caches resource state, and dispatches to the appropriate reconciler plugin. Manages lifecycle, status tracking, and audit history.
2. **Database Manager (`db.py`)** - PostgreSQL operations for storing resource definitions, cached state, and metadata
3. **Input Plugins** - Pluggable sources for resource events:
   - **HTTP API (`plugins/inputs/http/`)** - REST API for creating/updating resources directly
4. **Reconciler Plugins** - 3rd party pip packages that own reconciliation logic per resource type. Discovered via Python entry points.
5. **Action Plugins** - Optional executors available to reconciler plugins:
   - **GitHub Actions (`plugins/actions/github_actions/`)** - Triggers GitHub Actions workflows and monitors completion

## Key Features

- **Declarative Infrastructure**: Define desired state; reconciler plugins ensure it matches reality
- **Resource Types with Schema Validation**: Define resource types with OpenAPI v3 schemas (similar to Kubernetes CRDs)
- **3rd Party Reconcilers**: Reconciliation logic is owned by separately installable pip packages, auto-discovered via Python entry points
- **Extendable**: Input plugins, reconciler plugins, and action plugins can all be extended independently
- **PostgreSQL Metadata**: Resource definitions, cached state, reconciliation history, and locks (equivalent of ETCD in Kubernetes)
- **Automatic Reconciliation**: Continuous drift detection and correction via reconciler plugins
- **Exponential Backoff**: Failed reconciliations retry with intelligent backoff
- **Concurrent Reconciliation**: Multiple resources reconciled in parallel
- **Audit History**: Complete history of all reconciliation attempts

## Plugin Architecture

The controller is designed around three plugin types: **input plugins** (how events are received), **reconciler plugins** (how reconciliation is performed per resource type), and **action plugins** (optional executors that reconcilers can delegate to).

### Input Plugins

Input plugins define how the controller receives events that trigger reconciliation:

- **HTTP API** (implemented) - REST API for creating/updating resources directly
- **HTTP Polling** (planned) - Poll external APIs for state changes
- **Queue Listeners** (planned) - Listen on message queues (SQS, RabbitMQ, etc.)

### Reconciler Plugins

Reconciler plugins are **3rd party packages installed via pip** that own the reconciliation logic for one or more resource types. They are discovered automatically using Python entry points.

**Discovery mechanism:** The operator scans the `no8s.reconcilers` entry point group at startup. Any installed pip package that declares this entry point is automatically registered:

```toml
# In the 3rd party package's pyproject.toml
[project.entry-points.'no8s.reconcilers']
database_cluster = 'no8s_database:DatabaseClusterReconciler'
```

```python
# The operator discovers reconcilers at startup
from importlib.metadata import entry_points

for ep in entry_points(group='no8s.reconcilers'):
    ReconcilerClass = ep.load()
    # Register for the resource types it declares
```

**Key characteristics:**
- Each reconciler declares which resource type(s) it handles
- Reconcilers run their own continuous reconciliation loop, reading from the operator's resource cache
- Reconcilers **may optionally** use action plugins (GitHub Actions, Terraform, etc.) or implement reconciliation directly (e.g. calling an external API)
- The operator starts/stops reconciler loops alongside its own main loop
- Multiple reconcilers can coexist, each handling different resource types

### Action Plugins

Action plugins are optional executors that reconciler plugins can use to perform changes. They remain part of the core operator and are available to any reconciler that needs them:

- **GitHub Actions** (implemented) - Trigger GitHub Actions workflows and monitor completion
- **GitLab Pipelines** (planned) - Trigger GitLab CI/CD pipelines
- **Terraform** (planned) - Execute Terraform init/plan/apply
- **HTTP API** (planned) - Call external APIs to perform actions

## Resource Types

Resource Types are similar to Kubernetes CustomResourceDefinitions (CRDs). They define the schema for resources using OpenAPI v3 JSON Schema. All resources must reference a resource type, and their specs are validated against the schema.

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
      "required": ["engine", "engine_version", "instance_class", "storage_gb"],
      "properties": {
        "engine": {
          "type": "string",
          "enum": ["postgres", "mysql", "mariadb"],
          "description": "Database engine type"
        },
        "engine_version": {
          "type": "string",
          "description": "Database engine version"
        },
        "instance_class": {
          "type": "string",
          "description": "Instance size class (e.g. db.small, db.medium, db.large)"
        },
        "storage_gb": {
          "type": "integer",
          "minimum": 10,
          "maximum": 10000,
          "description": "Storage size in GB"
        },
        "replicas": {
          "type": "integer",
          "minimum": 0,
          "maximum": 5,
          "default": 0,
          "description": "Number of read replicas"
        },
        "backup_retention_days": {
          "type": "integer",
          "minimum": 1,
          "maximum": 35,
          "default": 7,
          "description": "Number of days to retain backups"
        },
        "high_availability": {
          "type": "boolean",
          "default": false,
          "description": "Enable multi-AZ high availability"
        }
      }
    }
  }'
```

### Listing Resource Types

```bash
curl http://localhost:8000/api/v1/resource-types

# Filter by name
curl http://localhost:8000/api/v1/resource-types?name=DatabaseCluster
```

### Getting a Resource Type

```bash
# By ID
curl http://localhost:8000/api/v1/resource-types/1

# By name and version
curl http://localhost:8000/api/v1/resource-types/DatabaseCluster/v1
```

### Resource Type Versioning

Resource types support versioning (e.g., v1, v1beta1, v2). Each version can have a different schema:

```bash
# Create v1beta1
curl -X POST http://localhost:8000/api/v1/resource-types \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DatabaseCluster",
    "version": "v1beta1",
    "schema": { ... }
  }'
```

## Installation

### Prerequisites

- Python 3.11+
- PostgreSQL 16+

**For GitHub Actions plugin:**
- GitHub personal access token with `repo` and `workflow` scopes

### Quick Start with Docker

```bash
# Clone the repository
git clone <repo-url>

# Start all services
docker-compose up -d

# Check logs
docker-compose logs -f controller-api
```

### Manual Installation

```bash
# Install the operator
pip install .

# Install reconciler plugins for your resource types
pip install no8s-database-reconciler

# Set up PostgreSQL
createdb operator_controller

# Configure environment variables
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=operator_controller
export DB_USER=operator
export DB_PASSWORD=operator

# GitHub Actions plugin configuration (if reconcilers use it)
export GITHUB_TOKEN=ghp_your_token_here

# Run the API server
python src/main.py
```

## Usage

### Creating a Resource

First, ensure a resource type exists (see Resource Types section above) and a reconciler plugin is installed for that resource type. Then create a resource by POSTing to the API:

```bash
curl -X POST http://localhost:8000/api/v1/resources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "production-pg",
    "resource_type_name": "DatabaseCluster",
    "resource_type_version": "v1",
    "spec": {
      "engine": "postgres",
      "engine_version": "16.2",
      "instance_class": "db.large",
      "storage_gb": 500,
      "replicas": 2,
      "backup_retention_days": 14,
      "high_availability": true
    }
  }'
```

The spec is validated against the resource type's OpenAPI v3 schema. If validation fails, the request is rejected with a 400 error.
If no reconciler exists for the resource type then the request is rejected.
The operator automatically dispatches to the reconciler plugin registered for the `DatabaseCluster` resource type.

### Checking Resource Status

```bash
# Get resource by ID
curl http://localhost:8000/api/v1/resources/1

# Get resource by name (requires resource type)
curl http://localhost:8000/api/v1/resources/by-name/DatabaseCluster/v1/production-pg

# Response
{
  "id": 1,
  "name": "production-pg",
  "resource_type_name": "DatabaseCluster",
  "resource_type_version": "v1",
  "status": "ready",
  "status_message": "Reconciliation successful",
  "generation": 1,
  "observed_generation": 1,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:31:00Z",
  "last_reconcile_time": "2024-01-15T10:31:00Z"
}
```

### Updating a Resource

```bash
curl -X PUT http://localhost:8000/api/v1/resources/1 \
  -H "Content-Type: application/json" \
  -d '{
    "spec": {
      "engine": "postgres",
      "engine_version": "16.2",
      "instance_class": "db.xlarge",
      "storage_gb": 1000,
      "replicas": 3,
      "backup_retention_days": 14,
      "high_availability": true
    }
  }'
```

The updated spec is validated against the resource type's schema. This triggers a new reconciliation. The controller will:
1. Detect the change (generation incremented)
2. Dispatch to the reconciler plugin registered for this resource type
3. The reconciler executes changes (directly or via an action plugin)
4. Update the status to `ready` once complete

### Viewing Reconciliation History

```bash
curl http://localhost:8000/api/v1/resources/1/history

# Response
[
  {
    "id": 5,
    "resource_id": 1,
    "generation": 2,
    "success": true,
    "phase": "completed",
    "error_message": null,
    "resources_created": 0,
    "resources_updated": 1,
    "resources_deleted": 0,
    "reconcile_time": "2024-01-15T10:35:00Z"
  },
  {
    "id": 4,
    "resource_id": 1,
    "generation": 1,
    "success": true,
    "phase": "completed",
    "error_message": null,
    "resources_created": 1,
    "resources_updated": 0,
    "resources_deleted": 0,
    "reconcile_time": "2024-01-15T10:31:00Z"
  }
]
```

### Getting Reconciler Outputs

Reconciler plugins can produce outputs. For a reconciler using GitHub Actions, this returns workflow job and artifact information:

```bash
curl http://localhost:8000/api/v1/resources/1/outputs

# Response
{
  "outputs": {
    "jobs": [
      {
        "name": "deploy",
        "status": "completed",
        "conclusion": "success",
        "started_at": "2024-01-15T10:30:00Z",
        "completed_at": "2024-01-15T10:35:00Z"
      }
    ],
    "artifacts": [
      {
        "name": "build-output",
        "size_in_bytes": 1234567
      }
    ]
  }
}
```

### Deleting a Resource

```bash
curl -X DELETE http://localhost:8000/api/v1/resources/1
```

This triggers the reconciler plugin's destroy logic. For resources using GitHub Actions, this cancels any running workflow.

### Manual Reconciliation Trigger

```bash
curl -X POST http://localhost:8000/api/v1/resources/1/reconcile
```

Forces an immediate reconciliation (useful for drift detection).

## Reconciliation Flow

The operator uses a two-tier reconciliation model:

### Tier 1: Main Loop (Operator)

The main loop is responsible for:

1. **Receiving events** from input plugins (HTTP API, queues, polling)
2. **Caching resource state** in PostgreSQL
3. **Starting/stopping** reconciler plugin loops at operator lifecycle boundaries
4. **Tracking status** and recording reconciliation history

### Tier 2: Reconciler Plugin (3rd Party)

Each reconciler plugin runs its own continuous reconciliation loop for its resource types:

1. **Watch**: Read resources from the operator's cache that need reconciliation
2. **Reconcile**: Compare desired state against actual state and take action — either directly or via an action plugin
3. **Report**: Update resource status back through the operator

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
```

- **Pending**: Resource created, waiting for first reconciliation
- **Reconciling**: Reconciler plugin is currently executing
- **Ready**: Successfully reconciled, matches desired state
- **Failed**: Reconciliation failed, will retry with backoff

## Configuration

### Database Schema

The system uses these PostgreSQL tables:

- **resource_types**: Defines resource schemas (similar to CRDs) with OpenAPI v3 validation
- **resources**: Stores desired state, status, and outputs (references a resource_type)
- **reconciliation_history**: Audit log of all reconciliation attempts
- **locks**: Distributed locking (for future multi-controller support)

### Controller Settings

Configurable via constructor parameters:

```python
controller = OperatorController(
    reconcile_interval=60,           # Check every 60 seconds
    max_concurrent_reconciles=5      # Max 5 parallel reconciliations
)
```

## Advanced Features

### Drift Detection

The main loop schedules re-reconciliation every 5 minutes for resources in `ready` state. The reconciler plugin's plan phase determines if drift has occurred. If drift is detected, it automatically reconciles.

### Exponential Backoff (TODO)

Failed reconciliations retry with exponential backoff:
- 1st retry: 1 minute
- 2nd retry: 2 minutes
- 3rd retry: 4 minutes
- ...
- Max: 1024 minutes (~17 hours)

### Generation Tracking

Similar to Kubernetes:
- **generation**: Increments on spec change (desired state change)
- **observed_generation**: Last successfully reconciled generation

When `generation > observed_generation`, reconciliation is triggered.

## Comparison to Kubernetes

| Kubernetes                       | Operator Controller                                          |
|----------------------------------|--------------------------------------------------------------|
| etcd                             | PostgreSQL                                                   |
| CustomResourceDefinitions (CRDs) | Resource Types with OpenAPI v3 schemas                       |
| Custom Resources                 | Resources (validated against resource type schema)           |
| Controller Manager               | Main loop (controller.py) — event handling, caching, dispatch|
| Controllers/Operators            | Reconciler plugins (3rd party pip packages)                  |
| kubectl apply                    | POST /api/v1/resources                                       |
| kubectl get                      | GET /api/v1/resources                                        |
| Finalizers                       | Reconciler plugin destroy on deletion                        |
| Status conditions                | status + status_message fields                               |

## Monitoring

### Health Check

```bash
curl http://localhost:8000/health
```

### Metrics (TODO)

Future enhancements:
- Prometheus metrics endpoint
- Reconciliation duration histograms
- Success/failure rates
- Queue depth

## Troubleshooting

### Resource Stuck in "reconciling"

Check the reconciliation history for errors:
```bash
curl http://localhost:8000/api/v1/resources/{id}/history
```

Manually trigger reconciliation:
```bash
curl -X POST http://localhost:8000/api/v1/resources/{id}/reconcile
```

### Database Connection Issues

Verify PostgreSQL is running and accessible:
```bash
psql -h localhost -U operator -d operator_controller -c "SELECT 1;"
```

## Development

### Codestyle
After every commit run `flake8` and `black .` to ensure codestyle compliance is met. No issues should be found.

Classes with parameter and return typehints are preferred.

### Running Tests

```bash
pytest tests/
```

### Code Structure

```
.
├── src/
│   ├── controller.py           # Main loop: event handling, caching, dispatch
│   ├── db.py                   # PostgreSQL database manager
│   ├── validation.py           # OpenAPI v3 schema validation
│   ├── plugins/
│   │   ├── inputs/
│   │   │   └── http/           # HTTP Input plugin
│   │   ├── actions/
│   │   │   └── github_actions/ # GitHub Actions plugin (used by reconcilers)
│   │   └── reconcilers/
│   │       └── base.py         # Base class for reconciler plugins
│   └── migrations/             # SQL migration files
├── tests/                      # Contains the test suite for the project
├── pyproject.toml              # Python dependencies
├── Dockerfile                  # Container image
├── docker-compose.yml          # Local development setup
└── CLAUDE.md
```

## Developing a Reconciler Plugin

Reconciler plugins are separate pip packages that integrate with the operator via Python entry points. This allows independent development, testing, and release cycles.

### Base Class

Reconciler plugins must subclass `ReconcilerPlugin` and implement the required methods:

```python
from no8s_operator.plugins.reconcilers.base import ReconcilerPlugin


class DatabaseClusterReconciler(ReconcilerPlugin):
    """Reconciler for DatabaseCluster resources."""

    @property
    def name(self) -> str:
        return "database_cluster"

    @property
    def resource_types(self) -> list[str]:
        """Resource type names this reconciler handles."""
        return ["DatabaseCluster"]

    async def start(self, ctx: ReconcilerContext) -> None:
        """Start the reconciliation loop.

        ctx provides access to the resource cache and action plugin registry.
        The reconciler should run its own loop, watching the cache for
        resources that need reconciliation.
        """
        while not ctx.shutdown_event.is_set():
            resources = await ctx.get_resources_needing_reconciliation()
            for resource in resources:
                await self.reconcile(resource, ctx)
            await asyncio.sleep(self.reconcile_interval)

    async def reconcile(self, resource: dict, ctx: ReconcilerContext) -> None:
        """Reconcile a single resource.

        Compare desired state against actual state and take action.
        Report status back via ctx.update_status().
        """
        await ctx.update_status(resource["id"], "reconciling")

        # Option A: Use an action plugin
        github = ctx.get_action_plugin("github_actions")
        await github.apply(action_ctx, workspace)

        # Option B: Call an API directly
        await httpx.post("https://api.example.com/clusters", json=resource["spec"])

        await ctx.update_status(resource["id"], "ready")

    async def stop(self) -> None:
        """Graceful shutdown. Clean up any resources."""
        ...
```

### Entry Point Registration

Declare the entry point in your package's `pyproject.toml`:

```toml
[project]
name = "no8s-database-reconciler"
version = "0.1.0"
dependencies = ["no8s-operator"]

[project.entry-points.'no8s.reconcilers']
database_cluster = 'no8s_database:DatabaseClusterReconciler'
```

After `pip install no8s-database-reconciler`, the operator will automatically discover and register the reconciler at startup.

### ReconcilerContext

The operator passes a `ReconcilerContext` to each reconciler on startup. This provides access to the resource cache, status reporting, and optionally the action plugin registry:

```python
# Read from the resource cache
resources = await ctx.get_resources_needing_reconciliation()

# Report status back to the operator
await ctx.update_status(resource_id, "ready", message="Reconciled successfully")

# Optionally use an action plugin
plugin = ctx.get_action_plugin("github_actions")
```

Reconcilers can implement reconciliation logic entirely on their own without using action plugins.

## Future Enhancements

- [ ] Multi-controller support with leader election
- [ ] Roles and authentication
- [ ] Terraform action plugin
- [ ] Plan approval workflow
- [ ] Prometheus metrics
- [ ] GitOps integration (watch Git repos for changes)
- [ ] Policy enforcement (OPA integration)
- [ ] Slack/email notifications
- [ ] Stable plugin API with backwards-compatibility guarantees
- [ ] Runtime reconciler hot-reload (detect newly installed packages without restart)
