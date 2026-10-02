-- Each session's privacy record and the versions of its key.
--
-- A record is written once, with the session's storage policy, and changes
-- only when its key is revoked. A version holds its data key wrapped by the
-- tenant's key service, never in the clear; revoking the key empties every
-- version's wrapped copy and keeps the row (ADR 1004).

CREATE TABLE core.session_privacy (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    policy jsonb NOT NULL,
    revoked_at timestamptz,
    revoked_by uuid,
    CONSTRAINT pk_session_privacy PRIMARY KEY (id)
);
CREATE INDEX ix_session_privacy_org_id ON core.session_privacy (org_id);

CREATE TABLE core.session_keys (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    session_id uuid NOT NULL,
    version integer NOT NULL,
    wrapped bytea,
    wrapping text,
    wrapped_at timestamptz,
    destroyed_at timestamptz,
    CONSTRAINT pk_session_keys PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_session_keys_org_id_session_id_version
    ON core.session_keys (org_id, session_id, version);

-- The second fence: the transaction's own tenant, or the system scope to the
-- system login alone (ADR 0016). The serving logins' grants come from the
-- role's default privileges.

ALTER TABLE core.session_privacy ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.session_privacy FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.session_privacy
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

ALTER TABLE core.session_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.session_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON core.session_keys
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
