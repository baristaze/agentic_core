-- Every column that names a session says agent session, as the session
-- itself does: a copy's own sessions take the bare word. A rename keeps
-- the rows.

ALTER TABLE activity.steps RENAME COLUMN session_id TO agent_session_id;
ALTER INDEX activity.uq_steps_org_id_session_id_seq RENAME TO uq_steps_org_id_agent_session_id_seq;
ALTER TABLE activity.step_cursors RENAME COLUMN session_id TO agent_session_id;
ALTER TABLE activity.artifacts RENAME COLUMN session_id TO agent_session_id;
ALTER INDEX activity.ix_artifacts_org_id_session_id RENAME TO ix_artifacts_org_id_agent_session_id;
ALTER TABLE activity.budget_holds RENAME COLUMN session_id TO agent_session_id;
ALTER TABLE activity.usage_records RENAME COLUMN session_id TO agent_session_id;
ALTER INDEX activity.ix_usage_records_org_id_session_id_id
    RENAME TO ix_usage_records_org_id_agent_session_id_id;

-- The agent a step names, on an input from another agent and on every tool
-- request, keeps its session's id under the new key. The fence binds the
-- migration login too, so it is lifted for this statement alone.
ALTER TABLE activity.steps NO FORCE ROW LEVEL SECURITY;
UPDATE activity.steps
    SET header = jsonb_set(
        header #- '{agent,session_id}', '{agent,agent_session_id}', header -> 'agent' -> 'session_id'
    )
    WHERE header -> 'agent' ? 'session_id';
ALTER TABLE activity.steps FORCE ROW LEVEL SECURITY;
