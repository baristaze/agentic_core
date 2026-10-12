-- Takes who sees a session, and who steers it, back out.

ALTER TABLE core.agent_sessions
    DROP COLUMN steerer,
    DROP COLUMN visibility;
