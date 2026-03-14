# Operator Controller

A Kubernetes-style controller for managing infrastructure. The operator receives resource events via input plugins (HTTP API, queue listeners, polling), caches resource state, and delegates reconciliation to **3rd party reconciler plugins** — each responsible for one or more resource types.

Reconciler plugins are installed as separate pip packages and auto-discovered via Python entry points. Each reconciler owns its own reconciliation loop and may optionally use the operator's action plugin system (GitHub Actions, GitLab Pipelines, etc.) to execute changes.

## Plugin Architecture

Three plugin types: **input plugins** (event sources), **reconciler plugins** (reconciliation per resource type), and **action plugins** (optional executors).

### Input Plugins

- **HTTP API** (implemented) - REST API for creating/updating resources. It needs a review of which resources belong in the plugin which can be disabled and which should be constantly enabled (similar to the cluster_status.py file)
- **HTTP Polling** (planned) - Poll external APIs for state changes
- **Queue Listeners** (planned) - SQS, RabbitMQ, etc.

## Advanced Features

### Finalizers

Kubernetes-style deletion protection. A resource cannot be hard-deleted until its `finalizers` JSONB array is empty.

**Lifecycle:**
1. On creation, the reconciler name is added as a finalizer (e.g. `["database_cluster"]`)
2. External controllers can add finalizers via `PUT /api/v1/resources/{id}/finalizers`
3. On `DELETE`, the resource is soft-deleted (`deleted_at` set, `status='deleting'`)
4. Reconciler destroys external resources, then removes its finalizer
5. Hard-deleted when no finalizers remain; stays in `deleting` if external finalizers exist


## Development

### Codestyle

After every commit run `flake8` and `black .` to ensure codestyle compliance. Classes with parameter and return typehints are preferred.

### Running Tests

```bash
pytest tests/
```

### Documentation

When making changes ensure the documentation in the `docs/` folder and this architecture document are updated.
