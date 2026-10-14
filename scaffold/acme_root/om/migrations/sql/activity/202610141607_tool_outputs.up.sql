-- A result kept whole outside its step is a tool output: a copy's own
-- artifacts, of a build or a model, take that word. A rename keeps the rows,
-- and their grants, fences, and policies go with the table.

ALTER TABLE activity.artifacts RENAME TO tool_outputs;
ALTER TABLE activity.tool_outputs RENAME CONSTRAINT pk_artifacts TO pk_tool_outputs;
ALTER INDEX activity.ix_artifacts_org_id_agent_session_id
    RENAME TO ix_tool_outputs_org_id_agent_session_id;

-- An input's or a tool response's header names its kept result under the
-- new key, and a tool request, a decision on one, and a toolset's load name
-- the tool that reads it by its new name. A step's content is sealed at
-- rest, so the name a model's reply used stays as written. The fence binds
-- the migration login too, so it is lifted for these statements alone.
ALTER TABLE activity.agent_steps NO FORCE ROW LEVEL SECURITY;
UPDATE activity.agent_steps
    SET header = (header - 'artifact') || jsonb_build_object('tool_output', header -> 'artifact')
    WHERE header ? 'artifact';
ALTER TABLE activity.agent_steps FORCE ROW LEVEL SECURITY;

ALTER TABLE activity.agent_steps NO FORCE ROW LEVEL SECURITY;
UPDATE activity.agent_steps
    SET header = jsonb_set(header, '{tool}', '"read_tool_output"')
    WHERE header ->> 'kind' = 'tool_request' AND header ->> 'tool' = 'read_artifact';
UPDATE activity.agent_steps
    SET header = jsonb_set(header, '{call,tool}', '"read_tool_output"')
    WHERE header ->> 'kind' = 'control' AND header -> 'call' ->> 'tool' = 'read_artifact';
UPDATE activity.agent_steps
    SET header = jsonb_set(
        header,
        '{toolset,tools}',
        (
            SELECT jsonb_agg(CASE WHEN tool = '"read_artifact"'::jsonb THEN '"read_tool_output"'::jsonb ELSE tool END ORDER BY i)
            FROM jsonb_array_elements(header -> 'toolset' -> 'tools') WITH ORDINALITY AS listed (tool, i)
        )
    )
    WHERE header ->> 'kind' = 'switched' AND header -> 'toolset' -> 'tools' ? 'read_artifact';
ALTER TABLE activity.agent_steps FORCE ROW LEVEL SECURITY;
