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
3. **Auth Manager (`auth.py`)** - JWT creation/validation, bcrypt password hashing, FastAPI dependency functions for RBAC, custom role permission checks.
4. **LDAP Sync (`ldap_sync.py`)** - Optional LDAP integration for syncing users from a directory.
5. **Input Plugins** - Pluggable event sources. Currently: **HTTP API (`plugins/inputs/http/`)**.
6. **Reconciler Plugins** - 3rd party pip packages discovered via entry points, owning reconciliation logic per resource type.
7. **Action Plugins** - Optional executors for reconcilers. Currently: **GitHub Actions (`plugins/actions/github_actions/`)**.

## Plugin Architecture

Three plugin types: **input plugins** (event sources), **reconciler plugins** (reconciliation per resource type), and **action plugins** (optional executors).

### Input Plugins

- **HTTP API** (implemented) - REST API for creating/updating resources
- **HTTP Polling** (planned) - Poll external APIs for state changes
- **Queue Listeners** (planned) - SQS, RabbitMQ, etc.

### Reconciler Plugins

3rd party pip packages discovered via the `no8s.reconcilers` entry point group. See [`docs/writing-a-reconciler.md`](docs/writing-a-reconciler.md).

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

Resource types define schemas using OpenAPI v3 JSON Schema (similar to Kubernetes CRDs). All resources must reference a resource type; specs are validated against the schema. Resource types support versioning — each version can have a different schema.

See [`docs/resource-types.md`](docs/resource-types.md) for the API reference.

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

## Advanced Features

### Finalizers

Kubernetes-style deletion protection. A resource cannot be hard-deleted until its `finalizers` JSONB array is empty.

**Lifecycle:**
1. On creation, the reconciler name is added as a finalizer (e.g. `["database_cluster"]`)
2. External controllers can add finalizers via `PUT /api/v1/resources/{id}/finalizers`
3. On `DELETE`, the resource is soft-deleted (`deleted_at` set, `status='deleting'`)
4. Reconciler destroys external resources, then removes its finalizer
5. Hard-deleted when no finalizers remain; stays in `deleting` if external finalizers exist

### Event Streaming

Real-time watch semantics via SSE (`src/events.py`). `EventBus` provides in-memory pub/sub using `asyncio.Queue` per subscriber. Non-blocking publish drops events on full queues to prevent backpressure.

Event types: `CREATED`, `MODIFIED`, `DELETED` (emitted by HTTP API handlers), `RECONCILED` (emitted by controller).

Endpoints: `GET /api/v1/events` (optional `resource_type` filter) and `GET /api/v1/resources/{id}/events`.

## Development

### Codestyle

After every commit run `flake8` and `black .` to ensure codestyle compliance. Classes with parameter and return typehints are preferred.

### Running Tests

```bash
pytest tests/
```

### Documentation

When making changes ensure the documentation in the `docs/` folder and this architecture document are updated.
