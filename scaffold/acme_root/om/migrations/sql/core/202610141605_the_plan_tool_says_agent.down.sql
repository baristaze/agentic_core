-- The tool name before write_agent_plan.

ALTER TABLE core.agent_sessions NO FORCE ROW LEVEL SECURITY;
UPDATE core.agent_sessions
    SET tools = (
        SELECT jsonb_agg(CASE WHEN tool = '"write_agent_plan"'::jsonb THEN '"write_plan"'::jsonb ELSE tool END ORDER BY i)
        FROM jsonb_array_elements(tools) WITH ORDINALITY AS listed (tool, i)
    )
    WHERE tools ? 'write_agent_plan';
ALTER TABLE core.agent_sessions FORCE ROW LEVEL SECURITY;

ALTER TABLE core.tool_policies NO FORCE ROW LEVEL SECURITY;
UPDATE core.tool_policies
    SET rules = (
        SELECT jsonb_agg(
            CASE WHEN rule ->> 'tool' = 'write_agent_plan' THEN jsonb_set(rule, '{tool}', '"write_plan"') ELSE rule END
            ORDER BY i
        )
        FROM jsonb_array_elements(rules) WITH ORDINALITY AS listed (rule, i)
    )
    WHERE rules @> '[{"tool": "write_agent_plan"}]';
ALTER TABLE core.tool_policies FORCE ROW LEVEL SECURITY;
