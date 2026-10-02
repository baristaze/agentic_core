-- Every version of every session's fill set: the model roles it resolves to
-- fills, one row a version. A switch writes the next version; nothing
-- rewrites one, so a fill changes only by a new version, announced by the
-- `switched` step its row names.

CREATE TABLE core.fill_sets (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    session_id uuid NOT NULL,
    version integer NOT NULL,
    roles jsonb NOT NULL,
    eligibility jsonb NOT NULL,
    reason text,
    switched_by uuid,
    CONSTRAINT pk_fill_sets PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_fill_sets_org_id_session_id_version ON core.fill_sets (org_id, session_id, version);

-- The second fence: the transaction's own tenant, or the system scope to the
-- system login alone (ADR 0016).

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

-- A version is written once. The role's default privileges grant the
-- serving logins every DML statement on a new table; here they keep SELECT
-- and INSERT, so no statement a process sends rewrites a version.

REVOKE UPDATE, DELETE ON core.fill_sets FROM acme_runtime, acme_system;
