-- Takes the trees back out, then the session's kind, authority, lineage,
-- and attribution.

DROP POLICY tenant_fence ON core.agent_trees;
DROP TABLE core.agent_trees;
DROP INDEX core.ix_agent_sessions_org_id_parent_id_id;
ALTER TABLE core.agent_sessions
    DROP COLUMN kind,
    DROP COLUMN kind_version,
    DROP COLUMN authority,
    DROP COLUMN tools,
    DROP COLUMN depth,
    DROP COLUMN handed_off_from,
    DROP COLUMN spender,
    DROP COLUMN speaker,
    DROP COLUMN untrusted;
