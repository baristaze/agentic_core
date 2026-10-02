-- Takes the purge login's reach on the history back.

REVOKE SELECT, DELETE ON activity.step_cursors FROM acme_purge;
REVOKE SELECT, DELETE ON activity.steps FROM acme_purge;
REVOKE USAGE ON SCHEMA activity FROM acme_purge;
