-- Takes the close back out: the gate's index over every budget, then the
-- column.

DROP INDEX core.ix_budgets_org_id_scope_kind_scope_key;
CREATE INDEX ix_budgets_org_id_scope_kind_scope_key ON core.budgets (org_id, scope_kind, scope_key);

ALTER TABLE core.budgets
    DROP COLUMN closed_at;
