CREATE TABLE IF NOT EXISTS cluster_nodes (
    node_id VARCHAR(255) PRIMARY KEY,
    hostname VARCHAR(255) NOT NULL,
    pid VARCHAR(50),
    first_seen TIMESTAMP NOT NULL DEFAULT NOW(),
    last_heartbeat TIMESTAMP NOT NULL DEFAULT NOW(),
    lease_duration_seconds INTEGER NOT NULL DEFAULT 60
);