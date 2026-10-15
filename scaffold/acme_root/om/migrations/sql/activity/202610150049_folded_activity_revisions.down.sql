-- The engine's own tables of the activity role go, grants first, and the role
-- is the base's.

REVOKE SELECT, DELETE ON activity.tool_outputs FROM acme_purge;
REVOKE SELECT, DELETE ON activity.agent_step_cursors FROM acme_purge;
REVOKE SELECT, DELETE ON activity.agent_steps FROM acme_purge;
REVOKE USAGE ON SCHEMA activity FROM acme_purge;
DROP TABLE activity.usage_records;
DROP TABLE activity.tool_outputs;
DROP TABLE activity.spend_budget_tallies;
DROP TABLE activity.spend_budget_settlements;
DROP TABLE activity.spend_budget_holds;
DROP TABLE activity.agent_steps;
DROP TABLE activity.agent_step_cursors;
