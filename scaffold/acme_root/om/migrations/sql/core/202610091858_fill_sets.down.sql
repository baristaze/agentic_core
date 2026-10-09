-- Takes the fill sets back out: the fence, then the table.

DROP POLICY tenant_fence ON core.fill_sets;
DROP TABLE core.fill_sets;
