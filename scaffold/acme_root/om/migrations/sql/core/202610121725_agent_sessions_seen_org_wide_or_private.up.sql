-- A session is org-wide or private, and caches who steers it, null while
-- its creator does. A session stored before this change is taken to be
-- private: one hidden from a member its creator would show it to is
-- switched open by that creator, while one wrongly open shows the results
-- of another person's delegated calls. The default stays: the previous
-- release writes no such column, during a roll and after a rollback, and
-- its rows take the same answer.

ALTER TABLE core.agent_sessions
    ADD COLUMN visibility text NOT NULL DEFAULT 'private',
    ADD COLUMN steerer uuid;
