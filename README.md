# no8s-operator

A Kubernetes-style controller for managing infrastructure without Kubernetes. This operator implements a reconciliation loop that continuously ensures your infrastructure matches the desired state defined in your resource specifications.

## WARNING
This project is being largely "vibe-coded" with minimal human review during the build out phase as I test the limits of
Claude Code. The intention will be after a first phase to do a full human review of the code. It is the intention to make
this fully production ready! But be warned if you're looking at it during these early development phases.

## Overview

no8s-operator brings the power of Kubernetes-style declarative infrastructure management to environments where running a full Kubernetes cluster isn't practical or desired. It supports:

- **Declarative Infrastructure**: Define desired state; the controller ensures it matches reality
- **Resource Types with Schema Validation**: Define resource types with OpenAPI v3 schemas (similar to Kubernetes CRDs)
- **Plugin Architecture**: Extensible inputs (HTTP API, polling, queues) and actions (GitHub Actions, GitLab, HTTP)
- **Automatic Reconciliation**: Continuous drift detection and correction
- **Status Conditions**: Kubernetes-style named conditions (`Ready`, `Reconciling`, `Degraded`) plus custom conditions set by reconciler plugins
- **Audit History**: Complete history of all reconciliation attempts

In some environments for a variety of reasons Kubernetes isn't an option - but you still want the benefits of managing
not just infrastructure through some sort of pipeline (whether Terraform/Ansible driven or similar). However, at this
point managing the deployed cluster is often done with a combination of manual approaches and some scripts. In
kubernetes this is where operators come in, this project aims to give that equivalent option outside of the kubernetes
ecosystem.

## Architecture

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
    │PostgreSQL│                             │  Github  │
    │          │                             │  Action  │
    │ Resource │                             │ Plugins  │
    │  Store   │                             │          │
    └──────────┘                             └──────────┘
```

## Prerequisites

- Python 3.11+
- PostgreSQL 16+
- GitHub personal access token with `repo` and `workflow` scopes (for GitHub Actions plugin)

## Quick Start

### Using Docker Compose

```bash
# Clone the repository
git clone <repo-url>
cd no8s-operator

# Start all services
docker-compose up -d

# Check logs
docker-compose logs -f controller-api
```

### Manual Installation

```bash
# Install dependencies
pip install .

# For development
pip install ".[dev]"

# Set up PostgreSQL
createdb operator_controller

# Configure environment variables (see .env.example)
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=operator_controller
export DB_USER=operator
export DB_PASSWORD=operator

# Run the API server
python main.py
```

## Usage

### Create a Resource Type

Resource types define the schema for resources using OpenAPI v3 JSON Schema:

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
        "engine": { "type": "string", "enum": ["postgres", "mysql", "mariadb"] },
        "engine_version": { "type": "string" },
        "instance_class": { "type": "string" },
        "storage_gb": { "type": "integer", "minimum": 10, "maximum": 10000 },
        "replicas": { "type": "integer", "minimum": 0, "maximum": 5, "default": 0 },
        "backup_retention_days": { "type": "integer", "default": 7 },
        "high_availability": { "type": "boolean", "default": false }
      }
    }
  }'
```

### Create a Resource

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
      "high_availability": true
    }
  }'
```

### Check Resource Status

```bash
curl http://localhost:8000/api/v1/resources/1
```

The response includes a `conditions` array alongside the `status` phase field:

```json
{
  "id": 1,
  "name": "production-pg",
  "status": "ready",
  "conditions": [
    {"type": "Ready",       "status": "True",    "reason": "ReconcileSuccess", ...},
    {"type": "Reconciling", "status": "False",   "reason": "ReconcileComplete", ...},
    {"type": "Degraded",    "status": "False",   "reason": "NoErrors", ...}
  ]
}
```

### View Reconciliation History

```bash
curl http://localhost:8000/api/v1/resources/1/history
```

## Resource Lifecycle

Resources track state in two complementary ways:

**Phase** — the `status` field, a coarse-grained state machine:

```
pending → reconciling → ready
                      ↓
                    failed → (exponential backoff) → reconciling

Deletion:
ready/failed → deleting → (destroy + finalizer removal) → hard delete
```

**Conditions** — the `conditions` array, named boolean states with `lastTransitionTime`:

| Condition | Meaning |
|-----------|---------|
| `Ready` | Resource matches desired state (`True`) or has errors (`False`) |
| `Reconciling` | Actively being reconciled right now |
| `Degraded` | Resource is in an error state |

Reconciler plugins can add domain-specific conditions (e.g. `ReplicationHealthy`, `SchemaApplied`) via `ctx.set_condition()`.

## Project Structure

```
.
├── main.py                 # Application entry point
├── controller.py           # Main reconciliation loop
├── db.py                   # PostgreSQL database manager
├── validation.py           # OpenAPI v3 schema validation
├── config.py               # Configuration management
├── plugins/
│   ├── inputs/
│   │   └── http/
│   │       └── api.py      # FastAPI REST API
│   └── actions/
│       └── github_actions/
│           └── executor.py # GitHub Actions workflow trigger
├── pyproject.toml          # Python dependencies
├── Dockerfile              # Container image
└── docker-compose.yml      # Local development setup
```

## Configuration

Environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_HOST` | PostgreSQL host | `localhost` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `DB_NAME` | Database name | `operator_controller` |
| `DB_USER` | Database user | `operator` |
| `DB_PASSWORD` | Database password | - |
| `GITHUB_TOKEN` | GitHub personal access token | - |
| `GITHUB_ACTIONS_TIMEOUT` | Workflow timeout in seconds | `3600` |

## Development

```bash
# Install dev dependencies
pip install ".[dev]"

# Format code
black .

# Lint
flake8
```

## License

GPL-3.0-or-later