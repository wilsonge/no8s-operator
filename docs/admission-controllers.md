# Admission Controllers

This guide covers building admission controllers for no8s, including integrating with Open Policy Agent (OPA) for policy-as-code enforcement. If you've worked with Kubernetes admission webhooks, the model is nearly identical — no8s calls your HTTP endpoint before persisting a resource, and your endpoint decides whether to allow, deny, or mutate the request.

## Concepts

| Kubernetes                          | no8s                                                              |
|-------------------------------------|-------------------------------------------------------------------|
| ValidatingWebhookConfiguration      | Admission webhook with `webhook_type: "validating"`               |
| MutatingWebhookConfiguration        | Admission webhook with `webhook_type: "mutating"`                 |
| AdmissionReview request/response    | JSON POST to `webhook_url`, JSON response with `allowed`/`patches`|
| `failurePolicy: Ignore`             | `failure_policy: "Ignore"`                                        |
| `failurePolicy: Fail`               | `failure_policy: "Fail"` (default)                                |
| `matchPolicy` / namespace selectors | `resource_type_name` + `resource_type_version` filters            |
| Webhook ordering (reinvocation)     | `ordering` field (lower values run first)                         |

## How Admission Works

Every resource mutation (CREATE, UPDATE, DELETE) passes through the admission chain **after** schema validation but **before** database persistence. If any webhook denies the request, the API returns HTTP 403 and the resource is not written.

```
Client request
     │
     ▼
Schema validation (OpenAPI v3)
     │
     ▼
Admission chain
  1. Mutating webhooks (in ordering order)
     – Each can patch the spec
     – Each can deny (stops the chain)
  2. Validating webhooks (in ordering order)
     – Each can deny (stops the chain)
     │
     ▼ (allowed)
Database persistence
     │
     ▼
201 Created / 200 OK

     ✕ (denied at any step)
403 Forbidden {"detail": "reason from webhook"}
```

## Registering a Webhook

```bash
curl -X POST http://localhost:8000/api/v1/admission-webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "opa-policy-check",
    "webhook_url": "http://opa-sidecar:8181/v1/data/no8s/admission",
    "webhook_type": "validating",
    "operations": ["CREATE", "UPDATE"],
    "resource_type_name": "DatabaseCluster",
    "resource_type_version": "v1",
    "timeout_seconds": 5,
    "failure_policy": "Fail",
    "ordering": 10
  }'
```

### Fields

| Field                   | Required | Default  | Description                                                                 |
|-------------------------|----------|----------|-----------------------------------------------------------------------------|
| `name`                  | yes      |          | Unique identifier for the webhook                                           |
| `webhook_url`           | yes      |          | HTTP endpoint that receives admission requests                              |
| `webhook_type`          | yes      |          | `"validating"` or `"mutating"`                                              |
| `operations`            | yes      |          | Array of operations to intercept: `["CREATE", "UPDATE", "DELETE"]`          |
| `resource_type_name`    | no       | `null`   | Target resource type. `null` means all types                                |
| `resource_type_version` | no       | `null`   | Target version. `null` means all versions                                   |
| `timeout_seconds`       | no       | `10`     | HTTP request timeout                                                        |
| `failure_policy`        | no       | `"Fail"` | `"Fail"` rejects on webhook errors; `"Ignore"` allows and logs a warning   |
| `ordering`              | no       | `0`      | Execution order within the same webhook type. Lower values run first        |

### Managing Webhooks

```bash
# List all webhooks (with optional filters)
curl http://localhost:8000/api/v1/admission-webhooks
curl http://localhost:8000/api/v1/admission-webhooks?webhook_type=validating

# Get a specific webhook
curl http://localhost:8000/api/v1/admission-webhooks/1

# Update
curl -X PUT http://localhost:8000/api/v1/admission-webhooks/1 \
  -H "Content-Type: application/json" \
  -d '{"timeout_seconds": 3, "failure_policy": "Ignore"}'

# Delete
curl -X DELETE http://localhost:8000/api/v1/admission-webhooks/1
```

## Writing a Webhook Server

A webhook server is any HTTP service that accepts a POST request and returns a JSON response. Here's the protocol.

### Request Format

no8s POSTs this JSON body to your `webhook_url`:

