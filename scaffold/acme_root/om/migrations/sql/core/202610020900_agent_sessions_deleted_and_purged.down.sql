-- Takes the purge login's reach back, then the marks.

REVOKE SELECT, DELETE ON core.agent_sessions FROM acme_purge;
REVOKE USAGE ON SCHEMA core FROM acme_purge;
DROP INDEX core.ix_agent_sessions_deleted_at;
ALTER TABLE core.agent_sessions DROP COLUMN purge_started_at;
ALTER TABLE core.agent_sessions DROP COLUMN deleted_by;
ALTER TABLE core.agent_sessions DROP COLUMN deleted_at;
