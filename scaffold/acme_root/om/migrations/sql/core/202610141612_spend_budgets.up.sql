-- A budget is a spend budget: a construction, an agency, or a campaign
-- budget is a copy's own. A rename keeps the rows, and their grants,
-- fences, and policies go with the table. The scope a budget caps names an
-- agent session or an agent project, and the park its gate writes says
-- spend_budget; a session parked so, and a wake not yet relayed, move in
-- place. The fence binds the migration login too, so it is lifted for
-- these statements alone.

ALTER TABLE core.budgets RENAME TO spend_budgets;
ALTER TABLE core.spend_budgets RENAME CONSTRAINT pk_budgets TO pk_spend_budgets;
ALTER INDEX core.ix_budgets_org_id_id RENAME TO ix_spend_budgets_org_id_id;
ALTER INDEX core.ix_budgets_org_id_scope_kind_scope_key
    RENAME TO ix_spend_budgets_org_id_scope_kind_scope_key;

ALTER TABLE core.spend_budgets NO FORCE ROW LEVEL SECURITY;
UPDATE core.spend_budgets SET scope_kind = CASE scope_kind
        WHEN 'session' THEN 'agent_session'
        WHEN 'project' THEN 'agent_project'
    END
    WHERE scope_kind IN ('session', 'project');
ALTER TABLE core.spend_budgets FORCE ROW LEVEL SECURITY;

ALTER TABLE core.agent_sessions NO FORCE ROW LEVEL SECURITY;
UPDATE core.agent_sessions
    SET park = jsonb_set(park, '{reason}', '"spend_budget"')
    WHERE park ->> 'reason' = 'budget';
UPDATE core.agent_sessions
    SET park = jsonb_set(park #- '{job,hold_id}', '{job,spend_hold_id}', park -> 'job' -> 'hold_id')
    WHERE park -> 'job' ? 'hold_id';
ALTER TABLE core.agent_sessions FORCE ROW LEVEL SECURITY;

ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
UPDATE core.outbox_rows
    SET payload = jsonb_set(payload, '{park,reason}', '"spend_budget"')
    WHERE kind = 'work.WAKE_AGENT_SESSION' AND payload -> 'park' ->> 'reason' = 'budget';
UPDATE core.outbox_rows
    SET payload = jsonb_set(
        payload #- '{park,job,hold_id}', '{park,job,spend_hold_id}', payload -> 'park' -> 'job' -> 'hold_id'
    )
    WHERE kind = 'work.WAKE_AGENT_SESSION' AND payload -> 'park' -> 'job' ? 'hold_id';
UPDATE core.outbox_rows
    SET payload = jsonb_set(payload, '{reason}', '"spend_budget"')
    WHERE kind = 'work.WAKE_AGENT_SESSIONS' AND payload ->> 'reason' = 'budget';
UPDATE core.outbox_rows SET kind = CASE kind
        WHEN 'budgets.budget.created' THEN 'spend_budgets.spend_budget.created'
        WHEN 'budgets.budget.updated' THEN 'spend_budgets.spend_budget.updated'
    END
    WHERE kind IN ('budgets.budget.created', 'budgets.budget.updated');
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;
