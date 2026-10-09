-- A session holds whether it holds private data, which it takes from where
-- it came. A session stored before this change is taken to hold it: most
-- kinds do, and a mark set where none is needed asks a person once more,
-- while one missing lets a child act outward unasked. The default stays:
-- the previous release writes no such column, during a roll and after a
-- rollback, and its rows take the same answer.

ALTER TABLE core.agent_sessions
    ADD COLUMN holds_private boolean NOT NULL DEFAULT true;
