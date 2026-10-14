-- The columns and the key before agent session, and the rows kept.

ALTER TABLE activity.steps NO FORCE ROW LEVEL SECURITY;
UPDATE activity.steps
    SET header = jsonb_set(
        header #- '{agent,agent_session_id}', '{agent,session_id}', header -> 'agent' -> 'agent_session_id'
    )
    WHERE header -> 'agent' ? 'agent_session_id';
ALTER TABLE activity.steps FORCE ROW LEVEL SECURITY;

ALTER INDEX activity.ix_usage_records_org_id_agent_session_id_id
    RENAME TO ix_usage_records_org_id_session_id_id;
ALTER TABLE activity.usage_records RENAME COLUMN agent_session_id TO session_id;
ALTER TABLE activity.budget_holds RENAME COLUMN agent_session_id TO session_id;
ALTER INDEX activity.ix_artifacts_org_id_agent_session_id RENAME TO ix_artifacts_org_id_session_id;
ALTER TABLE activity.artifacts RENAME COLUMN agent_session_id TO session_id;
ALTER TABLE activity.step_cursors RENAME COLUMN agent_session_id TO session_id;
ALTER INDEX activity.uq_steps_org_id_agent_session_id_seq RENAME TO uq_steps_org_id_session_id_seq;
ALTER TABLE activity.steps RENAME COLUMN agent_session_id TO session_id;
