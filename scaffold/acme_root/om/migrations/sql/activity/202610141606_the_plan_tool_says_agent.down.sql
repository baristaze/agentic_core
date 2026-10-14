-- The tool name before write_agent_plan.

ALTER TABLE activity.agent_steps NO FORCE ROW LEVEL SECURITY;
UPDATE activity.agent_steps
    SET header = jsonb_set(header, '{tool}', '"write_plan"')
    WHERE header ->> 'kind' = 'tool_request' AND header ->> 'tool' = 'write_agent_plan';
UPDATE activity.agent_steps
    SET header = jsonb_set(header, '{call,tool}', '"write_plan"')
    WHERE header ->> 'kind' = 'control' AND header -> 'call' ->> 'tool' = 'write_agent_plan';
UPDATE activity.agent_steps
    SET header = jsonb_set(
        header,
        '{toolset,tools}',
        (
            SELECT jsonb_agg(CASE WHEN tool = '"write_agent_plan"'::jsonb THEN '"write_plan"'::jsonb ELSE tool END ORDER BY i)
            FROM jsonb_array_elements(header -> 'toolset' -> 'tools') WITH ORDINALITY AS listed (tool, i)
        )
    )
    WHERE header ->> 'kind' = 'switched' AND header -> 'toolset' -> 'tools' ? 'write_agent_plan';
ALTER TABLE activity.agent_steps FORCE ROW LEVEL SECURITY;
