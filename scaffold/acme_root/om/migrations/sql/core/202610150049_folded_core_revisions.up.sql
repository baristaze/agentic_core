-- The engine's own tables of the core role, on the base's: the agent
-- sessions with the status each caches from its steps, their trees and
-- the authority each runs under, who sees each and the keys its content is
-- sealed under, their fill sets, the spend budgets, and the tool policies.
-- Each is fenced to its tenant, or the system scope to the system login
-- alone (ADR 0016). The serving logins' grants come from the role's default
-- privileges; the purge login reaches a session, its authority, and its
-- tree, within the tenant its transaction names (ADR 1010).

CREATE TABLE core.agent_session_authorities (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    mode text NOT NULL,
    principal jsonb NOT NULL,
    spender jsonb,
    version integer NOT NULL,
    CONSTRAINT pk_agent_session_authorities PRIMARY KEY (id)
);
CREATE INDEX ix_agent_session_authorities_org_id ON core.agent_session_authorities (org_id);
ALTER TABLE core.agent_session_authorities ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.agent_session_authorities FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.agent_session_authorities
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

CREATE TABLE core.agent_session_keys (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    agent_session_id uuid NOT NULL,
    version integer NOT NULL,
    wrapped bytea,
    wrapping text,
    wrapped_at timestamptz,
    destroyed_at timestamptz,
    CONSTRAINT pk_agent_session_keys PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_agent_session_keys_org_id_agent_session_id_version ON core.agent_session_keys (org_id, agent_session_id, version);
ALTER TABLE core.agent_session_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.agent_session_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.agent_session_keys
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

CREATE TABLE core.agent_session_privacy (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    agent_session_id uuid NOT NULL,
    policy jsonb NOT NULL,
    revoked_at timestamptz,
    revoked_by uuid,
    CONSTRAINT pk_agent_session_privacy PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_agent_session_privacy_org_id_agent_session_id ON core.agent_session_privacy (org_id, agent_session_id);
ALTER TABLE core.agent_session_privacy ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.agent_session_privacy FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.agent_session_privacy
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
    pending_input uuid,
    delivering_request uuid,
    archived_at timestamptz,
    version integer NOT NULL,
    deleted_at timestamptz,
    deleted_by uuid,
    purge_started_at timestamptz,
    kind text NOT NULL,
    kind_version integer NOT NULL,
    tools jsonb NOT NULL,
    depth integer NOT NULL,
    handed_off_from uuid,
    speaker jsonb,
    untrusted boolean NOT NULL,
    holds_private boolean DEFAULT true NOT NULL,
    visibility text DEFAULT 'private'::text NOT NULL,
    steerer uuid,
    CONSTRAINT pk_agent_sessions PRIMARY KEY (id)
);
CREATE INDEX ix_agent_sessions_deleted_at ON core.agent_sessions (deleted_at) WHERE (deleted_at IS NOT NULL);
CREATE INDEX ix_agent_sessions_org_id_id ON core.agent_sessions (org_id, id);
CREATE INDEX ix_agent_sessions_org_id_parent_id_id ON core.agent_sessions (org_id, parent_id, id);
CREATE INDEX ix_agent_sessions_org_id_root_id ON core.agent_sessions (org_id, root_id);
CREATE INDEX ix_agent_sessions_org_id_status_id ON core.agent_sessions (org_id, status, id);
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

CREATE TABLE core.agent_trees (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    height integer NOT NULL,
    count integer NOT NULL,
    concurrency integer,
    deadline timestamptz,
    size integer NOT NULL,
    version integer NOT NULL,
    CONSTRAINT pk_agent_trees PRIMARY KEY (id)
);
CREATE INDEX ix_agent_trees_org_id ON core.agent_trees (org_id);
ALTER TABLE core.agent_trees ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.agent_trees FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.agent_trees
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

CREATE TABLE core.fill_sets (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    agent_session_id uuid NOT NULL,
    version integer NOT NULL,
    roles jsonb NOT NULL,
    eligibility jsonb NOT NULL,
    reason text,
    switched_by uuid,
    CONSTRAINT pk_fill_sets PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_fill_sets_org_id_agent_session_id_version ON core.fill_sets (org_id, agent_session_id, version);
ALTER TABLE core.fill_sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.fill_sets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.fill_sets
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

CREATE TABLE core.spend_budgets (
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
    closed_at timestamptz,
    CONSTRAINT pk_spend_budgets PRIMARY KEY (id)
);
CREATE INDEX ix_spend_budgets_org_id_id ON core.spend_budgets (org_id, id);
CREATE INDEX ix_spend_budgets_org_id_scope_kind_scope_key ON core.spend_budgets (org_id, scope_kind, scope_key) WHERE (closed_at IS NULL);
ALTER TABLE core.spend_budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.spend_budgets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.spend_budgets
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

GRANT USAGE ON SCHEMA core TO acme_purge;
GRANT SELECT, DELETE ON core.agent_sessions TO acme_purge;
GRANT SELECT, DELETE ON core.agent_session_authorities TO acme_purge;
GRANT SELECT, DELETE ON core.agent_trees TO acme_purge;
