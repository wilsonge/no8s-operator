# Operator Controller

A Kubernetes-style controller for managing infrastructure. This controller implements a reconciliation loop that continuously ensures your infrastructure matches the desired state defined in your resource specifications.

This may be achieved through direct HTTP requests (similar to creating a kubernetes CRD), or by polling other HTTP APIs
for events or by listening on queues (such as SQS) for events.

Potential ways to reconcile this loop can be through triggering gitlab pipelines, github actions, or triggering an API.

Each method for receiving and actioning events should be plugin driven.

## Architecture

The system follows the Kubernetes controller pattern:

```
┌─────────────────────────────────────────────────────────────┐
│                    Operator Controller                      │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │         Reconciliation Loop (controller.py)          │   │
│  │                                                      │   │
│  │  1. Watch for resources needing reconciliation       │   │
│  │  2. Compare desired state vs actual state            │   │
│  │  3. Execute Action                                   │   │
│  │  4. Update status and metadata                       │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
           │                                         │
           ▼                                         ▼
    ┌──────────┐                             ┌──────────┐
    │PostgreSQL│                             │  GitHub  │
    │          │                             │ Actions  │
    │ Resource │                             │ Trigger  │
    │  Store   │                             │          │
    └──────────┘                             └──────────┘
```

### Components

1. **Controller (`controller.py`)** - Main reconciliation loop that watches for changes and dispatches to action plugins
2. **Database Manager (`db.py`)** - PostgreSQL operations for storing resource definitions and metadata
3. **API Server (`api.py`)** - REST API for submitting and managing resources (input plugin)
4. **Action Plugins** - Pluggable executors for reconciliation actions:
   - **GitHub Actions (`github_actions/executor.py`)** - Triggers GitHub Actions workflows and monitors completion

## Key Features

- **Declarative Infrastructure**: Define desired state; controller ensures it matches reality
- **Resource Types with Schema Validation**: Define resource types with OpenAPI v3 schemas (similar to Kubernetes CRDs)
- **Extendable**: Plugins can be used to extend it for both inputs and outputs.
- **PostgreSQL Metadata**: Resource definitions, reconciliation history, and locks (equivalent of ETCD in Kubernetes)
- **Automatic Reconciliation**: Continuous drift detection and correction
- **Exponential Backoff**: Failed reconciliations retry with intelligent backoff
- **Concurrent Reconciliation**: Multiple resources reconciled in parallel
- **Audit History**: Complete history of all reconciliation attempts

## Plugin Architecture

The controller is designed around a plugin-based architecture for both inputs (how events are received) and actions (how reconciliation is performed).

### Input Plugins

Input plugins define how the controller receives events that trigger reconciliation:

- **HTTP API** (implemented) - REST API for creating/updating resources directly
- **HTTP Polling** (planned) - Poll external APIs for state changes
- **Queue Listeners** (planned) - Listen on message queues (SQS, RabbitMQ, etc.)

### Action Plugins

Action plugins define how the controller performs reconciliation:

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
# Install dependencies
pip install .

# Set up PostgreSQL
createdb operator_controller

# Configure environment variables
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=operator_controller
export DB_USER=operator
export DB_PASSWORD=operator

# GitHub Actions plugin configuration
export GITHUB_TOKEN=ghp_your_token_here

# Run the API server
python src/main.py
```

## Usage

The following examples demonstrate using the GitHub Actions plugin. Other action plugins will have similar patterns but different resource specifications.

### Creating a Resource

First, ensure a resource type exists (see Resource Types section above). Then create a resource by POSTing to the API:

```bash
curl -X POST http://localhost:8000/api/v1/resources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "production-pg",
    "resource_type_name": "DatabaseCluster",
    "resource_type_version": "v1",
    "action_plugin": "github_actions",
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
  "action_plugin": "github_actions",
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
2. Dispatch to the configured action plugin
3. Execute the action (e.g. trigger a workflow to resize the cluster)
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

### Getting Action Outputs

Action plugins can produce outputs. For GitHub Actions resources, this returns workflow job and artifact information:

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

This triggers the action plugin's destroy/cleanup logic. For GitHub Actions resources, this cancels any running workflow.

### Manual Reconciliation Trigger

```bash
curl -X POST http://localhost:8000/api/v1/resources/1/reconcile
```

Forces an immediate reconciliation (useful for drift detection).

## Reconciliation Flow

The controller implements the following reconciliation phases:

1. **Initializing**: Prepares workspace and initializes the action plugin
2. **Planning**: Action plugin determines what changes are needed
3. **Applying**: Action plugin executes the changes
4. **Completed**: Updates status and metadata

### Resource Lifecycle

```
pending → reconciling → ready
                      ↓
                    failed → (exponential backoff) → reconciling
```

- **Pending**: Resource created, waiting for first reconciliation
- **Reconciling**: Action plugin is currently executing
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

The controller automatically detects drift every 5 minutes for resources in `ready` state. If drift is detected, it automatically reconciles.

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

| Kubernetes | Operator Controller |
|------------|---------------------|
| etcd | PostgreSQL |
| CustomResourceDefinitions (CRDs) | Resource Types with OpenAPI v3 schemas |
| Custom Resources | Resources (validated against resource type schema) |
| Controllers/Operators | Reconciliation loop + action plugins |
| kubectl apply | POST /api/v1/resources |
| kubectl get | GET /api/v1/resources |
| Finalizers | Action plugin destroy on deletion |
| Status conditions | status + status_message fields |

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
│   ├── controller.py           # Main reconciliation loop
│   ├── db.py                   # PostgreSQL database manager
│   ├── validation.py           # OpenAPI v3 schema validation
│   ├── plugins/
│   │   └── inputs/
│   │       └── http/           # Contains the HTTP Input plugin
│   │   └── actions/
│   │       └── github_actions/ # Contains the github actions Output plugin
│   └── migrations/             # SQL migration files
├── tests/                      # Contains the test suite for the project
├── pyproject.toml              # Python dependencies
├── Dockerfile                  # Container image
├── docker-compose.yml          # Local development setup
└── CLAUDE.md
```

## Future Enhancements

- [ ] Multi-controller support with leader election
- [ ] Terraform action plugin
- [ ] Plan approval workflow
- [ ] Prometheus metrics
- [ ] GitOps integration (watch Git repos for changes)
- [ ] Policy enforcement (OPA integration)
- [ ] Slack/email notifications
- [ ] Strong API between plugins so that plugins have a clear integration approach with B/C guarantees
