-- A session marked deleted is hidden and can be unmarked; past its
-- retention, the sweep claims it for its purge, which makes the delete
-- final, and the purge login removes its history and then its row
-- (ADR 1010).

ALTER TABLE core.agent_sessions ADD COLUMN deleted_at timestamptz;
ALTER TABLE core.agent_sessions ADD COLUMN deleted_by uuid;
ALTER TABLE core.agent_sessions ADD COLUMN purge_started_at timestamptz;

-- The sweep's read across tenants: the deleted sessions by their delete.
CREATE INDEX ix_agent_sessions_deleted_at ON core.agent_sessions (deleted_at)
    WHERE deleted_at IS NOT NULL;

-- The purge login reads and deletes a session, and nothing else of the
-- role. The tenant fence admits it within the tenant its transaction names;
-- its system-scope clause names the system login alone, so the purge login
-- reaches no row under the system scope.

GRANT USAGE ON SCHEMA core TO acme_purge;
GRANT SELECT, DELETE ON core.agent_sessions TO acme_purge;
