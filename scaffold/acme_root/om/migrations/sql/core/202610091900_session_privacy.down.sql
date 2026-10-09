-- Takes the privacy records and the key versions back out: the fences, then
-- the tables.

DROP POLICY tenant_fence ON core.session_keys;
DROP POLICY tenant_fence ON core.session_privacy;
DROP TABLE core.session_keys;
DROP TABLE core.session_privacy;
