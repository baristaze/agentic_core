-- The record of every artifact: a tool result too large for a step, kept
-- whole in the object store. A row is written once, beside the history it
-- belongs to, and read by its id.

CREATE TABLE activity.artifacts (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    session_id uuid NOT NULL,
    step_id uuid NOT NULL,
    characters bigint NOT NULL,
    CONSTRAINT pk_artifacts PRIMARY KEY (id)
);
CREATE INDEX ix_artifacts_org_id ON activity.artifacts (org_id);

-- The second fence: the transaction's own tenant, or the system scope to the
-- system login alone (ADR 0016).

ALTER TABLE activity.artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.artifacts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.artifacts
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

-- An artifact is written once, like the steps it belongs to: the serving
-- logins keep SELECT and INSERT (ADR 1002).

REVOKE UPDATE, DELETE ON activity.artifacts FROM acme_runtime, acme_system;
