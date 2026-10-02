-- Takes the agent sessions back out: the fence, then the table.

DROP POLICY tenant_fence ON core.agent_sessions;
DROP TABLE core.agent_sessions;
