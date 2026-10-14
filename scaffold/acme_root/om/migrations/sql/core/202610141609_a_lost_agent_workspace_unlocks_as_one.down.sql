-- The unlock before agent_workspace.

ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
UPDATE core.outbox_rows
    SET payload = jsonb_set(payload, '{park,unlock}', '"workspace"')
    WHERE kind = 'work.WAKE_AGENT_SESSION' AND payload -> 'park' ->> 'unlock' = 'agent_workspace';
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;

ALTER TABLE core.agent_sessions NO FORCE ROW LEVEL SECURITY;
UPDATE core.agent_sessions
    SET park = jsonb_set(park, '{unlock}', '"workspace"')
    WHERE park ->> 'unlock' = 'agent_workspace';
ALTER TABLE core.agent_sessions FORCE ROW LEVEL SECURITY;
