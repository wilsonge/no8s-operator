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
    conditions JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Finalizers (Kubernetes-style deletion protection)
    finalizers JSONB NOT NULL DEFAULT '[]'::jsonb,

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

CREATE TABLE IF NOT EXISTS admission_webhooks (
                                                  id SERIAL PRIMARY KEY,
                                                  name VARCHAR(255) NOT NULL UNIQUE,
    resource_type_name VARCHAR(255),
    resource_type_version VARCHAR(50),
    webhook_url VARCHAR(2048) NOT NULL,
    webhook_type VARCHAR(20) NOT NULL,
    operations JSONB NOT NULL,
    timeout_seconds INTEGER NOT NULL DEFAULT 10,
    failure_policy VARCHAR(10) NOT NULL DEFAULT 'Fail',
    ordering INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

CREATE TABLE custom_roles (
    id                 SERIAL PRIMARY KEY,
    name               VARCHAR(255) UNIQUE NOT NULL,
    description        TEXT,
    system_permissions JSONB NOT NULL DEFAULT '[]',
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE TYPE user_source AS ENUM ('manual', 'ldap');
CREATE TYPE user_status AS ENUM ('active', 'suspended');

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(255) UNIQUE NOT NULL,
    email           VARCHAR(255),
    display_name    VARCHAR(255),
    source          user_source NOT NULL DEFAULT 'manual',
    is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
    status          user_status NOT NULL DEFAULT 'active',
    custom_role_id  INTEGER REFERENCES custom_roles(id) ON DELETE SET NULL,
    password_hash   TEXT,          -- NULL for LDAP users
    ldap_dn         TEXT,          -- Distinguished Name (LDAP users only)
    ldap_uid        TEXT,          -- UID attribute value (LDAP users only)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ,
    last_synced_at  TIMESTAMPTZ    -- populated on LDAP sync
    );

CREATE TABLE custom_role_permissions (
    id                    SERIAL PRIMARY KEY,
    role_id               INTEGER NOT NULL REFERENCES custom_roles(id) ON DELETE CASCADE,
    resource_type_name    VARCHAR(255) NOT NULL DEFAULT '*',
    resource_type_version VARCHAR(255) NOT NULL DEFAULT '*',
    operations            JSONB NOT NULL DEFAULT '["CREATE","READ","UPDATE","DELETE"]',
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (role_id, resource_type_name, resource_type_version)
);

CREATE TABLE IF NOT EXISTS cluster_nodes (
    node_id VARCHAR(255) PRIMARY KEY,
    hostname VARCHAR(255) NOT NULL,
    pid VARCHAR(50),
    first_seen TIMESTAMP NOT NULL DEFAULT NOW(),
    last_heartbeat TIMESTAMP NOT NULL DEFAULT NOW(),
    lease_duration_seconds INTEGER NOT NULL DEFAULT 60
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

CREATE INDEX IF NOT EXISTS idx_admission_webhooks_type
    ON admission_webhooks(webhook_type, ordering);

CREATE INDEX IF NOT EXISTS idx_admission_webhooks_resource_type
    ON admission_webhooks(resource_type_name, resource_type_version);

CREATE INDEX IF NOT EXISTS idx_resources_conditions
    ON resources USING GIN (conditions)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_source   ON users(source);
CREATE INDEX IF NOT EXISTS idx_users_status   ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_ldap_dn  ON users(ldap_dn);

CREATE INDEX IF NOT EXISTS idx_users_custom_role_id ON users(custom_role_id);