```json
{
  "operation": "CREATE",
  "resource": {
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
  },
  "old_resource": null
}
```

For UPDATE operations, `old_resource` contains the resource state before the update. For CREATE and DELETE, it is `null`.

### Response Format

Your webhook must return JSON:

```json
{
  "allowed": true,
  "message": "Approved by policy check",
  "patches": []
}
```

| Field     | Type    | Description                                                                  |
|-----------|---------|------------------------------------------------------------------------------|
| `allowed` | bool    | `true` to allow, `false` to deny                                            |
| `message` | string  | Reason, returned to the client on denial                                     |
| `patches` | array   | JSON Patch operations (only meaningful for mutating webhooks)                |

### Patch Operations

Mutating webhooks can return patches to modify the resource spec. Supported operations:

```json
{"op": "add", "path": "/spec/replicas", "value": 3}
{"op": "replace", "path": "/spec/instance_class", "value": "db.xlarge"}
{"op": "remove", "path": "/spec/debug"}
```

Paths can use `/spec/field` or just `/field` — the `/spec/` prefix is stripped automatically. Patches are applied sequentially, so later patches see the result of earlier ones.

### Minimal Example (Python + FastAPI)

```python
from fastapi import FastAPI

app = FastAPI()

@app.post("/validate")
async def validate(request: dict):
    resource = request["resource"]
    spec = resource["spec"]

    # Deny if storage exceeds 5TB
    if spec.get("storage_gb", 0) > 5000:
        return {
            "allowed": False,
            "message": "Storage cannot exceed 5000 GB"
        }

    return {"allowed": True, "message": "OK"}
```

Register it:

```bash
curl -X POST http://localhost:8000/api/v1/admission-webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "storage-limit",
    "webhook_url": "http://my-webhook:9000/validate",
    "webhook_type": "validating",
    "operations": ["CREATE", "UPDATE"],
    "failure_policy": "Fail"
  }'
```

## OPA Integration

