-- The engine's own tables of the core role go, grants first, and the role
-- is the base's.

REVOKE SELECT, DELETE ON core.agent_trees FROM acme_purge;
REVOKE SELECT, DELETE ON core.agent_session_authorities FROM acme_purge;
REVOKE SELECT, DELETE ON core.agent_sessions FROM acme_purge;
REVOKE USAGE ON SCHEMA core FROM acme_purge;
DROP TABLE core.tool_policies;
DROP TABLE core.spend_budgets;
DROP TABLE core.fill_sets;
DROP TABLE core.agent_trees;
DROP TABLE core.agent_sessions;
DROP TABLE core.agent_session_privacy;
DROP TABLE core.agent_session_keys;
DROP TABLE core.agent_session_authorities;
