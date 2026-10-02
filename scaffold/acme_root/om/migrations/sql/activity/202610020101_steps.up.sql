-- The history of every session, and one cursor row per session.
--
-- An append takes the next seqs as `head + n` under the cursor row's lock,
-- in its own transaction, so a session's history is gapless. The same row
-- holds the writer epoch: a run moves it one up when it begins, and every
-- append it makes changes the row only while the row still holds that
-- epoch (ADR 1002).

CREATE TABLE activity.steps (
    id uuid NOT NULL,
    org_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    session_id uuid NOT NULL,
    seq bigint NOT NULL,
    loop_id uuid NOT NULL,
    type text NOT NULL,
    actor text NOT NULL,
    origin text NOT NULL,
    responds_to uuid,
    refs jsonb NOT NULL,
    header jsonb NOT NULL,
    content jsonb NOT NULL,
    children jsonb NOT NULL,
    CONSTRAINT pk_steps PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_steps_org_id_session_id_seq ON activity.steps (org_id, session_id, seq);

CREATE TABLE activity.step_cursors (
    org_id uuid NOT NULL,
    session_id uuid NOT NULL,
    head bigint NOT NULL,
    epoch bigint NOT NULL,
    CONSTRAINT pk_step_cursors PRIMARY KEY (org_id, session_id)
);

-- The second fence: the transaction's own tenant, or the system scope to the
-- system login alone (ADR 0016).

ALTER TABLE activity.steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.steps FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.steps
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

ALTER TABLE activity.step_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity.step_cursors FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_fence ON activity.step_cursors
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

-- A step is written once. The role's default privileges grant the serving
-- logins every DML statement on a new table; on the history they keep
-- SELECT and INSERT, so no statement a process sends rewrites a step or
-- takes one out of the middle of a history.

REVOKE UPDATE, DELETE ON activity.steps FROM acme_runtime, acme_system;
