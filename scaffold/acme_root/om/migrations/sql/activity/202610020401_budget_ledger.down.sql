-- Takes the ledger back out: the fences, then the tables.

DROP POLICY tenant_fence ON activity.budget_settlements;
DROP POLICY tenant_fence ON activity.budget_holds;
DROP POLICY tenant_fence ON activity.budget_tallies;
DROP TABLE activity.budget_settlements;
DROP TABLE activity.budget_holds;
DROP TABLE activity.budget_tallies;
