-- The names and values before spend budget, and the rows kept.

ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
UPDATE core.outbox_rows SET kind = CASE kind
        WHEN 'spend_budgets.spend_budget.created' THEN 'budgets.budget.created'
        WHEN 'spend_budgets.spend_budget.updated' THEN 'budgets.budget.updated'
    END
    WHERE kind IN ('spend_budgets.spend_budget.created', 'spend_budgets.spend_budget.updated');
UPDATE core.outbox_rows
    SET payload = jsonb_set(payload, '{reason}', '"budget"')
    WHERE kind = 'work.WAKE_AGENT_SESSIONS' AND payload ->> 'reason' = 'spend_budget';
UPDATE core.outbox_rows
    SET payload = jsonb_set(
        payload #- '{park,job,spend_hold_id}', '{park,job,hold_id}', payload -> 'park' -> 'job' -> 'spend_hold_id'
    )
    WHERE kind = 'work.WAKE_AGENT_SESSION' AND payload -> 'park' -> 'job' ? 'spend_hold_id';
UPDATE core.outbox_rows
    SET payload = jsonb_set(payload, '{park,reason}', '"budget"')
    WHERE kind = 'work.WAKE_AGENT_SESSION' AND payload -> 'park' ->> 'reason' = 'spend_budget';
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;

ALTER TABLE core.agent_sessions NO FORCE ROW LEVEL SECURITY;
UPDATE core.agent_sessions
    SET park = jsonb_set(park #- '{job,spend_hold_id}', '{job,hold_id}', park -> 'job' -> 'spend_hold_id')
    WHERE park -> 'job' ? 'spend_hold_id';
UPDATE core.agent_sessions
    SET park = jsonb_set(park, '{reason}', '"budget"')
    WHERE park ->> 'reason' = 'spend_budget';
ALTER TABLE core.agent_sessions FORCE ROW LEVEL SECURITY;

ALTER TABLE core.spend_budgets NO FORCE ROW LEVEL SECURITY;
UPDATE core.spend_budgets SET scope_kind = CASE scope_kind
        WHEN 'agent_session' THEN 'session'
        WHEN 'agent_project' THEN 'project'
    END
    WHERE scope_kind IN ('agent_session', 'agent_project');
ALTER TABLE core.spend_budgets FORCE ROW LEVEL SECURITY;

ALTER INDEX core.ix_spend_budgets_org_id_scope_kind_scope_key
    RENAME TO ix_budgets_org_id_scope_kind_scope_key;
ALTER INDEX core.ix_spend_budgets_org_id_id RENAME TO ix_budgets_org_id_id;
ALTER TABLE core.spend_budgets RENAME CONSTRAINT pk_spend_budgets TO pk_budgets;
ALTER TABLE core.spend_budgets RENAME TO budgets;
