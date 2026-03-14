# Reconciler Architecture

This page describes how reconciler plugins fit into the operator's overall architecture. If you want to build a reconciler, see [Writing a Reconciler](writing-a-reconciler.md).

## Two-Tier Model

The operator uses a two-tier reconciliation model:

**Tier 1 — Operator main loop:** Receives events from input plugins (HTTP API, queue listeners, polling), caches resource state in PostgreSQL, starts and stops reconciler loops, and tracks status and history.

**Tier 2 — Reconciler plugin loop (3rd party):** Runs its own loop per resource type — watches the cache, reconciles (directly or via an action plugin), and reports status back.

```
Operator Main Loop                 Reconciler Plugin Loop
──────────────────                 ──────────────────────
Receive event ──▶ Cache state      Watch cache ──▶ Reconcile ──▶ (Action Plugin)
                       │                  │                │
                       └──────────────────┘                │
Update status ◀── Record history ◀─────── Report ◀────────┘
```

The operator starts each reconciler's loop alongside its own main loop and stops it during shutdown by setting `ctx.shutdown_event`.

## Plugin Types

Three plugin types form the complete system:

- **Input plugins** (event sources) — HTTP API (implemented), HTTP polling (planned), queue listeners such as SQS and RabbitMQ (planned)
- **Reconciler plugins** (3rd party pip packages) — auto-discovered via the `no8s.reconcilers` Python entry point group; each declares which resource types it owns and runs its own continuous loop
- **Action plugins** (optional executors) — available to reconcilers for delegating execution to external systems; GitHub Actions (implemented), GitLab Pipelines (planned), HTTP API (planned)

### Key characteristics of reconciler plugins

- Each reconciler declares which resource type(s) it handles; the operator rejects API requests for resource types no reconciler claims
- A resource type can only be claimed by one reconciler
- Reconcilers run their own continuous loop, reading from the operator's resource cache
- Reconcilers may use action plugins or implement reconciliation logic directly
- The operator starts and stops reconciler loops alongside its own main loop

## Resource Lifecycle

```
pending  -->  reconciling  -->  ready
                             |
                             v
                           failed  -->  (exponential backoff)  -->  reconciling

Deletion:
ready/failed  -->  deleting  -->  (destroy + remove finalizers)  -->  hard delete
```

- **Pending** — created, awaiting first reconciliation
- **Reconciling** — reconciler executing
- **Ready** — matches desired state
- **Failed** — will retry with exponential backoff
- **Deleting** — awaiting destroy and finalizer removal

The reconciler is responsible for transitioning resources through `reconciling` → `ready`/`failed`. The operator handles `pending` (on creation), `deleting` (on `DELETE` API call), and the exponential backoff retry of `failed` resources.

## Finalizers

Kubernetes-style deletion protection. A resource cannot be hard-deleted until its `finalizers` array is empty.

**Lifecycle:**

1. On creation, the reconciler name is added as a finalizer (e.g. `["dns_record"]`)
2. External controllers can add their own finalizers via `PUT /api/v1/resources/{id}/finalizers`
3. On `DELETE`, the resource is soft-deleted (`deleted_at` set, `status='deleting'`)
4. The reconciler destroys external resources and removes its finalizer via `ctx.remove_finalizer()`
5. Once no finalizers remain the resource is hard-deleted; if external finalizers still exist it stays in `deleting`

## Event Streaming

The operator provides real-time event streaming via Server-Sent Events — the equivalent of `kubectl get --watch`.

`GET /api/v1/events` streams all resource events (accepts an optional `resource_type` query parameter to filter). `GET /api/v1/resources/{id}/events` streams events for a single resource.

Event types:

| Type         | Emitted by                                |
|--------------|-------------------------------------------|
| `CREATED`    | HTTP API on resource creation             |
| `MODIFIED`   | HTTP API on spec update                   |
| `DELETED`    | HTTP API on `DELETE`                      |
| `RECONCILED` | Controller after a reconciliation attempt |

Reconcilers do not consume the event stream directly — they poll the resource cache via `ctx.get_resources_needing_reconciliation()`. The event stream is primarily for external consumers watching for state changes.
