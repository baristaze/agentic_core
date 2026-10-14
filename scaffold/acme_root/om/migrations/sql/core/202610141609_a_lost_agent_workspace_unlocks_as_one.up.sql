-- A park on an agent's workspace, in line for one or lost with its
-- snapshot, is cleared by the agent_workspace unlock: a session parked so,
-- and a wake of it not yet relayed, move in place. The fence binds the
-- migration login too, so it is lifted for these statements alone.

ALTER TABLE core.agent_sessions NO FORCE ROW LEVEL SECURITY;
UPDATE core.agent_sessions
    SET park = jsonb_set(park, '{unlock}', '"agent_workspace"')
    WHERE park ->> 'unlock' = 'workspace';
ALTER TABLE core.agent_sessions FORCE ROW LEVEL SECURITY;

ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
UPDATE core.outbox_rows
    SET payload = jsonb_set(payload, '{park,unlock}', '"agent_workspace"')
    WHERE kind = 'work.WAKE_AGENT_SESSION' AND payload -> 'park' ->> 'unlock' = 'workspace';
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;
