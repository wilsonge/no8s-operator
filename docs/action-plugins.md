# Action Plugins

Action plugins are optional executors that reconciler plugins can use to delegate infrastructure changes to external systems. Rather than implementing execution logic directly, a reconciler can hand off the actual work — triggering a pipeline, calling an API, running Terraform — to an action plugin.

## How they work

Action plugins follow a standard lifecycle:

1. **Prepare** — set up a workspace (resolve endpoints, parse spec fields)
2. **Plan** — determine what changes would be made, without making them
3. **Apply** — execute the changes and wait for completion
4. **Cleanup** — tear down any temporary state

For resource deletion, **Destroy** is called instead of Apply.

Reconcilers interact with action plugins via `ReconcilerContext`:

```python
async def reconcile(self, resource, ctx):
    plugin = await ctx.get_action_plugin("github_actions")

    action_ctx = ActionContext(
        resource_id=resource["id"],
        resource_name=resource["name"],
        generation=resource["generation"],
        spec=resource["spec"],
        spec_hash=resource["spec_hash"],
    )

    workspace = await plugin.prepare(action_ctx)
    plan = await plugin.plan(action_ctx, workspace)

    if plan.has_changes:
        result = await plugin.apply(action_ctx, workspace)

    await plugin.cleanup(workspace)
```

---

## GitHub Actions

**Status:** Implemented
**Plugin name:** `github_actions`

Triggers a GitHub Actions workflow dispatch and polls for completion. The reconciler defines which workflow to run and what inputs to pass; the plugin handles authentication, dispatch, polling, and output retrieval.

### Configuration

Set via environment variables:

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | — | GitHub API token (required) |
| `GITHUB_API_URL` | `https://api.github.com` | Base API URL (override for GHES) |
| `GITHUB_ACTIONS_TIMEOUT` | `3600` | Workflow timeout in seconds |
| `GITHUB_ACTIONS_POLL_INTERVAL` | `10` | Seconds between status checks |

### Resource spec fields

Resources using this plugin must include the following in their `spec`:

| Field | Required | Description |
|---|---|---|
| `owner` | Yes | GitHub repository owner (user or org) |
| `repo` | Yes | Repository name |
| `workflow` | Yes | Workflow filename (e.g. `deploy.yml`) or numeric workflow ID |
| `ref` | No | Git ref to run on (default: `main`) |
| `inputs` | No | Map of workflow input parameters |

Example:

```json
{
  "owner": "my-org",
  "repo": "infrastructure",
  "workflow": "provision.yml",
  "ref": "main",
  "inputs": {
    "environment": "production",
    "cluster_name": "prod-eu-west"
  }
}
```

### Behaviour

- **Plan** — verifies the workflow exists and is accessible via the GitHub API. Always reports `has_changes: true` (workflow dispatch is always triggered on apply).
- **Apply** — dispatches a `workflow_dispatch` event, then polls until the run completes or times out. Returns the run URL, job summaries, and artifact metadata as outputs.
- **Destroy** — cancels the active workflow run for the resource, if one exists.
- **Drift detection** — detects if `inputs` in the spec have changed since the last run.

### Outputs

After a successful apply, the following outputs are available:

```json
{
  "jobs": [
    {
      "name": "deploy",
      "status": "completed",
      "conclusion": "success",
      "started_at": "2024-01-01T12:00:00Z",
      "completed_at": "2024-01-01T12:05:00Z"
    }
  ],
  "artifacts": [
    {
      "name": "deployment-manifest",
      "size_in_bytes": 1024,
      "archive_download_url": "https://api.github.com/..."
    }
  ]
}
```

!!! note
    GitHub Actions does not expose workflow-level outputs via the API directly. The plugin returns job completion details and artifact metadata. To pass structured data back to the reconciler, use artifacts or a side-channel (e.g. update a resource via the operator API from within the workflow).

---

## GitLab Pipelines

**Status:** Planned
**Plugin name:** `gitlab_pipelines`

Will trigger GitLab CI/CD pipelines via the GitLab API and monitor them to completion. Functionally equivalent to the GitHub Actions plugin but for GitLab-hosted repositories and self-managed GitLab instances.

Planned spec fields: `project_id` or `project_path`, `ref`, `variables`.

---

## HTTP API

**Status:** Planned
**Plugin name:** `http_api`

A generic plugin for delegating reconciliation to any HTTP endpoint. Intended for:

- Custom internal automation services
- Webhooks into existing tooling (Rundeck, Jenkins, etc.)
- Bridging to systems without a native plugin

Planned behaviour: POST the resource spec to a configured endpoint, poll a status URL, and interpret the response as success or failure. Authentication, retry policy, and response mapping will be configurable per resource type.

---

## Writing your own action plugin

Action plugins implement the `ActionPlugin` abstract base class from `plugins.actions.base`. Install your plugin as a Python package and register it with the operator registry at startup.

See the [reconciler architecture](reconciler-architecture.md) documentation for how plugins are discovered and how reconcilers interact with them.