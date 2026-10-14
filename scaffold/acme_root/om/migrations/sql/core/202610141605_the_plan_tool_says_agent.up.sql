-- The tool an agent writes its plan with is write_agent_plan: a session's
-- registry and a tenant's policy name it so. The fence binds the migration
-- login too, so it is lifted for these statements alone.

ALTER TABLE core.agent_sessions NO FORCE ROW LEVEL SECURITY;
UPDATE core.agent_sessions
    SET tools = (
        SELECT jsonb_agg(CASE WHEN tool = '"write_plan"'::jsonb THEN '"write_agent_plan"'::jsonb ELSE tool END ORDER BY i)
        FROM jsonb_array_elements(tools) WITH ORDINALITY AS listed (tool, i)
    )
    WHERE tools ? 'write_plan';
ALTER TABLE core.agent_sessions FORCE ROW LEVEL SECURITY;

ALTER TABLE core.tool_policies NO FORCE ROW LEVEL SECURITY;
UPDATE core.tool_policies
    SET rules = (
        SELECT jsonb_agg(
            CASE WHEN rule ->> 'tool' = 'write_plan' THEN jsonb_set(rule, '{tool}', '"write_agent_plan"') ELSE rule END
            ORDER BY i
        )
        FROM jsonb_array_elements(rules) WITH ORDINALITY AS listed (rule, i)
    )
    WHERE rules @> '[{"tool": "write_plan"}]';
ALTER TABLE core.tool_policies FORCE ROW LEVEL SECURITY;
