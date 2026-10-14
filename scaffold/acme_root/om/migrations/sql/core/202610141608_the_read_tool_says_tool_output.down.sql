-- The tool name before read_tool_output.

ALTER TABLE core.agent_sessions NO FORCE ROW LEVEL SECURITY;
UPDATE core.agent_sessions
    SET tools = (
        SELECT jsonb_agg(CASE WHEN tool = '"read_tool_output"'::jsonb THEN '"read_artifact"'::jsonb ELSE tool END ORDER BY i)
        FROM jsonb_array_elements(tools) WITH ORDINALITY AS listed (tool, i)
    )
    WHERE tools ? 'read_tool_output';
ALTER TABLE core.agent_sessions FORCE ROW LEVEL SECURITY;

ALTER TABLE core.tool_policies NO FORCE ROW LEVEL SECURITY;
UPDATE core.tool_policies
    SET rules = (
        SELECT jsonb_agg(
            CASE WHEN rule ->> 'tool' = 'read_tool_output' THEN jsonb_set(rule, '{tool}', '"read_artifact"') ELSE rule END
            ORDER BY i
        )
        FROM jsonb_array_elements(rules) WITH ORDINALITY AS listed (rule, i)
    )
    WHERE rules @> '[{"tool": "read_tool_output"}]';
ALTER TABLE core.tool_policies FORCE ROW LEVEL SECURITY;
