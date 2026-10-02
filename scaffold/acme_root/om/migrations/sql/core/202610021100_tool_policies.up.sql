-- Each tenant's layer of tool policy, one row a tenant: the rules that
-- narrow or loosen an agent kind's defaults, and who approves each class.

CREATE TABLE core.tool_policies (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    rules jsonb NOT NULL,
    approvers jsonb NOT NULL,
    version integer NOT NULL,
    CONSTRAINT pk_tool_policies PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_tool_policies_org_id ON core.tool_policies (org_id);

-- The second fence: the transaction's own tenant, or the system scope to the
-- system login alone (ADR 0016). The serving logins' grants come from the
-- role's default privileges.

ALTER TABLE core.tool_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.tool_policies FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.tool_policies
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