[Open Policy Agent](https://www.openpolicyagent.org/) (OPA) is a general-purpose policy engine. Because no8s admission webhooks use a simple HTTP request/response protocol, OPA slots in naturally — you write policies in Rego, deploy OPA as an HTTP service, and register it as a no8s admission webhook.

### Architecture

```
Client ──▶ no8s API ──▶ Admission Chain ──▶ OPA (HTTP) ──▶ Rego policies
                              │                                   │
                              ◀───────────── allow / deny ◀───────┘
```

There are two deployment patterns:

1. **OPA as a sidecar or standalone service** — run OPA with `opa run --server`, point the webhook URL at it. Best for production.
2. **OPA wrapper service** — a thin HTTP server that translates between no8s admission format and OPA's native API, giving you full control over the request/response mapping. Best when you need custom logic around the policy decision.

### Approach 1: OPA Wrapper Service

The most flexible approach. A lightweight service translates the no8s admission request into OPA input and converts the OPA decision back to an admission response.

```python
"""OPA admission webhook bridge for no8s."""

import httpx
from fastapi import FastAPI

app = FastAPI()

OPA_URL = "http://localhost:8181/v1/data/no8s/admission"


@app.post("/admit")
async def admit(request: dict):
    """Forward admission request to OPA and translate the response."""
    opa_input = {
        "input": {
            "operation": request["operation"],
            "resource": request["resource"],
            "old_resource": request.get("old_resource"),
        }
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(OPA_URL, json=opa_input, timeout=5.0)
        result = resp.json().get("result", {})

    allowed = result.get("allowed", False)
    message = result.get("message", "Denied by policy")
    patches = result.get("patches", [])

    return {
        "allowed": allowed,
        "message": message,
        "patches": patches,
    }
```

Register it as a no8s webhook:

```bash
curl -X POST http://localhost:8000/api/v1/admission-webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "opa-admission",
    "webhook_url": "http://opa-bridge:9000/admit",
    "webhook_type": "validating",
    "operations": ["CREATE", "UPDATE"],
    "failure_policy": "Fail",
    "ordering": 100
  }'
```

### Approach 2: OPA Direct (No Wrapper)

If your Rego policy produces output that matches the no8s admission response format exactly, you can point the webhook URL directly at OPA's Data API. OPA returns `{"result": {...}}`, so your policy's output must land under the `result` key.

This works but is less flexible — you lose the ability to do pre-/post-processing, logging, or error handling outside of Rego. The wrapper approach above is usually worth the small overhead.

### Writing Rego Policies

#### Project Layout

```
policies/
├── no8s/
│   └── admission.rego       # Main admission policy
├── no8s_test/
│   └── admission_test.rego  # Tests
└── data.json                # External data (optional)
```

#### Basic Validating Policy

```rego
package no8s.admission

import rego.v1

default allowed := false
default message := "Denied by default"

# Allow if all rules pass
allowed if {
    not violation
}

message := "OK" if {
    allowed
}

# Collect violation reasons
message := concat("; ", reasons) if {
    violation
    reasons := {msg | some msg in violations}
}

violation if {
    count(violations) > 0
}

# --- Individual policy rules ---

violations contains msg if {
    input.resource.spec.storage_gb > 5000
    msg := "Storage cannot exceed 5000 GB"
}

violations contains msg if {
    input.resource.spec.high_availability == true
    input.resource.spec.replicas < 2
    msg := "High availability requires at least 2 replicas"
}

violations contains msg if {
    input.operation == "UPDATE"
    input.old_resource.spec.engine != input.resource.spec.engine
    msg := "Engine type cannot be changed after creation"
}
```

#### Mutating Policy

OPA can also drive mutating webhooks by returning patch operations. Register the webhook as `"webhook_type": "mutating"` and have your Rego policy produce a `patches` array.

```rego
package no8s.mutation

import rego.v1

default allowed := true
default message := "OK"

# Inject default labels or enforce minimum values
patches contains patch if {
    input.resource.spec.replicas < 1
    patch := {
        "op": "replace",
        "path": "/spec/replicas",
        "value": 1,
    }
}

patches contains patch if {
    not input.resource.spec.monitoring
    patch := {
        "op": "add",
        "path": "/spec/monitoring",
        "value": true,
    }
}
```

#### Testing Policies

Test Rego policies before deploying with `opa test`:

```rego
# policies/no8s_test/admission_test.rego
package no8s.admission_test

import rego.v1

import data.no8s.admission

test_allow_valid_resource if {
    admission.allowed with input as {
        "operation": "CREATE",
        "resource": {
            "spec": {
                "engine": "postgres",
                "storage_gb": 500,
                "replicas": 2,
                "high_availability": true,
            },
        },
    }
}

test_deny_oversized_storage if {
    not admission.allowed with input as {
        "operation": "CREATE",
        "resource": {
            "spec": {
                "engine": "postgres",
                "storage_gb": 10000,
                "replicas": 1,
                "high_availability": false,
            },
        },
    }
}

test_deny_engine_change if {
    not admission.allowed with input as {
        "operation": "UPDATE",
        "resource": {"spec": {"engine": "mysql", "storage_gb": 100, "replicas": 1, "high_availability": false}},
        "old_resource": {"spec": {"engine": "postgres", "storage_gb": 100, "replicas": 1, "high_availability": false}},
    }
}
```

Run tests:

```bash
opa test policies/ -v
```

### Deploying OPA

#### Docker Compose

Add OPA and the webhook bridge to your `docker-compose.yml`:

```yaml
services:
  opa:
    image: openpolicyagent/opa:latest
    command:
      - "run"
      - "--server"
      - "--addr=0.0.0.0:8181"
      - "/policies"
    volumes:
      - ./policies:/policies
    ports:
      - "8181:8181"

  opa-bridge:
    build: ./opa-bridge
    environment:
      - OPA_URL=http://opa:8181/v1/data/no8s/admission
    ports:
      - "9000:9000"
    depends_on:
      - opa
```

#### Loading and Reloading Policies

OPA watches the policy directory by default. To update policies at runtime without restarting:

```bash
# Push a policy bundle via the OPA API
curl -X PUT http://localhost:8181/v1/policies/admission \
  -H "Content-Type: text/plain" \
  --data-binary @policies/no8s/admission.rego
```

For production, use [OPA Bundles](https://www.openpolicyagent.org/docs/latest/management-bundles/) to serve policies from a central bundle server (S3, GCS, HTTP) with automatic polling and versioning.

### Resource-Type-Scoped Policies

You can register one OPA webhook per resource type, each pointing to a different Rego package:

```bash
# DatabaseCluster policy
curl -X POST http://localhost:8000/api/v1/admission-webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "opa-database-policy",
    "webhook_url": "http://opa-bridge:9000/admit?package=no8s.database_admission",
    "webhook_type": "validating",
    "operations": ["CREATE", "UPDATE"],
    "resource_type_name": "DatabaseCluster",
    "resource_type_version": "v1",
    "failure_policy": "Fail",
    "ordering": 100
  }'

# DnsRecord policy
curl -X POST http://localhost:8000/api/v1/admission-webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "opa-dns-policy",
    "webhook_url": "http://opa-bridge:9000/admit?package=no8s.dns_admission",
    "webhook_type": "validating",
    "operations": ["CREATE", "UPDATE", "DELETE"],
    "resource_type_name": "DnsRecord",
    "failure_policy": "Fail",
    "ordering": 100
  }'
```

Or register a single global webhook (omit `resource_type_name`) and route inside Rego:

```rego
package no8s.admission

import rego.v1

default allowed := true

allowed := result if {
    input.resource.resource_type_name == "DatabaseCluster"
    result := data.no8s.database_admission.allowed with input as input
}

allowed := result if {
    input.resource.resource_type_name == "DnsRecord"
    result := data.no8s.dns_admission.allowed with input as input
}
```

### Combining Mutating and Validating Webhooks

A common pattern is to use OPA in two phases — mutating webhooks first to set defaults and normalize specs, then validating webhooks to enforce constraints on the final result:

```bash
# Phase 1: Mutation (ordering 10, runs first)
curl -X POST http://localhost:8000/api/v1/admission-webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "opa-defaults",
    "webhook_url": "http://opa-bridge:9000/mutate",
    "webhook_type": "mutating",
    "operations": ["CREATE"],
    "failure_policy": "Ignore",
    "ordering": 10
  }'

# Phase 2: Validation (ordering 100, runs after all mutating)
curl -X POST http://localhost:8000/api/v1/admission-webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "name": "opa-validate",
    "webhook_url": "http://opa-bridge:9000/admit",
    "webhook_type": "validating",
    "operations": ["CREATE", "UPDATE"],
    "failure_policy": "Fail",
    "ordering": 100
  }'
```

This ensures validating policies always see the fully-defaulted spec, regardless of what the client submitted.

### External Data in OPA

OPA policies can reference external data (RBAC roles, cost budgets, environment metadata) loaded via:

- **Static data files** mounted into the OPA container (`data.json`)
- **Bundle API** for dynamic updates from a remote source
- **OPA's `http.send`** built-in for real-time lookups (use sparingly — adds latency)

Example — denying resources that exceed a team's budget:

```rego
package no8s.admission

import rego.v1

# data.json: {"team_budgets": {"platform": {"max_storage_gb": 2000}}}

violations contains msg if {
    team := input.resource.metadata.team
    limit := data.team_budgets[team].max_storage_gb
    input.resource.spec.storage_gb > limit
    msg := sprintf("Team %s storage limit is %d GB", [team, limit])
}
```

### Failure Policy Considerations

Choose `failure_policy` based on how critical the policy is:

| Policy                     | `failure_policy` | Rationale                                              |
|----------------------------|------------------|--------------------------------------------------------|
| Security constraints       | `Fail`           | Must not bypass — reject if OPA is unreachable         |
| Cost guardrails            | `Fail`           | Accidental over-provisioning is expensive to reverse   |
| Default injection          | `Ignore`         | Better to create without defaults than to block        |
| Audit/logging webhooks     | `Ignore`         | Logging failures should not block resource creation    |

### Debugging

Check what OPA decides for a given input:

```bash
# Query OPA directly
curl -X POST http://localhost:8181/v1/data/no8s/admission \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "operation": "CREATE",
      "resource": {
        "resource_type_name": "DatabaseCluster",
        "resource_type_version": "v1",
        "spec": {"engine": "postgres", "storage_gb": 10000}
      }
    }
  }'
```

If a resource creation is denied, the no8s API returns the webhook's message in the 403 response body:

```json
{"detail": "Storage cannot exceed 5000 GB"}
```

Enable verbose OPA logging for troubleshooting:

```bash
opa run --server --log-level debug /policies
```