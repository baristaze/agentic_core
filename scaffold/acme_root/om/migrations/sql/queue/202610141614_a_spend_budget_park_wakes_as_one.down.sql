-- The reason and the key before spend budget.

ALTER TABLE queue.work_items NO FORCE ROW LEVEL SECURITY;
UPDATE queue.work_items
    SET payload = jsonb_set(payload, '{reason}', '"budget"')
    WHERE kind = 'WAKE_AGENT_SESSIONS' AND payload ->> 'reason' = 'spend_budget';
UPDATE queue.work_items
    SET payload = jsonb_set(
        payload #- '{park,job,spend_hold_id}', '{park,job,hold_id}', payload -> 'park' -> 'job' -> 'spend_hold_id'
    )
    WHERE kind = 'WAKE_AGENT_SESSION' AND payload -> 'park' -> 'job' ? 'spend_hold_id';
UPDATE queue.work_items
    SET payload = jsonb_set(payload, '{park,reason}', '"budget"')
    WHERE kind = 'WAKE_AGENT_SESSION' AND payload -> 'park' ->> 'reason' = 'spend_budget';
ALTER TABLE queue.work_items FORCE ROW LEVEL SECURITY;
