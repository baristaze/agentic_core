-- One usage record per billed model call, written once, in every storage
-- mode: ids, token counts, reference cost, a duration, and labels, and no
-- content (ADR 1014).

CREATE TABLE activity.usage_records (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    hold_id uuid NOT NULL,
    session_id uuid NOT NULL,
    tree_id uuid NOT NULL,
    loop_id uuid NOT NULL,
    step_id uuid NOT NULL,
    agent_kind text NOT NULL,
    kind_version integer NOT NULL,
    role text NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    input_tokens bigint NOT NULL,
    cache_read_tokens bigint NOT NULL,
    cache_write_tokens bigint NOT NULL,
    output_tokens bigint NOT NULL,
    thinking_tokens bigint NOT NULL,
    cost_micros bigint,
    latency_ms bigint NOT NULL,
    CONSTRAINT pk_usage_records PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_usage_records_org_id_hold_id ON activity.usage_records (org_id, hold_id);
CREATE INDEX ix_usage_records_org_id_session_id_id ON activity.usage_records (org_id, session_id, id);

-- The second fence: the transaction's own tenant, or the system scope to the
-- system login alone (ADR 0016).

ALTER TABLE activity.usage_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.usage_records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.usage_records
    USING (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
        OR (
            current_setting('app.org_id', true) = '00000000-0000-0000-0000-000000000000'
            AND current_user = 'acme_system'
        )
    );

-- A record is written once. The role's default privileges grant the serving
-- logins every DML statement on a new table; here they keep SELECT and
-- INSERT. The purge login is granted nothing: a record outlives its
-- session's purge and its tenant's, as the ledger does.

REVOKE UPDATE, DELETE ON activity.usage_records FROM acme_runtime, acme_system;
