-- A session's steps and their cursors say agent step, and so does every
-- column that names one: a copy's own steps, of a workflow or a procedure,
-- take the bare word. A rename keeps the rows, and their grants, fences,
-- and policies go with the table.

ALTER TABLE activity.steps RENAME TO agent_steps;
ALTER TABLE activity.agent_steps RENAME CONSTRAINT pk_steps TO pk_agent_steps;
ALTER INDEX activity.uq_steps_org_id_agent_session_id_seq
    RENAME TO uq_agent_steps_org_id_agent_session_id_seq;

ALTER TABLE activity.step_cursors RENAME TO agent_step_cursors;
ALTER TABLE activity.agent_step_cursors RENAME CONSTRAINT pk_step_cursors TO pk_agent_step_cursors;

ALTER TABLE activity.artifacts RENAME COLUMN step_id TO agent_step_id;
ALTER TABLE activity.usage_records RENAME COLUMN step_id TO agent_step_id;
