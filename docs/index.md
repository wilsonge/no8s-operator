# no8s-operator

A Kubernetes-style controller for managing infrastructure without Kubernetes. Define desired state as resources; reconciler plugins continuously ensure reality matches.

!!! warning
    This project is being largely "vibe-coded" with minimal human review during the build out phase as I test the limits of Claude Code. The intention will be after a first phase to do a full human review of the code. It is the intention to make this fully production ready! But be warned if you're looking at it during these early development phases.

## What it is

In some environments, Kubernetes isn't an option — but you still want the operational benefits of declarative infrastructure management: drift detection, automatic reconciliation, structured status tracking, and audit history. no8s-operator provides that, using PostgreSQL as the backing store and a plugin architecture where 3rd party reconcilers handle domain-specific resource types.

The primary reason here that we use PostgreSQL and drop Kubernetes is to allow this to be provisioned as an active/passive setup that can be failed over at any given time (alongside a DNS swap), allowing users to run across regions or clouds in DR scenarios.

## Features

- **Declarative infrastructure** — define desired state; reconciler plugins drive resources to `ready`
- **Resource types with schema validation** — OpenAPI v3 schemas, similar to Kubernetes CRDs
- **3rd party reconciler plugins** — pip packages auto-discovered via Python entry points (see [Reconciler Architecture](reconciler-architecture.md))
- **Authentication and RBAC** — JWT bearer tokens, bcrypt passwords, LDAP integration, custom roles with per-resource-type CRUD permissions
- **Finalizers** — Kubernetes-style deletion protection
- **Status conditions** — named conditions (`Ready`, `Reconciling`, `Degraded`) plus domain-specific conditions from reconciler plugins
- **Admission webhooks** — validating and mutating webhooks before resource persistence
- **Event streaming** — Server-Sent Events for real-time watch semantics (`kubectl get --watch` equivalent)
- **Audit history** — complete log of all reconciliation attempts

## Quick start

```bash
git clone <repo-url>
docker-compose up -d
docker-compose logs -f controller-api
```

See [Installation](installation.md) for manual installation, environment variable reference, and plugin discovery verification.

## Future enhancements

- [ ] Terraform backend storing state in the operator's PostgreSQL database
- [ ] OIDC authentication for users and services (e.g. GitLab)
- [ ] GitOps integration (watch Git repos for changes)
- [ ] Backstage integration
- [ ] Active/Passive clusters documented for multi-region DR scenarios
- [ ] Prometheus metrics
- [ ] Policy enforcement (OPA integration)
- [ ] Slack/email notifications
- [ ] Stable plugin API with backwards-compatibility guarantees
- [ ] Runtime reconciler hot-reload
- [ ] Add ability to require metadata keys on resource types (similar to SCP's)
- [ ] Add roles that can only see resources with certain metadata (similar to ABAC)
