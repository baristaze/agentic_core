-- A budget closes once, and from then on binds no call (ADR 1036). Every
-- budget stored before this change is open, as the column's NULL says. The
-- gate reads the open budgets of a call's scopes, so its index holds those
-- alone: a scope's closed run budgets never lengthen the read every call
-- makes. Its predicate is a literal test, which a generic plan can prove.

ALTER TABLE core.budgets
    ADD COLUMN closed_at timestamptz;

DROP INDEX core.ix_budgets_org_id_scope_kind_scope_key;
CREATE INDEX ix_budgets_org_id_scope_kind_scope_key ON core.budgets (org_id, scope_kind, scope_key)
    WHERE closed_at IS NULL;
