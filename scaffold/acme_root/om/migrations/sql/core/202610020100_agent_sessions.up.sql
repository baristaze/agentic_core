-- The agent sessions: each a series of loops over one history, with the
-- status it caches from its steps. The steps and their cursor are the
-- activity role's; a session is written with its outbox rows, so nothing
-- crosses a role.

CREATE TABLE core.agent_sessions (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    title text NOT NULL,
    participants jsonb NOT NULL,
    parent_id uuid,
    root_id uuid NOT NULL,
    status text NOT NULL,
    park jsonb,
    status_seq bigint NOT NULL,
    archived_at timestamptz,
    version integer NOT NULL,
    CONSTRAINT pk_agent_sessions PRIMARY KEY (id)
);
CREATE INDEX ix_agent_sessions_org_id_id ON core.agent_sessions (org_id, id);
CREATE INDEX ix_agent_sessions_org_id_status_id ON core.agent_sessions (org_id, status, id);

-- The second fence: the transaction's own tenant, or the system scope to the
-- system login alone (ADR 0016). The serving logins' grants come from the
-- role's default privileges.

ALTER TABLE core.agent_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.agent_sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.agent_sessions
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
