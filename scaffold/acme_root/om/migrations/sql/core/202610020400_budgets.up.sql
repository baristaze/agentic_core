-- The budgets: each a scope, a window, and an amount, which binds every call
-- charged to its scope. The ledger that counts against them is the activity
-- role's; a hold carries each budget's amount as the gate read it, so
-- nothing crosses a role (ADR 1006).

CREATE TABLE core.budgets (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    scope_kind text NOT NULL,
    scope_key text NOT NULL,
    window_kind text NOT NULL,
    window_seconds integer,
    cost_micros bigint,
    tokens bigint,
    version integer NOT NULL,
    CONSTRAINT pk_budgets PRIMARY KEY (id)
);
CREATE INDEX ix_budgets_org_id_id ON core.budgets (org_id, id);
CREATE INDEX ix_budgets_org_id_scope_kind_scope_key ON core.budgets (org_id, scope_kind, scope_key);

-- The second fence: the transaction's own tenant, or the system scope to the
-- system login alone (ADR 0016). The serving logins' grants come from the
-- role's default privileges.

ALTER TABLE core.budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.budgets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.budgets
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
