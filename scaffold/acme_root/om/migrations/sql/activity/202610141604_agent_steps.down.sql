-- The names before agent step, and the rows kept.

ALTER TABLE activity.usage_records RENAME COLUMN agent_step_id TO step_id;
ALTER TABLE activity.artifacts RENAME COLUMN agent_step_id TO step_id;

ALTER TABLE activity.agent_step_cursors RENAME CONSTRAINT pk_agent_step_cursors TO pk_step_cursors;
ALTER TABLE activity.agent_step_cursors RENAME TO step_cursors;

ALTER INDEX activity.uq_agent_steps_org_id_agent_session_id_seq
    RENAME TO uq_steps_org_id_agent_session_id_seq;
ALTER TABLE activity.agent_steps RENAME CONSTRAINT pk_agent_steps TO pk_steps;
ALTER TABLE activity.agent_steps RENAME TO steps;
