-- The names before tool output, and the rows kept.

ALTER TABLE activity.agent_steps NO FORCE ROW LEVEL SECURITY;
UPDATE activity.agent_steps
    SET header = jsonb_set(header, '{tool}', '"read_artifact"')
    WHERE header ->> 'kind' = 'tool_request' AND header ->> 'tool' = 'read_tool_output';
UPDATE activity.agent_steps
    SET header = jsonb_set(header, '{call,tool}', '"read_artifact"')
    WHERE header ->> 'kind' = 'control' AND header -> 'call' ->> 'tool' = 'read_tool_output';
UPDATE activity.agent_steps
    SET header = jsonb_set(
        header,
        '{toolset,tools}',
        (
            SELECT jsonb_agg(CASE WHEN tool = '"read_tool_output"'::jsonb THEN '"read_artifact"'::jsonb ELSE tool END ORDER BY i)
            FROM jsonb_array_elements(header -> 'toolset' -> 'tools') WITH ORDINALITY AS listed (tool, i)
        )
    )
    WHERE header ->> 'kind' = 'switched' AND header -> 'toolset' -> 'tools' ? 'read_tool_output';
ALTER TABLE activity.agent_steps FORCE ROW LEVEL SECURITY;

ALTER TABLE activity.agent_steps NO FORCE ROW LEVEL SECURITY;
UPDATE activity.agent_steps
    SET header = (header - 'tool_output') || jsonb_build_object('artifact', header -> 'tool_output')
    WHERE header ? 'tool_output';
ALTER TABLE activity.agent_steps FORCE ROW LEVEL SECURITY;

ALTER INDEX activity.ix_tool_outputs_org_id_agent_session_id
    RENAME TO ix_artifacts_org_id_agent_session_id;
ALTER TABLE activity.tool_outputs RENAME CONSTRAINT pk_tool_outputs TO pk_artifacts;
ALTER TABLE activity.tool_outputs RENAME TO artifacts;
