# Resources

Resources are instances of a resource type — they represent a piece of infrastructure in its desired state. The operator stores the spec, tracks status, and dispatches to the appropriate reconciler plugin to make reality match the desired state.

## Concepts

| Kubernetes | no8s |
|---|---|
| Custom resource (CR) | Resource |
| `metadata.name` | `name` |
| `spec` | `spec` |
| `status.phase` | `status` |
| `status.conditions` | `conditions` |
| `metadata.generation` | `generation` |
| `status.observedGeneration` | `observed_generation` |
| `metadata.finalizers` | `finalizers` |
| `kubectl apply` | `POST /api/v1/resources` |
| `kubectl get` | `GET /api/v1/resources` |
| `kubectl get --watch` | `GET /api/v1/events` (SSE) |
| `kubectl delete` | `DELETE /api/v1/resources/{id}` |

## Authentication

All resource endpoints require a JWT token. Obtain one:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "changeme123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

Non-admin users need a custom role with matching permissions (`CREATE`, `READ`, `UPDATE`, `DELETE`) scoped to the relevant resource type. See [`docs/users.md`](users.md).

## Lifecycle

Resources move through a `status` field as the reconciler works:

```
pending  →  reconciling  →  ready
                          ↓
                        failed  →  (exponential backoff)  →  reconciling

Deletion:
ready / failed  →  deleting  →  (destroy + finalizers cleared)  →  hard deleted
```

| Status | Meaning |
|---|---|
| `pending` | Created, waiting for first reconciliation |
| `reconciling` | Reconciler is actively working |
| `ready` | Matches desired state |
| `failed` | Last reconciliation failed; will retry with backoff |
| `deleting` | Soft-deleted; reconciler is destroying external resources |

## Create a resource

`POST /api/v1/resources` — requires `CREATE` permission on the resource type (admins exempt).

Prerequisites:
- The resource type must exist (see [`docs/resource-types.md`](resource-types.md))
- A reconciler plugin must be installed that handles the resource type

```bash
curl -X POST http://localhost:8000/api/v1/resources \
  -H "Authorization: Bearer $TOKEN" \
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

The spec is validated against the resource type's schema. A `400` is returned on validation failure. If no reconciler is registered for the resource type, the request is also rejected with `400`.

Response (`201 Created`):

```json
{
  "id": 1,
  "name": "production-pg",
  "resource_type_name": "DatabaseCluster",
  "resource_type_version": "v1",
  "status": "pending",
  "status_message": null,
  "generation": 1,
  "observed_generation": 0,
  "finalizers": ["database_cluster"],
  "conditions": [],
  "created_at": "2024-06-01T12:00:00+00:00",
  "updated_at": "2024-06-01T12:00:00+00:00",
  "last_reconcile_time": null
}
```

Resource names follow Kubernetes naming conventions: lowercase alphanumeric and hyphens, maximum 63 characters.

### Optional fields

| Field | Description |
|---|---|
| `metadata` | Arbitrary key-value pairs stored alongside the resource |
| `plugin_config` | Plugin-specific configuration passed to the reconciler |

## List resources

`GET /api/v1/resources` — any authenticated user. Non-admins see only resource types their custom role grants `READ` on.

```bash
# All resources
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/resources

# Filter by status
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/resources?status=failed"

# Pagination
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/resources?limit=50"
```

## Get a resource

By numeric ID:

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/resources/1
```

By resource type name, version, and resource name (more portable across environments):

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/resources/by-name/DatabaseCluster/v1/production-pg
```

Both return `404` if not found and `403` if the caller lacks `READ` permission.

## Update a resource

`PUT /api/v1/resources/{id}` — requires `UPDATE` permission.

Updating `spec` increments `generation`. The reconciler picks up the change on its next loop iteration and drives the resource toward the new desired state.

```bash
curl -X PUT http://localhost:8000/api/v1/resources/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "spec": {
      "engine": "postgres",
      "instance_class": "db.xlarge",
      "storage_gb": 1000,
      "replicas": 3,
      "high_availability": true
    }
  }'
```

The updated spec is validated against the resource type schema before being stored. Admission webhooks (if configured) also run before persistence.

`plugin_config` can also be updated independently of `spec`.

## Delete a resource

`DELETE /api/v1/resources/{id}` — requires `DELETE` permission.

Deletion is a two-phase process, equivalent to Kubernetes finalizer-based deletion:

1. The API soft-deletes the resource: sets `status` to `deleting` and records `deleted_at`.
2. The reconciler destroys the external infrastructure and removes its finalizer.
3. Once all finalizers are cleared, the resource is hard-deleted from the database.

```bash
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/resources/1
```

Returns `202 Accepted`. The resource is not immediately gone — poll `GET /api/v1/resources/1` until you receive `404`.

If external controllers have added their own finalizers, the resource remains in `deleting` state until those finalizers are also removed.

## Finalizers

Finalizers are strings stored on a resource that block hard-deletion until cleared. The reconciler plugin adds its own name as a finalizer on resource creation (e.g. `"database_cluster"`).

External controllers or automation can add finalizers to express their own ownership:

```bash
# Add a finalizer
curl -X PUT http://localhost:8000/api/v1/resources/1/finalizers \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"add": ["external-controller"], "remove": []}'

