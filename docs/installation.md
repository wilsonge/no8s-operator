# Installation

This guide walks through installing the no8s operator and adding 3rd party reconciler plugins to it.

## Prerequisites

- Python 3.11+
- PostgreSQL 16+
- A GitHub personal access token with `repo` and `workflow` scopes (only required if reconcilers use the GitHub Actions plugin)

## Install the operator

### From PyPI

```bash
pip install no8s-operator
```

### From source

```bash
git clone <repo-url>
cd no8s-operator
pip install .
```

To include optional extras:

```bash
# LDAP user sync
pip install "no8s-operator[ldap]"

# AWS Secrets Manager secret store backend
pip install "no8s-operator[aws]"
```

## Install reconciler plugins

Reconciler plugins are separate pip packages. Install them into the same Python environment as the operator.

The operator discovers installed reconcilers automatically at startup via the `no8s.reconcilers` Python entry point group — no manual registration step is needed.

You can verify that a reconciler is discoverable before starting the operator:

```bash
python -c "
from importlib.metadata import entry_points
eps = entry_points(group='no8s.reconcilers')
for ep in eps:
    print(ep.name, '->', ep.value)
"
```

## Configure the environment

All configuration is provided via environment variables. Create a `.env` file or export them in your shell:

```bash
# Database (required)
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=operator_controller
export DB_USER=operator
export DB_PASSWORD=operator

# Auth (required)
export JWT_SECRET_KEY=your-long-random-secret-at-least-32-chars

# Bootstrap admin user (only used on first startup when the users table is empty)
export INITIAL_ADMIN_USERNAME=admin
export INITIAL_ADMIN_PASSWORD=changeme123

# GitHub Actions plugin (required only if a reconciler uses it)
export GITHUB_TOKEN=ghp_your_token_here
```

### Secret store (optional)

By default secrets are read from environment variables. To use HashiCorp Vault
or AWS Secrets Manager instead:

```bash
# HashiCorp Vault (no extra packages)
export SECRET_STORE_PLUGIN=vault
export VAULT_ADDR=https://vault.example.com:8200
export VAULT_TOKEN=s.xxxxxxxxxxxx

# AWS Secrets Manager (requires pip install "no8s-operator[aws]")
export SECRET_STORE_PLUGIN=aws_secrets_manager
export AWS_REGION=eu-west-1
```

See [`docs/secret-stores.md`](secret-stores.md) for full configuration options and how to write a custom backend.

### LDAP (optional)

```bash
export LDAP_URL=ldap://ldap.example.com:389
export LDAP_BIND_DN=cn=service,dc=example,dc=com
export LDAP_BIND_PASSWORD=secret
export LDAP_BASE_DN=ou=users,dc=example,dc=com
```

See [`docs/users.md`](users.md) for the full LDAP configuration reference.

## Set up PostgreSQL

Create the database and user before starting the operator:

```bash
psql -U postgres -c "CREATE USER operator WITH PASSWORD 'operator';"
psql -U postgres -c "CREATE DATABASE operator_controller OWNER operator;"
```

The operator creates all required tables on startup — no manual schema migration is needed.

## Start the operator

```bash
python src/main.py
```

On startup the operator:

1. Connects to PostgreSQL and initialises the schema.
2. Bootstraps the initial admin user (if `INITIAL_ADMIN_USERNAME` is set and the users table is empty).
3. Initialises the configured secret store (`SECRET_STORE_PLUGIN`, default `env`).
4. Scans the `no8s.reconcilers` entry point group and registers each discovered reconciler.
5. Starts the controller loop and each reconciler's own reconciliation loop.
6. Starts the HTTP API on port `8000`.

You should see log lines like:

```
Registered reconciler plugin: dns_record (resource types: DnsRecord)
Started reconciler plugin: dns_record
```

Verify the operator is running:

```bash
curl http://localhost:8000/health
```

## Docker Compose

The repository includes a `docker-compose.yml` that starts PostgreSQL and the operator together. To add reconciler plugins, extend the Dockerfile to install them before the operator starts. Then run:

```bash
docker-compose up -d
docker-compose logs -f controller-api
```

## Development installs

When developing a reconciler plugin locally, install it in editable mode into the same environment as the operator so the entry point is registered immediately:

```bash
# Operator
pip install -e /path/to/no8s-operator

# Your plugin (editable)
pip install -e /path/to/my-reconciler-plugin

python src/main.py
```

## Verifying plugin discovery

After startup, the plugin discovery endpoint lists every registered reconciler and action plugin. This requires an authenticated request:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "changeme123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/plugins/actions
```

Non-admin users need the `view_plugins` system permission on their custom role to access this endpoint. See [`docs/users.md`](users.md).

## Next steps

- **Register resource types** — before creating resources, the corresponding resource type (schema) must exist. See [`docs/resource-types.md`](resource-types.md).
- **Create resources** — use `POST /api/v1/resources` to declare desired state. The appropriate reconciler picks it up and drives it to `ready`. See [`docs/resources.md`](resources.md).
- **Write your own reconciler** — see [`docs/writing-a-reconciler.md`](writing-a-reconciler.md) for a full walkthrough.
- **Manage users and RBAC** — see [`docs/users.md`](users.md) for authentication, custom roles, and LDAP.
- **Secret stores** — see [`docs/secret-stores.md`](secret-stores.md) to configure Vault, AWS Secrets Manager, or a custom backend.