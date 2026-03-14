# Cluster Nodes

The `GET /api/v1/cluster/nodes` endpoint provides a real-time view of all operator instances in the cluster — analogous to `kubectl get nodes`. It is intended for operational monitoring, failover diagnosis, and split-brain detection.

Requires an admin JWT bearer token. The public liveness endpoint `GET /api/v1/cluster/health` does not require authentication and is suitable for load balancer health checks.

## Response structure

```json
{
  "this_instance_id": "<node_id of the instance that served this request>",
  "this_instance_is_leader": true,
  "leader_lock": { ... },
  "nodes": [ ... ]
}
```

| Field | Description |
|---|---|
| `this_instance_id` | The `node_id` of the instance that handled this request. Useful behind a load balancer to identify which node you reached. |
| `this_instance_is_leader` | In-memory leadership state of the serving instance. This is the authoritative real-time flag — not derived from the database. |
| `leader_lock` | The current leader lock row from the database, or `null` if no lock exists or the database is unreachable. |
| `nodes` | All instances that have registered a heartbeat, ordered by first registration. Empty if the database is unreachable. |

### `leader_lock` object

| Field | Description |
|---|---|
| `holder_id` | `node_id` of the instance holding the lock. |
| `acquired_at` | When the lock was last acquired or renewed. |
| `expires_at` | `acquired_at` + `lease_duration_seconds`. |
| `is_valid` | `true` if `expires_at` is in the future at query time. |

### Node object

| Field | Description |
|---|---|
| `node_id` | Unique identifier for this instance: `hostname:pid:uuid`. |
| `hostname` | Hostname of the machine running the instance. |
| `pid` | Process ID. |
| `status` | `Ready` if the last heartbeat is within the lease window; `NotReady` if it has expired. |
| `role` | `leader` if this node holds a currently valid leader lock; `follower` otherwise. |
| `age` | Time since first registration in kubectl format (`2d5h`, `45m`, `30s`). |
| `first_seen` | Timestamp when the node first registered. |
| `last_heartbeat` | Timestamp of the most recent heartbeat renewal. |

Node heartbeats are renewed on every leader election loop iteration. A node stops renewing when it loses its database connection. `NotReady` means the instance has not renewed within `lease_duration_seconds` — it is either down, partitioned from the database, or stuck.

## Normal operation — 3-node cluster

In steady state, all nodes are `Ready`, one holds the leader lock, and the others are `follower`.

```json
{
  "this_instance_id": "node-1.example.com:4821:a3f7c2d1-...",
  "this_instance_is_leader": true,
  "leader_lock": {
    "holder_id": "node-1.example.com:4821:a3f7c2d1-...",
    "acquired_at": "2026-03-10T09:12:44Z",
    "expires_at": "2026-03-10T09:13:14Z",
    "is_valid": true
  },
  "nodes": [
    {
      "node_id": "node-1.example.com:4821:a3f7c2d1-...",
      "hostname": "node-1.example.com",
      "pid": "4821",
      "status": "Ready",
      "role": "leader",
      "age": "2d5h",
      "first_seen": "2026-03-08T04:07:33Z",
      "last_heartbeat": "2026-03-10T09:13:09Z"
    },
    {
      "node_id": "node-2.example.com:4739:b8e1d409-...",
      "hostname": "node-2.example.com",
      "pid": "4739",
      "status": "Ready",
      "role": "follower",
      "age": "2d5h",
      "first_seen": "2026-03-08T04:08:01Z",
      "last_heartbeat": "2026-03-10T09:13:07Z"
    },
    {
      "node_id": "node-3.example.com:4802:c1f94e77-...",
      "hostname": "node-3.example.com",
      "pid": "4802",
      "status": "Ready",
      "role": "follower",
      "age": "2d5h",
      "first_seen": "2026-03-08T04:09:12Z",
      "last_heartbeat": "2026-03-10T09:13:05Z"
    }
  ]
}
```

## Graceful failover

When the leader shuts down cleanly it releases the lock immediately (rather than waiting for lease expiry), and deregisters itself from the node table. A standby wins the lock within one `retry_interval_seconds` cycle.

Querying any surviving node shortly after:

```json
{
  "this_instance_id": "node-2.example.com:4739:b8e1d409-...",
  "this_instance_is_leader": true,
  "leader_lock": {
    "holder_id": "node-2.example.com:4739:b8e1d409-...",
    "acquired_at": "2026-03-10T10:30:02Z",
    "expires_at": "2026-03-10T10:30:32Z",
    "is_valid": true
  },
  "nodes": [
    {
      "node_id": "node-2.example.com:4739:b8e1d409-...",
      "hostname": "node-2.example.com",
      "pid": "4739",
      "status": "Ready",
      "role": "leader",
      "age": "2d5h",
      "first_seen": "2026-03-08T04:08:01Z",
      "last_heartbeat": "2026-03-10T10:30:27Z"
    },
    {
      "node_id": "node-3.example.com:4802:c1f94e77-...",
      "hostname": "node-3.example.com",
      "pid": "4802",
      "status": "Ready",
      "role": "follower",
      "age": "2d5h",
      "first_seen": "2026-03-08T04:09:12Z",
      "last_heartbeat": "2026-03-10T10:30:25Z"
    }
  ]
}
```

