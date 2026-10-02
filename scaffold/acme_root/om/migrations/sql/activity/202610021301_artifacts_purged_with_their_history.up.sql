-- An artifact goes with its session's history (ADR 1010). The purge login
-- holds SELECT and DELETE on the records, as it holds them on the steps, and
-- nothing else of them; the serving logins keep SELECT and INSERT. A purge
-- reads a session's records, or a tenant's, so the index by tenant leads to
-- the session.

GRANT SELECT, DELETE ON activity.artifacts TO acme_purge;

DROP INDEX activity.ix_artifacts_org_id;
CREATE INDEX ix_artifacts_org_id_session_id ON activity.artifacts (org_id, session_id);
