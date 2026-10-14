-- The tool an agent writes its plan with is write_agent_plan: a tool
-- request, a person's decision on one, and a toolset's load name it so. A
-- step's content is sealed at rest, so the name a model's reply used stays
-- as written. The fence binds the migration login too, so it is lifted for
-- these statements alone.

ALTER TABLE activity.agent_steps NO FORCE ROW LEVEL SECURITY;
UPDATE activity.agent_steps
    SET header = jsonb_set(header, '{tool}', '"write_agent_plan"')
    WHERE header ->> 'kind' = 'tool_request' AND header ->> 'tool' = 'write_plan';
UPDATE activity.agent_steps
    SET header = jsonb_set(header, '{call,tool}', '"write_agent_plan"')
    WHERE header ->> 'kind' = 'control' AND header -> 'call' ->> 'tool' = 'write_plan';
UPDATE activity.agent_steps
    SET header = jsonb_set(
        header,
        '{toolset,tools}',
        (
            SELECT jsonb_agg(CASE WHEN tool = '"write_plan"'::jsonb THEN '"write_agent_plan"'::jsonb ELSE tool END ORDER BY i)
            FROM jsonb_array_elements(header -> 'toolset' -> 'tools') WITH ORDINALITY AS listed (tool, i)
        )
    )
    WHERE header ->> 'kind' = 'switched' AND header -> 'toolset' -> 'tools' ? 'write_plan';
ALTER TABLE activity.agent_steps FORCE ROW LEVEL SECURITY;
