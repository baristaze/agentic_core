-- Takes the budgets back out: the fence, then the table.

DROP POLICY tenant_fence ON core.budgets;
DROP TABLE core.budgets;
