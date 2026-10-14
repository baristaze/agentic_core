-- The tool that reads a kept result is read_tool_output: a session's
-- registry and a tenant's policy name it so. The fence binds the migration
-- login too, so it is lifted for these statements alone.

ALTER TABLE core.agent_sessions NO FORCE ROW LEVEL SECURITY;
UPDATE core.agent_sessions
    SET tools = (
        SELECT jsonb_agg(CASE WHEN tool = '"read_artifact"'::jsonb THEN '"read_tool_output"'::jsonb ELSE tool END ORDER BY i)
        FROM jsonb_array_elements(tools) WITH ORDINALITY AS listed (tool, i)
    )
    WHERE tools ? 'read_artifact';
ALTER TABLE core.agent_sessions FORCE ROW LEVEL SECURITY;

ALTER TABLE core.tool_policies NO FORCE ROW LEVEL SECURITY;
UPDATE core.tool_policies
    SET rules = (
        SELECT jsonb_agg(
            CASE WHEN rule ->> 'tool' = 'read_artifact' THEN jsonb_set(rule, '{tool}', '"read_tool_output"') ELSE rule END
            ORDER BY i
        )
        FROM jsonb_array_elements(rules) WITH ORDINALITY AS listed (rule, i)
    )
    WHERE rules @> '[{"tool": "read_artifact"}]';
ALTER TABLE core.tool_policies FORCE ROW LEVEL SECURITY;
