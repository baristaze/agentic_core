-- Takes the artifacts' records back out: the fence, then the table.

DROP POLICY tenant_fence ON activity.artifacts;
DROP TABLE activity.artifacts;
