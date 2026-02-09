-- Initial schema: resources, reconciliation_history, locks, resource_types

CREATE TABLE IF NOT EXISTS resources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,

    -- Resource type reference
    resource_type_name VARCHAR(255) NOT NULL,
    resource_type_version VARCHAR(50) NOT NULL,

    -- Plugin architecture fields
    action_plugin VARCHAR(50) NOT NULL,
    spec JSONB DEFAULT '{}',
    plugin_config JSONB DEFAULT '{}',
    metadata JSONB DEFAULT '{}',
    outputs JSONB DEFAULT '{}',

    -- Status fields
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    status_message TEXT,
    generation INTEGER NOT NULL DEFAULT 1,
    observed_generation INTEGER DEFAULT 0,
    spec_hash VARCHAR(64) NOT NULL,
    retry_count INTEGER DEFAULT 0,
    last_reconcile_time TIMESTAMP,
    next_reconcile_time TIMESTAMP,

    -- Finalizers (Kubernetes-style deletion protection)
    finalizers JSONB NOT NULL DEFAULT '[]',

    -- Timestamps
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMP,

    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS reconciliation_history (
    id SERIAL PRIMARY KEY,
    resource_id INTEGER NOT NULL REFERENCES resources(id)
        ON DELETE CASCADE,
    generation INTEGER NOT NULL,
    success BOOLEAN NOT NULL,
    phase VARCHAR(50) NOT NULL,
    plan_output TEXT,
    apply_output TEXT,
    error_message TEXT,
    resources_created INTEGER DEFAULT 0,
    resources_updated INTEGER DEFAULT 0,
    resources_deleted INTEGER DEFAULT 0,
    duration_seconds FLOAT,
    trigger_reason VARCHAR(50),
    drift_detected BOOLEAN DEFAULT FALSE,
    reconcile_time TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS locks (
    resource_key VARCHAR(255) PRIMARY KEY,
    holder_id VARCHAR(255) NOT NULL,
    acquired_at TIMESTAMP NOT NULL DEFAULT NOW(),
    lease_duration_seconds INTEGER NOT NULL DEFAULT 30
);

CREATE TABLE IF NOT EXISTS resource_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    version VARCHAR(50) NOT NULL,
    schema JSONB NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(name, version)
);

CREATE INDEX IF NOT EXISTS idx_resources_status
ON resources(status) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_resources_next_reconcile
ON resources(next_reconcile_time) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_resources_action_plugin
ON resources(action_plugin) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_reconciliation_history_resource
ON reconciliation_history(resource_id, reconcile_time DESC);

CREATE INDEX IF NOT EXISTS idx_resource_types_name
ON resource_types(name, version);

CREATE INDEX IF NOT EXISTS idx_resources_type
ON resources(resource_type_name, resource_type_version)
WHERE deleted_at IS NULL;