node-1 is absent from the node list because it deregistered on shutdown. If the process crashes instead of shutting gracefully, node-1 remains in the list as `NotReady` until its heartbeat expires, and the leader lock stays until it expires — see [Crash failover](#crash-failover) below.

## Crash failover

When the leader crashes, it cannot release the lock or deregister. The cluster must wait up to `lease_duration_seconds` before a standby can acquire the lock.

### Immediately after crash (lock still valid)

The crashed leader's heartbeat has already stopped, so its `status` flips to `NotReady` quickly (within one lease window). But the lock `is_valid` remains `true` until `expires_at` passes — no instance shows `role: leader` yet because the lock holder is no longer active.

```json
{
  "this_instance_id": "node-2.example.com:4739:b8e1d409-...",
  "this_instance_is_leader": false,
  "leader_lock": {
    "holder_id": "node-1.example.com:4821:a3f7c2d1-...",
    "acquired_at": "2026-03-10T10:44:40Z",
    "expires_at": "2026-03-10T10:45:10Z",
    "is_valid": true
  },
  "nodes": [
    {
      "node_id": "node-1.example.com:4821:a3f7c2d1-...",
      "hostname": "node-1.example.com",
      "pid": "4821",
      "status": "NotReady",
      "role": "follower",
      "age": "2d5h",
      "first_seen": "2026-03-08T04:07:33Z",
      "last_heartbeat": "2026-03-10T10:44:38Z"
    },
    ...
  ]
}
```

The transient state `status: NotReady` + `role: follower` on the lock holder is the crash signal. `is_valid: true` means no standby has taken over yet.

### After lease expiry — new leader elected

Once `expires_at` passes, a standby acquires the lock within `retry_interval_seconds`.

```json
{
  "this_instance_id": "node-2.example.com:4739:b8e1d409-...",
  "this_instance_is_leader": true,
  "leader_lock": {
    "holder_id": "node-2.example.com:4739:b8e1d409-...",
    "acquired_at": "2026-03-10T10:45:15Z",
    "expires_at": "2026-03-10T10:45:45Z",
    "is_valid": true
  },
  "nodes": [
    {
      "node_id": "node-1.example.com:4821:a3f7c2d1-...",
      "hostname": "node-1.example.com",
      "pid": "4821",
      "status": "NotReady",
      "role": "follower",
      "age": "2d5h",
      "first_seen": "2026-03-08T04:07:33Z",
      "last_heartbeat": "2026-03-10T10:44:38Z"
    },
    {
      "node_id": "node-2.example.com:4739:b8e1d409-...",
      "hostname": "node-2.example.com",
      "pid": "4739",
      "status": "Ready",
      "role": "leader",
      "age": "2d5h",
      "first_seen": "2026-03-08T04:08:01Z",
      "last_heartbeat": "2026-03-10T10:45:40Z"
    },
    ...
  ]
}
```

The crashed node stays `NotReady` in the list indefinitely (its row is never deleted because it never deregistered). This is intentional — it acts as a record that the node existed. If the process restarts on the same host it will re-register under a new `node_id` (new PID and UUID).

### Failover timing

| Event | Time after crash |
|---|---|
| Node heartbeat stops renewing | Immediate |
| Node shows `NotReady` | Up to `lease_duration_seconds` |
| Leader lock expires | Up to `lease_duration_seconds` |
| Standby acquires lock and starts reconciling | Lock expiry + up to `retry_interval_seconds` |
| **Worst-case total outage** | **`lease_duration_seconds` + `retry_interval_seconds`** |

With defaults (`lease_duration_seconds: 30`, `retry_interval_seconds: 5`) the worst-case reconciliation gap is **35 seconds**. The HTTP API continues serving on all surviving instances throughout.

## AZ partition / split-brain

In a network partition, some nodes lose access to the database. The response differs depending on which side of the partition you query.

### Setup

Three nodes across two AZs, database accessible from AZ-b:

```
AZ-a: node-1 (original leader)
AZ-b: node-2, node-3, PostgreSQL
```

Partition occurs at 09:45. The database becomes unreachable from AZ-a.

### Querying the partitioned side (AZ-a — node-1)

node-1's election loop detects the DB failure, clears `is_leader` (so it stops reconciling). Both the lock and node list queries fail and are swallowed. The endpoint still returns HTTP 200 but with minimal data.

`GET /api/v1/cluster/health` on node-1 returns `503 disconnected` simultaneously.

```json
{
  "this_instance_id": "node-1.example.com:4821:a3f7c2d1-...",
  "this_instance_is_leader": false,
  "leader_lock": null,
  "nodes": []
}
```

### Querying the healthy side (AZ-b — node-2, after lock expiry and re-election)

Once node-1's lock expires, node-2 acquires it. From AZ-b the cluster looks normal, except node-1 shows as `NotReady`.

```json
{
  "this_instance_id": "node-2.example.com:4739:b8e1d409-...",
  "this_instance_is_leader": true,
  "leader_lock": {
    "holder_id": "node-2.example.com:4739:b8e1d409-...",
    "acquired_at": "2026-03-10T09:46:15Z",
    "expires_at": "2026-03-10T09:46:45Z",
    "is_valid": true
  },
  "nodes": [
    {
      "node_id": "node-1.example.com:4821:a3f7c2d1-...",
      "hostname": "node-1.example.com",
      "pid": "4821",
      "status": "NotReady",
      "role": "follower",
      "age": "2d5h",
      "first_seen": "2026-03-08T04:07:33Z",
      "last_heartbeat": "2026-03-10T09:44:58Z"
    },
    {
      "node_id": "node-2.example.com:4739:b8e1d409-...",
      "hostname": "node-2.example.com",
      "pid": "4739",
      "status": "Ready",
      "role": "leader",
      "age": "2d5h",
      "first_seen": "2026-03-08T04:08:01Z",
      "last_heartbeat": "2026-03-10T09:46:40Z"
    },
    {
      "node_id": "node-3.example.com:4802:c1f94e77-...",
      "hostname": "node-3.example.com",
      "pid": "4802",
      "status": "Ready",
      "role": "follower",
      "age": "2d5h",
      "first_seen": "2026-03-08T04:09:12Z",
      "last_heartbeat": "2026-03-10T09:46:38Z"
    }
  ]
}
```

### Reading the partition signal

| Signal | Partitioned side | Healthy side |
|---|---|---|
| `GET /api/v1/cluster/health` | `503 disconnected` | `200 ok` |
| `this_instance_is_leader` | `false` | `true` (new leader) |
| `leader_lock` | `null` | valid, new holder |
| `nodes` | `[]` | node-1 `NotReady`, others `Ready` |

The combination of `leader_lock: null` + `nodes: []` + `this_instance_is_leader: false` is the unambiguous database-isolation signature. A single `null` or empty field alone could mean other things (e.g. no nodes have registered yet on a fresh cluster), but all three together mean the serving instance cannot reach the database.

### Transient split-brain window

There is a brief window between the partition occurring and node-1's lock expiring where the AZ-b side still sees node-1 as `role: leader` with `is_valid: true` in `leader_lock`, but `status: NotReady` in the node list — the lock holder is no longer alive but the lease has not yet expired.

```json
{
  "leader_lock": {
    "holder_id": "node-1.example.com:4821:a3f7c2d1-...",
    "acquired_at": "2026-03-10T09:44:50Z",
    "expires_at": "2026-03-10T09:45:20Z",
    "is_valid": true
  },
  "nodes": [
    {
      "node_id": "node-1.example.com:4821:a3f7c2d1-...",
      "status": "NotReady",
      "role": "leader",
      ...
    },
    ...
  ]
}
```

`status: NotReady` + `role: leader` on the same node is the split-brain-in-progress indicator. This resolves automatically once the lease expires and a standby takes over. The window is bounded by `lease_duration_seconds`.

## Relationship between `this_instance_is_leader` and `leader_lock`

Because `this_instance_is_leader` is an in-memory flag and `leader_lock` is read from the database, they can diverge in edge cases. The combinations and their meaning:

| `this_instance_is_leader` | `leader_lock.holder_id` matches `this_instance_id` | `leader_lock.is_valid` | Meaning |
|---|---|---|---|
| `true` | yes | `true` | Normal — this instance is the active leader |
| `true` | no | `true` | Two instances believe they are leader; partition resolving |
| `true` | — | — | `leader_lock: null` — DB unreachable; this instance still thinks it leads but cannot renew |
| `false` | yes | `true` | Lock acquired in DB but in-memory flag not yet set; transient during startup |
| `false` | no | `true` | Normal follower |
| `false` | — | — | `leader_lock: null` — DB unreachable on a follower (most common partition signature) |

In practice the last case (`this_instance_is_leader: false`, `leader_lock: null`, `nodes: []`) is what you see on the partitioned side of an AZ split.