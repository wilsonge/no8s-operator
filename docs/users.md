# User Management

This guide covers authentication, role-based access control, manual user creation, and LDAP directory integration.

## Concepts

All API endpoints (except `POST /api/v1/auth/login`) require a valid JWT bearer token. Access is controlled by two complementary mechanisms:

### Admin flag

Each user has an `is_admin` boolean. Admins bypass all permission checks and have full access to every endpoint. All other users must have a custom role to do anything meaningful.

### Custom roles

Custom roles are the sole mechanism for granting access to non-admin users. A role has two independent parts:

**Resource permissions** — controls which CRUD operations a user may perform on specific resource types:

| Field | Description |
|---|---|
| `resource_type_name` | Resource type name, or `*` for all types |
| `resource_type_version` | Resource type version, or `*` for all versions |
| `operations` | List of `CREATE`, `READ`, `UPDATE`, `DELETE` |

**System permissions** — controls access to system-level read endpoints:

| Value | Grants access to |
|---|---|
| `"view_webhooks"` | `GET /api/v1/admission-webhooks` and `GET /api/v1/admission-webhooks/{id}` |
| `"view_plugins"` | `GET /api/v1/plugins/actions` and `GET /api/v1/plugins/inputs` |

A user with no custom role can only read resource types and their own identity (`/api/v1/auth/me`). Assigning a custom role grants exactly the operations that role permits.

Users come from two sources:

| Source | How created | How authenticated |
|---|---|---|
| `manual` | Created via the API with a username and password | bcrypt password check |
| `ldap` | Synced from an LDAP directory | Bind to the LDAP server at login time |

## Configuration

### Required

| Variable | Description |
|---|---|
| `JWT_SECRET_KEY` | Signing key for JWTs. Use a long random string (32+ chars). |

### Optional

| Variable | Default | Description |
|---|---|---|
| `JWT_EXPIRY_HOURS` | `24` | Token lifetime in hours |
| `INITIAL_ADMIN_USERNAME` | — | Username for the bootstrap admin (see below) |
| `INITIAL_ADMIN_PASSWORD` | — | Password for the bootstrap admin |

### Bootstrap admin

On startup, if both `INITIAL_ADMIN_USERNAME` and `INITIAL_ADMIN_PASSWORD` are set **and** the users table is empty, the operator creates an admin user automatically. This is a one-time operation — once any user exists the bootstrap is skipped.

```bash
JWT_SECRET_KEY=your-long-random-secret \
INITIAL_ADMIN_USERNAME=admin \
INITIAL_ADMIN_PASSWORD=changeme123 \
python src/main.py
```

## Logging in

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "changeme123"}'
```

Response:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "username": "admin",
  "is_admin": true
}
```

Pass the token in subsequent requests:

```bash
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/resources
```

### Who am I?

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/auth/me
```

---

## Manual users

All user management endpoints require admin access.

### Create a user

```bash
curl -X POST http://localhost:8000/api/v1/users \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "username": "alice",
    "password": "securepass1",
    "email": "alice@example.com",
    "display_name": "Alice Smith",
    "is_admin": false,
    "custom_role_id": 3
  }'
```

Fields:

| Field | Required | Description |
|---|---|---|
| `username` | Yes | Lowercase alphanumeric + hyphens, max 63 chars |
| `password` | Yes | Min 8 characters |
| `is_admin` | No | `true` for full admin access (default: `false`) |
| `custom_role_id` | No | ID of a custom role granting resource and/or system permissions |
| `email` | No | |
| `display_name` | No | |

### List users

```bash
# All users
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/users

# Filter by is_admin or source
curl -H "Authorization: Bearer $TOKEN" \
  'http://localhost:8000/api/v1/users?is_admin=false&source=manual'
```

Query parameters: `source` (`manual`/`ldap`), `is_admin` (`true`/`false`), `status` (`active`/`suspended`), `limit` (default 100).

### Get a user

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/users/1
```

### Update a user

```bash
curl -X PUT http://localhost:8000/api/v1/users/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"is_admin": true, "display_name": "Alice (Admin)"}'
```

Updatable fields: `email`, `display_name`, `is_admin`, `status`, `custom_role_id`.

### Suspend a user

Users are never hard-deleted. `DELETE` sets their status to `suspended`, which prevents login.

```bash
curl -X DELETE -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/users/1
```

To re-activate a suspended user, set their status back to `active` via `PUT`:

```bash
curl -X PUT http://localhost:8000/api/v1/users/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "active"}'
```

---

## Custom roles

Custom roles control access for non-admin users. An admin creates a role, configures its permissions, then assigns it to users via `custom_role_id`.

