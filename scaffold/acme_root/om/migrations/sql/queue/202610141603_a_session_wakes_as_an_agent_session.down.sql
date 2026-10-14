-- The kinds before agent session, and the items kept.

ALTER TABLE queue.work_items NO FORCE ROW LEVEL SECURITY;
UPDATE queue.work_items SET kind = CASE kind
        WHEN 'WAKE_AGENT_SESSION' THEN 'WAKE_SESSION'
        WHEN 'WAKE_AGENT_SESSIONS' THEN 'WAKE_SESSIONS'
    END
    WHERE kind IN ('WAKE_AGENT_SESSION', 'WAKE_AGENT_SESSIONS');
ALTER TABLE queue.work_items FORCE ROW LEVEL SECURITY;
