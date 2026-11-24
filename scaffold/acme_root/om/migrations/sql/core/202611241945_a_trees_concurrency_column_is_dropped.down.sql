-- Puts the column back, empty: what it held is gone, and nothing reads it.

ALTER TABLE core.agent_trees ADD COLUMN concurrency integer NULL;
