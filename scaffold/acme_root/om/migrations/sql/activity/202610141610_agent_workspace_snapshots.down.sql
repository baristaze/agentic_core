-- The key and the unlock before agent workspace.

ALTER TABLE activity.agent_steps NO FORCE ROW LEVEL SECURITY;
UPDATE activity.agent_steps
    SET header = jsonb_set(header, '{park,unlock}', '"workspace"')
    WHERE header ->> 'kind' = 'parked' AND header -> 'park' ->> 'unlock' = 'agent_workspace';
UPDATE activity.agent_steps
    SET header = jsonb_set(
        header #- '{snapshot,agent_workspace_id}',
        '{snapshot,workspace_id}',
        header -> 'snapshot' -> 'agent_workspace_id'
    )
    WHERE header -> 'snapshot' ? 'agent_workspace_id';
ALTER TABLE activity.agent_steps FORCE ROW LEVEL SECURITY;
