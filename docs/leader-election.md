# Leader Election

The operator uses distributed leader election so multiple instances can run concurrently
with only **one actively reconciling** at a time. Input plugins (e.g. the HTTP API) run
on all instances regardless of leadership status.

## How it works

Leader election is always enabled. Each instance competes for a row in the `locks` table
using an atomic `INSERT … ON CONFLICT DO UPDATE` query. The winner holds a renewable
lease; all others idle and retry periodically.

```
Instance A ──▶ acquire lock ──▶ LEADER  ──▶ runs reconciliation loop
Instance B ──▶ acquire lock ──▶ not acquired ──▶ sleep, retry
Instance C ──▶ acquire lock ──▶ not acquired ──▶ sleep, retry

If Instance A dies:
Instance B ──▶ lease expires ──▶ acquire lock ──▶ LEADER ──▶ runs reconciliation loop
```

### Lease renewal

The leader renews its lock every `renew_interval_seconds`. If renewal is missed (e.g. the
process crashes), the lease expires after `lease_duration_seconds` and a standby instance
will take over.

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `LEADER_ELECTION_LOCK_NAME` | `no8s-operator-leader` | Lock key in `locks` table |
| `LEADER_ELECTION_HOLDER_ID` | `hostname:pid:uuid` | Unique identity for this instance |
| `LEADER_ELECTION_LEASE_DURATION` | `30` | Lease lifetime in seconds |
| `LEADER_ELECTION_RENEW_INTERVAL` | `10` | How often the leader renews in seconds |
| `LEADER_ELECTION_RETRY_INTERVAL` | `5` | How often non-leaders retry in seconds |

### Holder ID uniqueness

By default the holder ID is `hostname:pid:uuid4`, which is unique per process even across
hosts. Set `LEADER_ELECTION_HOLDER_ID` explicitly only when you need deterministic IDs
(e.g. in tests or static Kubernetes pod names).

## Multi-instance deployment

Run any number of instances pointing at the same PostgreSQL database. No extra
configuration is needed — leader election starts automatically on every instance.

- **Only the leader** runs `controller.start()` (reconciliation and requeue loops).
- **All instances** serve the HTTP API and handle resource mutations.
- On graceful shutdown the leader releases its lock so a standby can take over
  immediately without waiting for lease expiry.