# Remove a finalizer
curl -X PUT http://localhost:8000/api/v1/resources/1/finalizers \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"add": [], "remove": ["external-controller"]}'
```

If a resource is in `deleting` state and removing the last finalizer empties the list, the resource is immediately hard-deleted.

Requires `UPDATE` permission on the resource.

## Trigger reconciliation

Force an immediate reconciliation attempt, bypassing the normal polling interval:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/resources/1/reconcile
```

Returns `202 Accepted`. Useful for debugging or after making external changes you want the reconciler to pick up immediately. Requires `UPDATE` permission.

## Reconciliation history

The operator keeps an audit log of every reconciliation attempt:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/resources/1/history?limit=20"
```

Response:

```json
[
  {
    "id": 42,
    "resource_id": 1,
    "generation": 2,
    "success": true,
    "phase": "ready",
    "error_message": null,
    "resources_created": 0,
    "resources_updated": 1,
    "resources_deleted": 0,
    "reconcile_time": "2024-06-01T12:05:00+00:00"
  }
]
```

`limit` defaults to 10. Requires `READ` permission.

## Action plugin outputs

Reconcilers that delegate to action plugins (e.g. GitHub Actions) can store structured output on the resource — job IDs, artifact URLs, apply summaries, etc.

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/resources/1/outputs
```

Response:

```json
{
  "outputs": {
    "workflow_run_id": 12345678,
    "workflow_url": "https://github.com/org/repo/actions/runs/12345678"
  }
}
```

The shape of `outputs` is determined by the reconciler plugin. Requires `READ` permission.

## Status conditions

Alongside the `status` field, resources carry an array of named conditions that give fine-grained observability into what the reconciler is doing. This is equivalent to `metav1.Condition` in Kubernetes.

Each condition has:

| Field | Description |
|---|---|
| `type` | Unique name, e.g. `Ready`, `Reconciling`, `Degraded`, or a domain-specific name set by the reconciler |
| `status` | `"True"`, `"False"`, or `"Unknown"` |
| `reason` | Short CamelCase identifier, e.g. `ReconcileSuccess` |
| `message` | Human-readable detail |
| `lastTransitionTime` | Only updated when `status` changes (not on every reconcile) |
| `observedGeneration` | The resource generation when the condition was last set |

The operator sets three standard conditions automatically:

| Condition | Reconcile start | Success | Failure | Deleting |
|---|---|---|---|---|
| `Ready` | `Unknown` | `True` | `False` | `Unknown` |
| `Reconciling` | `True` | `False` | `False` | `False` |
| `Degraded` | (unchanged) | `False` | `True` | (unchanged) |

Reconciler plugins can add domain-specific conditions on top of these — for example a database reconciler might set `ReplicationHealthy` or `SchemaApplied`. Conditions appear in every resource GET response.

## Event streaming

Watch for real-time changes using Server-Sent Events (SSE), equivalent to `kubectl get --watch`.

### All events

```bash
curl -H "Authorization: Bearer $TOKEN" \
  -H "Accept: text/event-stream" \
  http://localhost:8000/api/v1/events
```

Filter to a specific resource type:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  -H "Accept: text/event-stream" \
  "http://localhost:8000/api/v1/events?resource_type=DatabaseCluster"
```

Non-admin users receive only events for resource types their custom role grants `READ` on.

### Single resource

```bash
curl -H "Authorization: Bearer $TOKEN" \
  -H "Accept: text/event-stream" \
  http://localhost:8000/api/v1/resources/1/events
```

### Event format

Each SSE message contains a JSON payload:

```
event: CREATED
data: {"event_type": "CREATED", "resource_id": 1, "resource_name": "production-pg", "resource_type_name": "DatabaseCluster", "resource_type_version": "v1", "resource_data": { ... }, "timestamp": "2024-06-01T12:00:00Z"}
```

| Event type | Emitted when |
|---|---|
| `CREATED` | Resource is created via the API |
| `MODIFIED` | Resource spec or status is updated |
| `DELETED` | Resource is soft-deleted |
| `RECONCILED` | Reconciler completes a reconciliation attempt |

## Troubleshooting

**Resource stuck in `reconciling`** — check the reconciliation history for errors:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/resources/1/history
```

**Resource stuck in `deleting`** — a finalizer is blocking hard-deletion. Check which finalizers remain:

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/resources/1 \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['finalizers'])"
```

Remove any stale finalizers once the external work is confirmed done.

**Force re-reconciliation** after external changes:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/resources/1/reconcile
```