-- A session pins its agent kind and holds the tools it may call, its depth
-- in its tree, the session that handed it over, and the speaker and the
-- mark it caches from its steps. No session row is written before this
-- change, so each column arrives as the ORM holds it, with no default to
-- fill old rows: a table that held one would refuse the change and keep
-- everything it had.

ALTER TABLE core.agent_sessions
    ADD COLUMN kind text NOT NULL,
    ADD COLUMN kind_version integer NOT NULL,
    ADD COLUMN tools jsonb NOT NULL,
    ADD COLUMN depth integer NOT NULL,
    ADD COLUMN handed_off_from uuid,
    ADD COLUMN speaker jsonb,
    ADD COLUMN untrusted boolean NOT NULL;
CREATE INDEX ix_agent_sessions_org_id_parent_id_id ON core.agent_sessions (org_id, parent_id, id);

-- A tree: a root session and its sub-agents, one row keyed by the root's
-- id, holding what the whole tree shares. A spawn takes a slot in one
-- conditional statement, so two spawns at once never pass the count.

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

-- A session's authority: the mode its tool calls run under, the principal
-- they run under, and the spender a spawn passed it, one row keyed by the
-- session's id.

CREATE TABLE core.session_authorities (
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
    CONSTRAINT pk_session_authorities PRIMARY KEY (id)
);
CREATE INDEX ix_session_authorities_org_id ON core.session_authorities (org_id);

-- The second fence on both: the transaction's own tenant, or the system
-- scope to the system login alone (ADR 0016). The serving logins' grants
-- come from the role's default privileges.

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

ALTER TABLE core.session_authorities ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.session_authorities FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.session_authorities
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
