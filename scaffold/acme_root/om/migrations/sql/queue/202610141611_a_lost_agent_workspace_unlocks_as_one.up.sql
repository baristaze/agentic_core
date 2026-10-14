-- A queued wake of a session parked on its agent workspace names the
-- agent_workspace unlock. The fence binds the migration login too, so it
-- is lifted for this statement alone.

ALTER TABLE queue.work_items NO FORCE ROW LEVEL SECURITY;
UPDATE queue.work_items
    SET payload = jsonb_set(payload, '{park,unlock}', '"agent_workspace"')
    WHERE kind = 'WAKE_AGENT_SESSION' AND payload -> 'park' ->> 'unlock' = 'workspace';
ALTER TABLE queue.work_items FORCE ROW LEVEL SECURITY;
