-- A tree's concurrency is a dead column (ADR 0038). The release before took
-- it out of the mapping, so none of its statements names it, and nothing
-- reads what it holds. Nothing else changes.

ALTER TABLE core.agent_trees DROP COLUMN concurrency;
