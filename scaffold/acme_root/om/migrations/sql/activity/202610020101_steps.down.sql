-- Takes the history and its cursors back out: the fences, then the tables.

DROP POLICY tenant_fence ON activity.step_cursors;
DROP POLICY tenant_fence ON activity.steps;
DROP TABLE activity.step_cursors;
DROP TABLE activity.steps;
