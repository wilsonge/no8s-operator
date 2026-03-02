# Writing a Reconciler Plugin

This guide walks through building a no8s reconciler plugin. If you've written a Kubernetes controller or operator, the concepts map directly — reconciler plugins are the equivalent of a controller's `Reconcile()` function, packaged as a standalone pip-installable module.

## Concepts

| Kubernetes                       | no8s                                                        |
|----------------------------------|-------------------------------------------------------------|
| Controller with `Reconcile()`    | `ReconcilerPlugin` subclass                                 |
| Informer / watch cache           | `ReconcilerContext.get_resources_needing_reconciliation()`  |
| `client-go` status update        | `ReconcilerContext.update_status()`                         |
| Status conditions                | `ReconcilerContext.set_condition()`                         |
| Finalizers on the object         | `ReconcilerContext.get_finalizers()` / `remove_finalizer()` |
| `ctrl.Result{RequeueAfter: ...}` | `ReconcileResult(requeue_after=seconds)`                    |
| Entry in `manager.Register()`    | Python entry point in `no8s.reconcilers` group              |

The operator discovers your plugin at startup via Python entry points (like a Kubernetes controller-manager discovering controllers), creates a `ReconcilerContext` (your handle to the operator's internals), and calls your `start()` method in its own asyncio task.

## Project Structure

A minimal reconciler plugin looks like this:

```
no8s-dns-reconciler/
├── pyproject.toml
└── src/
    └── no8s_dns/
        ├── __init__.py
        └── reconciler.py
```

## Step 1: Define the Reconciler

Subclass `ReconcilerPlugin` and implement four methods: `name`, `resource_types`, `start`, `reconcile`, and `stop`.

```python
# src/no8s_dns/reconciler.py
import asyncio
import logging
import time
from typing import Any, Dict, List

from no8s_operator.plugins.reconcilers.base import (
    ReconcilerPlugin,
    ReconcilerContext,
    ReconcileResult,
)

logger = logging.getLogger(__name__)


class DnsRecordReconciler(ReconcilerPlugin):
    """Reconciles DnsRecord resources against a DNS provider API."""

    def __init__(self):
        self._running = False
        self.reconcile_interval = 30  # seconds between loop iterations

    @property
    def name(self) -> str:
        return "dns_record"

    @property
    def resource_types(self) -> List[str]:
        """Resource type names this reconciler handles.

        Similar to setting up a Watch on specific GVKs in a Kubernetes controller.
        The operator rejects API requests for resource types that no reconciler claims.
        """
        return ["DnsRecord"]

    async def start(self, ctx: ReconcilerContext) -> None:
        """Main reconciliation loop — equivalent to controller-runtime's Start().

        The operator calls this in a dedicated asyncio task. Run until
        ctx.shutdown_event is set (analogous to the context cancellation
        in a Kubernetes controller).
        """
        self._running = True
        logger.info("DnsRecord reconciler started")

        while not ctx.shutdown_event.is_set():
            try:
                resources = await ctx.get_resources_needing_reconciliation(
                    resource_type_names=self.resource_types,
                    limit=10,
                )

                for resource in resources:
                    result = await self.reconcile(resource, ctx)
                    # Record the attempt in reconciliation history
                    await ctx.record_reconciliation(
                        resource_id=resource["id"],
                        result=result,
                        trigger_reason="spec_change",
                    )

            except Exception:
                logger.exception("Error in DnsRecord reconcile loop")

            # Wait before next iteration (like the resync period in an informer)
            try:
                await asyncio.wait_for(
                    ctx.shutdown_event.wait(),
                    timeout=self.reconcile_interval,
                )
                break  # shutdown_event was set
            except asyncio.TimeoutError:
                continue  # timeout expired, loop again

    async def reconcile(
        self, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> ReconcileResult:
        """Reconcile a single DnsRecord — equivalent to Reconcile() in controller-runtime."""
        resource_id = resource["id"]
        spec = resource.get("spec", {})
        start_time = time.monotonic()

        # Handle deletion (like checking DeletionTimestamp != nil)
        if resource.get("status") == "deleting":
            return await self._handle_delete(resource, ctx)

        # Mark as reconciling
        await ctx.update_status(resource_id, "reconciling", message="Syncing DNS record")

        try:
            # --- Your reconciliation logic here ---
            # Compare desired state (spec) with actual state (external API)
            domain = spec["domain"]
            record_type = spec["type"]
            value = spec["value"]

            # Example: call your DNS provider
            # await self.dns_client.upsert_record(domain, record_type, value)

            # Set a domain-specific condition for fine-grained observability
            await ctx.set_condition(
                resource_id,
                condition_type="DnsRecordSynced",
                status="True",
                reason="RecordUpserted",
                message=f"{record_type} record for {domain} synced",
                observed_generation=resource["generation"],
            )

            # Mark as ready (the operator also sets Ready=True automatically)
            await ctx.update_status(
                resource_id,
                "ready",
                message=f"DNS record {domain} synced",
                observed_generation=resource["generation"],
            )

            return ReconcileResult(success=True, message="Synced")

        except Exception as e:
            # Set a domain-specific condition to capture the reason for failure
            await ctx.set_condition(
                resource_id,
                condition_type="DnsRecordSynced",
                status="False",
                reason="ProviderError",
                message=str(e),
            )
            # Mark as failed — the operator's requeue loop will retry with backoff
            # (like returning ctrl.Result{}, err in Kubernetes)
            await ctx.update_status(
                resource_id,
                "failed",
                message=str(e),
            )
            return ReconcileResult(success=False, message=str(e))

    async def _handle_delete(
        self, resource: Dict[str, Any], ctx: ReconcilerContext
    ) -> ReconcileResult:
        """Handle resource deletion — equivalent to the finalizer removal pattern in Kubernetes."""
        resource_id = resource["id"]

        try:
            # Clean up external resources
            # await self.dns_client.delete_record(resource["spec"]["domain"])

            # Remove our finalizer (like removing the finalizer string from metadata.finalizers)
            await ctx.remove_finalizer(resource_id, self.name)

            # If no finalizers remain, hard-delete the resource
            # (like the API server garbage-collecting the object after all finalizers clear)
            remaining = await ctx.get_finalizers(resource_id)
            if not remaining:
                await ctx.hard_delete_resource(resource_id)

            return ReconcileResult(success=True, message="Deleted")

        except Exception as e:
            return ReconcileResult(success=False, message=f"Delete failed: {e}")

    async def stop(self) -> None:
        """Graceful shutdown — clean up connections, flush buffers, etc."""
        self._running = False
        logger.info("DnsRecord reconciler stopped")
```

## Step 2: Register via Entry Point

In your `pyproject.toml`, declare the entry point so the operator discovers your plugin at startup. This is the equivalent of registering a controller with the controller-manager.

```toml
[project]
name = "no8s-dns-reconciler"
version = "0.1.0"
dependencies = [
    "no8s-operator",
]

[project.entry-points.'no8s.reconcilers']
dns_record = "no8s_dns.reconciler:DnsRecordReconciler"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
```

The entry point key (`dns_record`) should match your reconciler's `name` property. The value is a standard Python dotted path to the class.

## Step 3: Install and Run

```bash
# Install the operator (if not already)
pip install no8s-operator

# Install your reconciler plugin
pip install .
# or for development:
pip install -e .

# The operator auto-discovers your reconciler on startup
python src/main.py
```

At startup, the operator logs:

```
Registered reconciler plugin: dns_record (resource types: DnsRecord)
Started reconciler plugin: dns_record
```

## Step 4: Create the Resource Type and Resources

Before creating resources, register the resource type (equivalent to applying a CRD):

```bash
curl -X POST http://localhost:8000/api/v1/resource-types \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DnsRecord",
    "version": "v1",
    "schema": {
      "type": "object",
      "required": ["domain", "type", "value"],
      "properties": {
        "domain": {"type": "string"},
        "type": {"type": "string", "enum": ["A", "AAAA", "CNAME", "TXT"]},
        "value": {"type": "string"},
        "ttl": {"type": "integer", "minimum": 60, "default": 300}
      }
    }
  }'
```

Then create a resource (equivalent to `kubectl apply`):

```bash
curl -X POST http://localhost:8000/api/v1/resources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "api-record",
    "resource_type_name": "DnsRecord",
    "resource_type_version": "v1",
    "spec": {
      "domain": "api.example.com",
      "type": "A",
      "value": "203.0.113.50",
      "ttl": 300
    }
  }'
```

## ReconcilerContext API

The `ReconcilerContext` is your interface back into the operator — similar to the client and recorder injected into a Kubernetes controller.

| Method | Kubernetes Equivalent | Description |
|---|---|---|
| `get_resources_needing_reconciliation(resource_type_names, limit)` | Informer work queue | Fetch resources where `generation > observed_generation`, status is `pending`/`failed`/`deleting`, or the drift detection window has elapsed |
| `update_status(resource_id, status, message, observed_generation)` | `Status().Update()` | Set `status` to `pending`, `reconciling`, `ready`, `failed`, or `deleting`. Set `observed_generation` on success to acknowledge the spec |
| `set_condition(resource_id, condition_type, status, reason, message, observed_generation)` | `meta.SetStatusCondition()` | Set a named condition on the resource. `status` is `"True"`, `"False"`, or `"Unknown"`. `lastTransitionTime` is only updated when `status` changes |
| `record_reconciliation(resource_id, result, ...)` | Event recorder | Write an entry to the reconciliation history audit log |
| `get_action_plugin(name)` | N/A (no8s-specific) | Get an action plugin instance to delegate execution (e.g. trigger a GitHub Actions workflow) |
| `remove_finalizer(resource_id, finalizer)` | Patch to remove finalizer string | Remove a finalizer from the resource's JSONB array |
| `get_finalizers(resource_id)` | Read `metadata.finalizers` | List current finalizers |
| `hard_delete_resource(resource_id)` | N/A (API server does this) | Permanently delete a resource. Only succeeds if the resource is soft-deleted and has zero finalizers |

## ReconcileResult

Return a `ReconcileResult` from your `reconcile()` method to report the outcome:

```python
@dataclass
class ReconcileResult:
    success: bool = False           # Did reconciliation succeed?
    message: str = ""               # Human-readable status message
    requeue_after: Optional[int] = None  # Re-reconcile after N seconds (like ctrl.Result{RequeueAfter})
```

## Resource Lifecycle

Resources move through a phase field (`status`) plus a set of named conditions.

### Phase

```
pending  -->  reconciling  -->  ready
                             |
                             v
                           failed  -->  (exponential backoff)  -->  reconciling

Deletion:
ready/failed  -->  deleting  -->  (destroy + remove finalizers)  -->  hard delete
```

Your reconciler is responsible for transitioning resources through `reconciling` -> `ready`/`failed`. The operator handles `pending` (on creation) and `deleting` (on `DELETE` API call). The operator's requeue loop retries `failed` resources with exponential backoff automatically.

### Status Conditions

Alongside the phase, resources carry an array of named conditions — each with `type`, `status` (`"True"`/`"False"`/`"Unknown"`), `reason` (CamelCase), `message`, `lastTransitionTime`, and `observedGeneration`. This is equivalent to `metav1.Condition` in Kubernetes.

The operator sets three standard conditions automatically at each lifecycle transition:

| Condition     | Reconciling start   | Success | Failure | Deleting    |
|---------------|---------------------|---------|---------|-------------|
| `Ready`       | `Unknown`           | `True`  | `False` | `Unknown`   |
| `Reconciling` | `True`              | `False` | `False` | `False`     |
| `Degraded`    | (unchanged)         | `False` | `True`  | (unchanged) |

Your reconciler can add domain-specific conditions on top of these using `ctx.set_condition()`. A DNS reconciler might add `DnsRecordSynced`; a database reconciler might add `ReplicationHealthy` or `SchemaApplied`.

`lastTransitionTime` is only updated when the condition's `status` value changes — not on every reconciliation. This matches Kubernetes semantics and lets consumers detect actual state changes.

Conditions appear in every resource GET response:

```json
{
  "id": 1,
  "name": "api-record",
  "status": "ready",
  "conditions": [
    {
      "type": "Ready",
      "status": "True",
      "reason": "ReconcileSuccess",
      "message": "Resource reconciled successfully",
      "lastTransitionTime": "2024-06-01T12:00:00+00:00",
      "observedGeneration": 3
    },
    {
      "type": "Reconciling",
      "status": "False",
      "reason": "ReconcileComplete",
      "message": "Reconciliation completed",
      "lastTransitionTime": "2024-06-01T12:00:00+00:00",
      "observedGeneration": 3
    },
    {
      "type": "Degraded",
      "status": "False",
      "reason": "NoErrors",
      "message": "",
      "lastTransitionTime": "2024-06-01T12:00:00+00:00",
      "observedGeneration": 3
    },
    {
      "type": "DnsRecordSynced",
      "status": "True",
      "reason": "RecordUpserted",
      "message": "A record for api.example.com synced",
      "lastTransitionTime": "2024-06-01T12:00:00+00:00",
      "observedGeneration": 3
    }
  ]
}
```

## Using Action Plugins

Reconcilers can optionally delegate execution to action plugins. This is useful when reconciliation involves triggering an external CI/CD system rather than calling APIs directly.

```python
async def reconcile(self, resource, ctx):
    # Get the GitHub Actions action plugin
    github = await ctx.get_action_plugin("github_actions")

    # Create an ActionContext for the plugin
    from no8s_operator.plugins.base import ActionContext
    action_ctx = ActionContext(
        resource_id=resource["id"],
        resource_name=resource["name"],
        generation=resource["generation"],
        spec=resource["spec"],
        spec_hash=resource["spec_hash"],
    )

    # Delegate to the action plugin's prepare/plan/apply lifecycle
    workspace = await github.prepare(action_ctx)
    plan = await github.plan(action_ctx, workspace)

    if plan.has_changes:
        result = await github.apply(action_ctx, workspace)

    await github.cleanup(workspace)
```

## Resource Type Ownership

Each resource type can only be claimed by one reconciler. If two reconcilers declare the same resource type, the second one fails to register:

```
ValueError: Resource type 'DnsRecord' is already claimed by reconciler 'dns_record'. Cannot register 'other_dns'.
```

This is analogous to Kubernetes preventing two controllers from watching the same GVK with conflicting logic — except no8s enforces it at registration time.

## Tips

- **Keep `reconcile()` idempotent.** Just like Kubernetes controllers, your reconcile function may be called multiple times for the same generation. Always compare desired vs actual state rather than assuming the previous call failed.

- **Use `observed_generation` to avoid redundant work.** Set it on successful reconciliation. The operator only re-queues resources where `generation > observed_generation` (plus drift detection on a timer).

- **Handle partial failures.** If your reconciliation involves multiple steps, consider what happens if it fails midway. The function will be retried — make sure earlier steps are safe to re-run.

- **Respect the shutdown event.** Check `ctx.shutdown_event.is_set()` in your loop and exit cleanly. The operator cancels your task after calling `stop()`, but graceful shutdown is preferred.

- **Use `asyncio.wait_for` for interruptible sleeps.** Instead of a bare `asyncio.sleep()`, wait on the shutdown event with a timeout. This lets your reconciler exit promptly during operator shutdown instead of blocking until the sleep completes.

- **Log with context.** Include the resource name/ID in log messages for debugging, the same way Kubernetes controllers log with `klog.WithValues("name", req.Name)`.
