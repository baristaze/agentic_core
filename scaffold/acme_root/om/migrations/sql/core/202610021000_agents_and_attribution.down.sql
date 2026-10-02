-- Takes the authorities and the trees back out, then the session's kind,
-- lineage, and cached attribution.

DROP POLICY tenant_fence ON core.session_authorities;
DROP TABLE core.session_authorities;
DROP POLICY tenant_fence ON core.agent_trees;
DROP TABLE core.agent_trees;
DROP INDEX core.ix_agent_sessions_org_id_parent_id_id;
ALTER TABLE core.agent_sessions
    DROP COLUMN kind,
    DROP COLUMN kind_version,
    DROP COLUMN tools,
    DROP COLUMN depth,
    DROP COLUMN handed_off_from,
    DROP COLUMN speaker,
    DROP COLUMN untrusted;
