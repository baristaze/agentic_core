-- The engine's own tables of the activity role, on the base's: each agent
-- session's history, written once and numbered with no gaps, and its
-- cursor; the spend budgets' tallies, holds, and settlements; the usage
-- record of each model call; and the tool outputs a history keeps whole.
-- Each is fenced to its tenant, or the system scope to the system login
-- alone (ADR 0016). The serving logins' grants come from the role's default
-- privileges, less UPDATE and DELETE on what is written once (ADR 1002,
-- ADR 1006, ADR 1014); the purge login reaches a history, its cursor, and
-- its tool outputs, within the tenant its transaction names (ADR 1010).

CREATE TABLE activity.agent_step_cursors (
    org_id uuid NOT NULL,
    agent_session_id uuid NOT NULL,
    head bigint NOT NULL,
    epoch bigint NOT NULL,
    CONSTRAINT pk_agent_step_cursors PRIMARY KEY (org_id, agent_session_id)
);
ALTER TABLE activity.agent_step_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.agent_step_cursors FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.agent_step_cursors
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

CREATE TABLE activity.agent_steps (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    agent_session_id uuid NOT NULL,
    seq bigint NOT NULL,
    loop_id uuid NOT NULL,
    type text NOT NULL,
    actor text NOT NULL,
    origin text NOT NULL,
    responds_to uuid,
    refs jsonb NOT NULL,
    header jsonb NOT NULL,
    content jsonb NOT NULL,
    children jsonb NOT NULL,
    CONSTRAINT pk_agent_steps PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_agent_steps_org_id_agent_session_id_seq ON activity.agent_steps (org_id, agent_session_id, seq);
ALTER TABLE activity.agent_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.agent_steps FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.agent_steps
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

CREATE TABLE activity.spend_budget_holds (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    spender_id uuid NOT NULL,
    agent_session_id uuid,
    purpose text NOT NULL,
    exposure jsonb NOT NULL,
    own jsonb,
    lines jsonb NOT NULL,
    CONSTRAINT pk_spend_budget_holds PRIMARY KEY (id)
);
CREATE INDEX ix_spend_budget_holds_org_id ON activity.spend_budget_holds (org_id);
ALTER TABLE activity.spend_budget_holds ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.spend_budget_holds FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.spend_budget_holds
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

CREATE TABLE activity.spend_budget_settlements (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    spend_hold_id uuid NOT NULL,
    bill jsonb NOT NULL,
    spent jsonb NOT NULL,
    overshoot jsonb,
    CONSTRAINT pk_spend_budget_settlements PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_spend_budget_settlements_org_id_spend_hold_id ON activity.spend_budget_settlements (org_id, spend_hold_id);
ALTER TABLE activity.spend_budget_settlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.spend_budget_settlements FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.spend_budget_settlements
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

CREATE TABLE activity.spend_budget_tallies (
    org_id uuid NOT NULL,
    spend_budget_id uuid NOT NULL,
    window_start timestamptz NOT NULL,
    held_cost_micros bigint NOT NULL,
    held_tokens bigint NOT NULL,
    spent_cost_micros bigint NOT NULL,
    spent_tokens bigint NOT NULL,
    CONSTRAINT pk_spend_budget_tallies PRIMARY KEY (org_id, spend_budget_id, window_start)
);
ALTER TABLE activity.spend_budget_tallies ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.spend_budget_tallies FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.spend_budget_tallies
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

CREATE TABLE activity.tool_outputs (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    agent_session_id uuid NOT NULL,
    agent_step_id uuid NOT NULL,
    characters bigint NOT NULL,
    CONSTRAINT pk_tool_outputs PRIMARY KEY (id)
);
CREATE INDEX ix_tool_outputs_org_id_agent_session_id ON activity.tool_outputs (org_id, agent_session_id);
ALTER TABLE activity.tool_outputs ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.tool_outputs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.tool_outputs
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

CREATE TABLE activity.usage_records (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    spend_hold_id uuid NOT NULL,
    agent_session_id uuid NOT NULL,
    tree_id uuid NOT NULL,
    loop_id uuid NOT NULL,
    agent_step_id uuid NOT NULL,
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
    settled_whole boolean NOT NULL,
    CONSTRAINT pk_usage_records PRIMARY KEY (id)
);
CREATE INDEX ix_usage_records_org_id_agent_session_id_id ON activity.usage_records (org_id, agent_session_id, id);
CREATE UNIQUE INDEX uq_usage_records_org_id_spend_hold_id ON activity.usage_records (org_id, spend_hold_id);
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

REVOKE UPDATE, DELETE ON activity.agent_steps FROM acme_runtime, acme_system;
REVOKE UPDATE, DELETE ON activity.spend_budget_holds FROM acme_runtime, acme_system;
REVOKE UPDATE, DELETE ON activity.spend_budget_settlements FROM acme_runtime, acme_system;
GRANT USAGE ON SCHEMA activity TO acme_purge;
GRANT SELECT, DELETE ON activity.agent_steps TO acme_purge;
GRANT SELECT, DELETE ON activity.agent_step_cursors TO acme_purge;
REVOKE UPDATE, DELETE ON activity.tool_outputs FROM acme_runtime, acme_system;
GRANT SELECT, DELETE ON activity.tool_outputs TO acme_purge;
REVOKE UPDATE, DELETE ON activity.usage_records FROM acme_runtime, acme_system;
