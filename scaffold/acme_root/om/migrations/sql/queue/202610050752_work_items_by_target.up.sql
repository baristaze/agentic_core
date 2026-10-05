-- A read of one target's items of a kind within its tenant, newest first:
-- whether one is open, and the latest. Without this index such a read
-- filters the tenant's whole work history. The engine makes none itself;
-- the index sits with the table so a copy that does never adds it.
--
-- The build holds SHARE on queue.work_items: reads go on, and writes wait
-- until it ends. It changes no row. A database that already holds the
-- index under this name migrates through it unchanged; `migrate-check`
-- holds its columns to the table's declaration.

CREATE INDEX IF NOT EXISTS ix_work_items_org_id_kind_target_id_created_at
    ON queue.work_items (org_id, kind, target_id, created_at);
