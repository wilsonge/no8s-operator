-- Admission webhooks: validating and mutating HTTP callbacks

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

CREATE INDEX IF NOT EXISTS idx_admission_webhooks_type
ON admission_webhooks(webhook_type, ordering);

CREATE INDEX IF NOT EXISTS idx_admission_webhooks_resource_type
ON admission_webhooks(resource_type_name, resource_type_version);
