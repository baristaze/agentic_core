-- A queued wake of a session parked on its spend budget names the
-- spend_budget reason, and a park on a job its spend hold. The fence binds
-- the migration login too, so it is lifted for these statements alone.

ALTER TABLE queue.work_items NO FORCE ROW LEVEL SECURITY;
UPDATE queue.work_items
    SET payload = jsonb_set(payload, '{park,reason}', '"spend_budget"')
    WHERE kind = 'WAKE_AGENT_SESSION' AND payload -> 'park' ->> 'reason' = 'budget';
UPDATE queue.work_items
    SET payload = jsonb_set(
        payload #- '{park,job,hold_id}', '{park,job,spend_hold_id}', payload -> 'park' -> 'job' -> 'hold_id'
    )
    WHERE kind = 'WAKE_AGENT_SESSION' AND payload -> 'park' -> 'job' ? 'hold_id';
UPDATE queue.work_items
    SET payload = jsonb_set(payload, '{reason}', '"spend_budget"')
    WHERE kind = 'WAKE_AGENT_SESSIONS' AND payload ->> 'reason' = 'budget';
ALTER TABLE queue.work_items FORCE ROW LEVEL SECURITY;