All custom role endpoints require admin access.

### Create a custom role

Resource permissions and system permissions can be included at creation time:

```bash
curl -X POST http://localhost:8000/api/v1/custom-roles \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "db-writer",
    "description": "Full access to DatabaseCluster resources",
    "system_permissions": ["view_webhooks"],
    "permissions": [
      {
        "resource_type_name": "DatabaseCluster",
        "resource_type_version": "v1",
        "operations": ["CREATE", "READ", "UPDATE", "DELETE"]
      }
    ]
  }'
```

Response:

```json
{
  "id": 3,
  "name": "db-writer",
  "description": "Full access to DatabaseCluster resources",
  "system_permissions": ["view_webhooks"],
  "permissions": [
    {
      "id": 1,
      "role_id": 3,
      "resource_type_name": "DatabaseCluster",
      "resource_type_version": "v1",
      "operations": ["CREATE", "READ", "UPDATE", "DELETE"],
      "created_at": "2024-01-01T00:00:00+00:00"
    }
  ],
  "created_at": "2024-01-01T00:00:00+00:00",
  "updated_at": "2024-01-01T00:00:00+00:00"
}
```

Use `*` as a wildcard for `resource_type_name` or `resource_type_version` to match all values.

### List / get custom roles

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/custom-roles
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/custom-roles/3
```

### Update a custom role

```bash
curl -X PUT http://localhost:8000/api/v1/custom-roles/3 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"system_permissions": ["view_webhooks", "view_plugins"]}'
```

Updatable fields: `name`, `description`, `system_permissions`. To change resource permissions, use the permissions sub-endpoints below.

### Delete a custom role

```bash
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/custom-roles/3
```

Deleting a custom role sets `custom_role_id` to `NULL` on any users assigned to it.

### Managing resource permissions

#### Add a permission

```bash
curl -X POST http://localhost:8000/api/v1/custom-roles/3/permissions \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "resource_type_name": "DnsRecord",
    "resource_type_version": "*",
    "operations": ["READ"]
  }'
```

Permission fields:

| Field | Default | Description |
|---|---|---|
| `resource_type_name` | `*` | Resource type name, or `*` for all types |
| `resource_type_version` | `*` | Resource type version, or `*` for all versions |
| `operations` | all four | List of `CREATE`, `READ`, `UPDATE`, `DELETE` |

Each `(role_id, resource_type_name, resource_type_version)` combination must be unique. Adding a second permission for the same scope returns 409.

#### Update a permission

```bash
curl -X PUT http://localhost:8000/api/v1/custom-roles/3/permissions/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"operations": ["READ"]}'
```

#### Remove a permission

```bash
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/custom-roles/3/permissions/1
```

### Assigning a custom role to a user

Set `custom_role_id` when creating a user or update it later:

```bash
curl -X PUT http://localhost:8000/api/v1/users/5 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"custom_role_id": 3}'
```

Set to `null` to remove the custom role assignment.

---

## LDAP integration

LDAP support is entirely optional. When `LDAP_URL` is not set the operator runs in manual-user-only mode.

### How it works

1. The operator binds to the directory using a service account (`LDAP_BIND_DN` / `LDAP_BIND_PASSWORD`) and searches for users matching `LDAP_USER_FILTER` under `LDAP_BASE_DN`.
2. Each found entry is upserted into the local users table (keyed on username). New users are created with `is_admin=False` and no custom role; an admin assigns roles post-sync.
3. At login time, instead of checking a stored password, the operator attempts a bind to the LDAP server using the user's stored DN and the password they provided. This means the LDAP server is the single source of truth for credentials — password changes in the directory take effect immediately.

LDAP users **must be synced** before they can log in. A user who exists in the directory but has never been synced will get a 401.

### Configuration

| Variable | Default | Description |
|---|---|---|
| `LDAP_URL` | — | Enables LDAP. e.g. `ldap://ldap.example.com:389` or `ldaps://ldap.example.com:636` |
| `LDAP_BIND_DN` | — | Service account DN used to search the directory |
| `LDAP_BIND_PASSWORD` | — | Service account password |
| `LDAP_BASE_DN` | — | Search base, e.g. `ou=people,dc=example,dc=com` |
| `LDAP_USER_FILTER` | `(objectClass=inetOrgPerson)` | LDAP search filter |
| `LDAP_ATTR_USERNAME` | `uid` | Attribute used as the no8s username |
| `LDAP_ATTR_EMAIL` | `mail` | Attribute mapped to email |
| `LDAP_ATTR_DISPLAY_NAME` | `cn` | Attribute mapped to display name |
| `LDAP_SYNC_INTERVAL` | `0` | Seconds between automatic syncs. `0` disables background sync. |

