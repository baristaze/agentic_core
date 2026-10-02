-- Takes the authorities and the trees back out, then the session's kind,
-- lineage, and cached attribution.

REVOKE SELECT, DELETE ON core.agent_trees FROM acme_purge;
REVOKE SELECT, DELETE ON core.session_authorities FROM acme_purge;
DROP POLICY tenant_fence ON core.session_authorities;
DROP TABLE core.session_authorities;
DROP POLICY tenant_fence ON core.agent_trees;
DROP TABLE core.agent_trees;
DROP INDEX core.ix_agent_sessions_org_id_root_id;
DROP INDEX core.ix_agent_sessions_org_id_parent_id_id;
ALTER TABLE core.agent_sessions
    DROP COLUMN kind,
    DROP COLUMN kind_version,
    DROP COLUMN tools,
    DROP COLUMN depth,
    DROP COLUMN handed_off_from,
    DROP COLUMN speaker,
    DROP COLUMN untrusted;
