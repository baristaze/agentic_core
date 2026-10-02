-- The history is append-only for every serving login (ADR 1002). Its one
-- delete, the purge, runs under a login of its own that holds SELECT and
-- DELETE on the steps and their cursor rows, and nothing else of the role
-- (ADR 1010). The tenant fence admits it within the tenant its transaction
-- names; its system-scope clause names the system login alone, so the
-- purge login reaches no row under the system scope.

GRANT USAGE ON SCHEMA activity TO acme_purge;
GRANT SELECT, DELETE ON activity.steps TO acme_purge;
GRANT SELECT, DELETE ON activity.step_cursors TO acme_purge;
