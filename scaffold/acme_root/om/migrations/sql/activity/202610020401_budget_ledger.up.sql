-- The ledger: one hold per call, written once, one settlement per hold,
-- written once, and one tally per budget and window, which a hold and its
-- settlement move under the tally's lock in the transaction that writes
-- them (ADR 1006).

CREATE TABLE activity.budget_tallies (
    org_id uuid NOT NULL,
    budget_id uuid NOT NULL,
    window_start timestamptz NOT NULL,
    held_cost_micros bigint NOT NULL,
    held_tokens bigint NOT NULL,
    spent_cost_micros bigint NOT NULL,
    spent_tokens bigint NOT NULL,
    CONSTRAINT pk_budget_tallies PRIMARY KEY (org_id, budget_id, window_start)
);

CREATE TABLE activity.budget_holds (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    spender_id uuid NOT NULL,
    session_id uuid,
    purpose text NOT NULL,
    exposure jsonb NOT NULL,
    own jsonb,
    lines jsonb NOT NULL,
    CONSTRAINT pk_budget_holds PRIMARY KEY (id)
);
CREATE INDEX ix_budget_holds_org_id ON activity.budget_holds (org_id);

CREATE TABLE activity.budget_settlements (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    hold_id uuid NOT NULL,
    bill jsonb NOT NULL,
    spent jsonb NOT NULL,
    overshoot jsonb,
    CONSTRAINT pk_budget_settlements PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_budget_settlements_org_id_hold_id ON activity.budget_settlements (org_id, hold_id);

-- The second fence: the transaction's own tenant, or the system scope to the
-- system login alone (ADR 0016).

ALTER TABLE activity.budget_tallies ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.budget_tallies FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.budget_tallies
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

ALTER TABLE activity.budget_holds ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.budget_holds FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.budget_holds
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

ALTER TABLE activity.budget_settlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.budget_settlements FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.budget_settlements
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

-- A hold and a settlement are written once. The role's default privileges
-- grant the serving logins every DML statement on a new table; on these two
-- they keep SELECT and INSERT, so no statement a process sends rewrites what
-- a call held or what it spent.

REVOKE UPDATE, DELETE ON activity.budget_holds FROM acme_runtime, acme_system;
REVOKE UPDATE, DELETE ON activity.budget_settlements FROM acme_runtime, acme_system;
