-- Takes the sealed titles and their keys back out. A title sealed since
-- is lost with them: its column holds the word that stood for it.

DROP TABLE core.tenant_title_keys;
ALTER TABLE core.agent_session_privacy DROP COLUMN title_key;
ALTER TABLE core.agent_session_privacy DROP COLUMN chosen;
DROP INDEX core.ix_agent_sessions_title_clear;
ALTER TABLE core.agent_sessions DROP COLUMN title_sealed;
