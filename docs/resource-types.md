# Resource Types

Resource types define the schema for resources managed by the operator. They are equivalent to Kubernetes CustomResourceDefinitions (CRDs) — before you can create a resource, the corresponding resource type must exist.

## Concepts

| Kubernetes                               | no8s                                           |
|------------------------------------------|------------------------------------------------|
| CustomResourceDefinition (CRD)           | Resource type                                  |
| `spec.versions[].schema.openAPIV3Schema` | `schema` (OpenAPI v3 JSON Schema)              |
| `spec.group/version/kind`                | `name` + `version` (e.g. `DatabaseCluster/v1`) |
| `kubectl apply -f crd.yaml`              | `POST /api/v1/resource-types`                  |
| `kubectl get crd`                        | `GET /api/v1/resource-types`                   |

Managing resource types requires admin access. Any authenticated user can read them.

## Authentication

All examples below assume you have a token in `$TOKEN`. Obtain one with:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "changeme123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

## Create a resource type

`POST /api/v1/resource-types` — requires admin.

The `schema` field must be a valid OpenAPI v3 JSON Schema object. The operator validates specs against it when resources are created or updated.

```bash
curl -X POST http://localhost:8000/api/v1/resource-types \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DatabaseCluster",
    "version": "v1",
    "description": "Managed PostgreSQL or MySQL cluster",
    "schema": {
      "type": "object",
      "required": ["engine", "instance_class", "storage_gb"],
      "properties": {
        "engine": {"type": "string", "enum": ["postgres", "mysql"]},
        "instance_class": {"type": "string"},
        "storage_gb": {"type": "integer", "minimum": 10, "maximum": 10000},
        "replicas": {"type": "integer", "minimum": 0, "default": 0},
        "high_availability": {"type": "boolean", "default": false}
      }
    }
  }'
```

Response (`201 Created`):

```json
{
  "id": 1,
  "name": "DatabaseCluster",
  "version": "v1",
  "description": "Managed PostgreSQL or MySQL cluster",
  "status": "active",
  "schema": { ... },
  "metadata": {},
  "created_at": "2024-06-01T12:00:00+00:00",
  "updated_at": "2024-06-01T12:00:00+00:00"
}
```

Creating a duplicate `name`/`version` pair returns `409 Conflict`.

## Versioning

Each resource type is identified by its `name` and `version` together. The same logical type can have multiple versions with different schemas:

```bash
# v1 schema
curl -X POST http://localhost:8000/api/v1/resource-types \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "DatabaseCluster", "version": "v1", "schema": { ... }}'

# v2 schema with additional fields
curl -X POST http://localhost:8000/api/v1/resource-types \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "DatabaseCluster", "version": "v2", "schema": { ... }}'
```

Resources created against `v1` continue to use the `v1` schema. Versions are independent — there is no automatic migration between them.

## List resource types

`GET /api/v1/resource-types` — any authenticated user.

```bash
# All resource types
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/resource-types

# Filter by name (returns all versions of that type)
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/resource-types?name=DatabaseCluster"

# Filter by status
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/resource-types?status=deprecated"

# Pagination
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/resource-types?limit=20"
```

## Get a resource type

By numeric ID:

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/resource-types/1
```

By name and version (more stable across environments than IDs):

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/resource-types/DatabaseCluster/v1
```

Both return `404` if not found.

## Update a resource type

`PUT /api/v1/resource-types/{id}` — requires admin. All fields are optional; only supplied fields are updated.

```bash
# Update the schema
curl -X PUT http://localhost:8000/api/v1/resource-types/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "schema": {
      "type": "object",
      "required": ["engine", "instance_class", "storage_gb"],
      "properties": {
        "engine": {"type": "string", "enum": ["postgres", "mysql", "mariadb"]},
        "instance_class": {"type": "string"},
        "storage_gb": {"type": "integer", "minimum": 10}
      }
    }
  }'

# Mark as deprecated
curl -X PUT http://localhost:8000/api/v1/resource-types/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": "deprecated", "description": "Use DatabaseCluster/v2 instead"}'
```

Updating the schema does not revalidate existing resources — only new creates and updates are validated against the current schema.

## Delete a resource type

`DELETE /api/v1/resource-types/{id}` — requires admin.

```bash
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/resource-types/1
```

Returns `204 No Content` on success. Returns `409 Conflict` if any resources still reference this type — delete or migrate those resources first.