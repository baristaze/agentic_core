-- Takes the tool policies back out: the fence, then the table.

DROP POLICY tenant_fence ON core.tool_policies;
DROP TABLE core.tool_policies;
