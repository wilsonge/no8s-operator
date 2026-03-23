# no8s-operator

A Kubernetes-style controller for managing infrastructure without Kubernetes. Define desired state as resources; reconciler plugins continuously ensure reality matches.

## WARNING
This project is being largely "vibe-coded" with minimal human review during the build out phase as I test the limits of
Claude Code. The intention will be after a first phase to do a full human review of the code. It is the intention to make
this fully production ready! But be warned if you're looking at it during these early development phases.

## What it is

no8s-operator brings declarative infrastructure management to any environment — drift detection, automatic reconciliation,
structured status tracking, and audit history — backed by PostgreSQL and a lightweight plugin architecture. Define the
desired state of your resources; reconciler plugins do the rest.

**Built for real-world operational needs:**

- **Multicloud and multi-region by design** — because state lives in PostgreSQL, you can run operator instances across
  clouds and regions against a shared or replicated store. Manage AWS, GCP, and Azure resources from a single control
  plane, or deploy region-local instances that share state seamlessly.
- **True active/passive DR** — failover is a DNS swap. Promote a standby instance in seconds with no cluster snapshot
  restore, no etcd archaeology, and no cloud-specific recovery runbook.
- **Fits your existing stack** — no container orchestrator required. Run it wherever you can run Python and PostgreSQL:
  VMs, bare metal, managed container services, or a single laptop.
- **Extensible by design** — reconciler plugins are plain pip packages, auto-discovered via Python entry points. Teams
  ship domain-specific reconcilers independently without touching core operator code.

## Features

- **Declarative infrastructure** — define desired state; reconciler plugins drive resources to `ready`
- **Resource types with schema validation** — OpenAPI v3 schemas, similar to Kubernetes CRDs
- **3rd party reconciler plugins** — pip packages auto-discovered via Python entry points
- **Authentication and RBAC** — JWT bearer tokens, bcrypt passwords, LDAP integration, custom roles with per-resource-type CRUD permissions
- **Finalizers** — Kubernetes-style deletion protection
- **Status conditions** — named conditions (`Ready`, `Reconciling`, `Degraded`) plus domain-specific conditions from reconciler plugins
- **Admission webhooks** — validating and mutating webhooks before resource persistence
- **Event streaming** — Server-Sent Events for real-time watch semantics (`kubectl get --watch` equivalent)
- **Audit history** — complete log of all reconciliation attempts
- **Automatic Reconciliation**: Continuous drift detection and correction
- **Exponential Backoff**: Failed reconciliations retry with intelligent backoff
- **Concurrent Reconciliation**: Multiple resources reconciled in parallel

## Architecture

The system follows a delegated controller pattern inspired by Kubernetes:

```
┌──────────────────────────────────────────────────────────────────┐
│                      Operator Controller                         │
│                                                                  │
│  ┌────────────────┐    ┌──────────────────────────────────────┐  │
│  │  Input Plugins │───▶│      Main Loop (controller.py)       │  │
│  │  (HTTP, SQS,   │    │                                      │  │
│  │   Polling)     │    │  1. Receive resource events          │  │
│  └────────────────┘    │  2. Cache resource state             │  │
│                        │  3. Dispatch to reconciler plugin    │  │
│                        │  4. Update status and metadata       │  │
│                        └──────────┬───────────────────────────┘  │
│                                   │                              │
│              ┌────────────────────┼─────────────────────┐        │
│              │                                          │        │
│              ▼                                          ▼        │
│  ┌───────────────────┐                       ┌─────────────────┐ │
│  │ Reconciler Plugin │                       │Reconciler Plugin│ │
│  │ (pip: no8s-db)    │                       │(pip: no8s-dns)  │ │
│  │                   │                       │                 │ │
│  │ ResourceType:     │                       │ ResourceType:   │ │
│  │  DatabaseCluster  │                       │  DnsRecord      │ │
│  └────────┬──────────┘                       └───────┬─────────┘ │
│           │ (optional)                               │ (direct)  │
└───────────┼──────────────────────────────────────────┼───────────┘
            ▼                                          ▼
    ┌──────────────┐                           ┌────────────┐
    │ Action Plugin│                           │  External  │
    │ (GitHub      │                           │  API       │
    │  Actions)    │                           │            │
    └──────────────┘                           └────────────┘
            │
            ▼
    ┌──────────┐
    │PostgreSQL│
    │ Resource │
    │  Store   │
    └──────────┘
```

## Quick start

```bash
git clone <repo-url>
docker-compose up -d
docker-compose logs -f controller-api
```

See [docs/installation.md](docs/installation.md) for manual installation, environment variable reference, and plugin discovery verification.

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
- [ ] Add ability to require metadata keys on resource types (similar to AWS SCP's)
- [ ] Add roles that can only see resources with certain metadata (similar to AWS ABAC)
- [ ] Create an input plugin for polling external APIs for state changes (HTTP APIs)
- [ ] Create an input plugin for Queue Listeners such as SQS, RabbitMQ

## License

GPL-3.0-or-later