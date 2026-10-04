-- Takes the session's private-data mark back out.

ALTER TABLE core.agent_sessions
    DROP COLUMN holds_private;
