-- A snapshot a step names, on a restore and on the step that took it,
-- keeps its agent workspace's id under the new key, and a park on an
-- agent's workspace is cleared by the agent_workspace unlock. The fence
-- binds the migration login too, so it is lifted for these statements alone.

ALTER TABLE activity.agent_steps NO FORCE ROW LEVEL SECURITY;
UPDATE activity.agent_steps
    SET header = jsonb_set(
        header #- '{snapshot,workspace_id}',
        '{snapshot,agent_workspace_id}',
        header -> 'snapshot' -> 'workspace_id'
    )
    WHERE header -> 'snapshot' ? 'workspace_id';
UPDATE activity.agent_steps
    SET header = jsonb_set(header, '{park,unlock}', '"agent_workspace"')
    WHERE header ->> 'kind' = 'parked' AND header -> 'park' ->> 'unlock' = 'workspace';
ALTER TABLE activity.agent_steps FORCE ROW LEVEL SECURITY;
