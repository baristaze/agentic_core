-- The work that wakes a session says agent session, as the session itself
-- does. An item queued under the old kind moves in place; the fence binds
-- the migration login too, so it is lifted for this statement alone.

ALTER TABLE queue.work_items NO FORCE ROW LEVEL SECURITY;
UPDATE queue.work_items SET kind = CASE kind
        WHEN 'WAKE_SESSION' THEN 'WAKE_AGENT_SESSION'
        WHEN 'WAKE_SESSIONS' THEN 'WAKE_AGENT_SESSIONS'
    END
    WHERE kind IN ('WAKE_SESSION', 'WAKE_SESSIONS');
ALTER TABLE queue.work_items FORCE ROW LEVEL SECURITY;
