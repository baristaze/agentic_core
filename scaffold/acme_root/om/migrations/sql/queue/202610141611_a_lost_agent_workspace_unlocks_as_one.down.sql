-- The unlock before agent_workspace.

ALTER TABLE queue.work_items NO FORCE ROW LEVEL SECURITY;
UPDATE queue.work_items
    SET payload = jsonb_set(payload, '{park,unlock}', '"workspace"')
    WHERE kind = 'WAKE_AGENT_SESSION' AND payload -> 'park' ->> 'unlock' = 'agent_workspace';
ALTER TABLE queue.work_items FORCE ROW LEVEL SECURITY;