Example startup with LDAP enabled and automatic sync every 10 minutes:

```bash
JWT_SECRET_KEY=your-long-random-secret \
LDAP_URL=ldap://ldap.example.com:389 \
LDAP_BIND_DN="cn=svc-no8s,ou=service-accounts,dc=example,dc=com" \
LDAP_BIND_PASSWORD=svc-password \
LDAP_BASE_DN="ou=people,dc=example,dc=com" \
LDAP_SYNC_INTERVAL=600 \
python src/main.py
```

### Trigger a manual sync

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8000/api/v1/users/ldap-sync
```

Response:

```json
{
  "created": 12,
  "updated": 3,
  "total": 15
}
```

- `created` — users added for the first time
- `updated` — existing LDAP users whose attributes were refreshed
- `total` — total users found in the directory during this sync

### Active Directory

Active Directory uses `sAMAccountName` as the login attribute rather than `uid`. Override the defaults:

```bash
LDAP_USER_FILTER="(objectClass=user)"
LDAP_ATTR_USERNAME=sAMAccountName
LDAP_ATTR_EMAIL=mail
LDAP_ATTR_DISPLAY_NAME=cn
```

---

## RBAC reference

Resource endpoints use per-resource-type permission checks. Admins pass all checks unconditionally. Non-admin users must have a custom role with a matching permission for the resource type and operation.

| Endpoint | Required |
|---|---|
| `POST /api/v1/auth/login` | None (public) |
| `GET /api/v1/auth/me` | Any authenticated user |
| `GET /api/v1/users` | Admin |
| `POST /api/v1/users` | Admin |
| `GET /api/v1/users/{id}` | Admin |
| `PUT /api/v1/users/{id}` | Admin |
| `DELETE /api/v1/users/{id}` | Admin |
| `POST /api/v1/users/ldap-sync` | Admin |
| `GET /api/v1/custom-roles` | Admin |
| `POST /api/v1/custom-roles` | Admin |
| `GET /api/v1/custom-roles/{id}` | Admin |
| `PUT /api/v1/custom-roles/{id}` | Admin |
| `DELETE /api/v1/custom-roles/{id}` | Admin |
| `POST /api/v1/custom-roles/{id}/permissions` | Admin |
| `PUT /api/v1/custom-roles/{id}/permissions/{perm_id}` | Admin |
| `DELETE /api/v1/custom-roles/{id}/permissions/{perm_id}` | Admin |
| `GET /api/v1/resource-types` (all three GET routes) | Any authenticated user |
| `POST /api/v1/resource-types` | Admin |
| `PUT /api/v1/resource-types/{id}` | Admin |
| `DELETE /api/v1/resource-types/{id}` | Admin |
| `GET /api/v1/resources` | Any authenticated user (list filtered by custom role READ permissions) |
| `POST /api/v1/resources` | Custom role with CREATE on the resource type |
| `GET /api/v1/resources/{id}` | Custom role with READ on the resource type |
| `GET /api/v1/resources/by-name/{type}/{version}/{name}` | Custom role with READ on the resource type |
| `PUT /api/v1/resources/{id}` | Custom role with UPDATE on the resource type |
| `DELETE /api/v1/resources/{id}` | Custom role with DELETE on the resource type |
| `PUT /api/v1/resources/{id}/finalizers` | Custom role with UPDATE on the resource type |
| `POST /api/v1/resources/{id}/reconcile` | Custom role with UPDATE on the resource type |
| `GET /api/v1/resources/{id}/history` | Custom role with READ on the resource type |
| `GET /api/v1/resources/{id}/outputs` | Custom role with READ on the resource type |
| `GET /api/v1/resources/{id}/events` | Custom role with READ on the resource type |
| `GET /api/v1/events` | Any authenticated user (events filtered to READ-permitted types) |
| `GET /api/v1/admission-webhooks` | Custom role with `view_webhooks` system permission |
| `POST /api/v1/admission-webhooks` | Admin |
| `GET /api/v1/admission-webhooks/{id}` | Custom role with `view_webhooks` system permission |
| `PUT /api/v1/admission-webhooks/{id}` | Admin |
| `DELETE /api/v1/admission-webhooks/{id}` | Admin |
| `GET /api/v1/plugins/actions` | Custom role with `view_plugins` system permission |
| `GET /api/v1/plugins/inputs` | Custom role with `view_plugins` system permission |
| `GET /` (health check) | None (public) |
