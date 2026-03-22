# Operator Controller

A Kubernetes-style controller for managing infrastructure. The operator receives resource events via input plugins (HTTP API, queue listeners, polling), caches resource state, and delegates reconciliation to **3rd party reconciler plugins** — each responsible for one or more resource types.

Reconciler plugins are installed as separate pip packages and auto-discovered via Python entry points. Each reconciler owns its own reconciliation loop and may optionally use the operator's action plugin system (GitHub Actions, GitLab Pipelines, etc.) to execute changes.

Three plugin types: **input plugins** (event sources), **reconciler plugins** (reconciliation per resource type), and **action plugins** (optional executors).

## Development

### Codestyle

After every commit run `flake8` and `black .` to ensure codestyle compliance. Classes with parameter and return typehints are preferred.

### Running Tests

```bash
pytest tests/
```

### Documentation

When making changes ensure the documentation in the `docs/` folder and this architecture document are updated.
