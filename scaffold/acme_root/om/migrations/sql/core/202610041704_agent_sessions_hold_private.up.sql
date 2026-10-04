-- A session holds whether it holds private data, which it takes from where
-- it came. A session stored before this change is taken to hold it: most
-- kinds do, and a mark set where none is needed asks a person once more,
-- while one missing lets a child act outward unasked. The default fills
-- those rows alone and is dropped, so every new row says.

ALTER TABLE core.agent_sessions
    ADD COLUMN holds_private boolean NOT NULL DEFAULT true;
ALTER TABLE core.agent_sessions
    ALTER COLUMN holds_private DROP DEFAULT;
