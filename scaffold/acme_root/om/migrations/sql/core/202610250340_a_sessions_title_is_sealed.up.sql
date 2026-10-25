-- A session's title is content, sealed under a key of its own (ADR 1042).
-- Its column stays as the release before reads it: a row that release
-- wrote holds its title there in the clear, until the sweep seals it in
-- place, and a row this one writes holds a word, never the title, so
-- either release reads every row. An existing row reads as it did: its
-- sealed title is NULL, so the partial index holds it for the sweep.
-- Each session's title key is kept on its privacy record, wrapped under
-- its tenant's title key, which the key service wraps; the revocation
-- clears it. A record the title writes holds the default policy, not yet
-- chosen; every record written before was chosen, as is every one the
-- release before writes. Nothing else changes.

ALTER TABLE core.agent_sessions ADD COLUMN title_sealed bytea NULL;
CREATE INDEX ix_agent_sessions_title_clear ON core.agent_sessions (id) WHERE (title_sealed IS NULL);

ALTER TABLE core.agent_session_privacy ADD COLUMN chosen boolean DEFAULT true NOT NULL;
ALTER TABLE core.agent_session_privacy ADD COLUMN title_key bytea NULL;

CREATE TABLE core.tenant_title_keys (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    wrapped bytea NOT NULL,
    wrapping text NOT NULL,
    wrapped_at timestamptz NOT NULL,
    CONSTRAINT pk_tenant_title_keys PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_tenant_title_keys_org_id ON core.tenant_title_keys (org_id);
ALTER TABLE core.tenant_title_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.tenant_title_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.tenant_title_keys
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
