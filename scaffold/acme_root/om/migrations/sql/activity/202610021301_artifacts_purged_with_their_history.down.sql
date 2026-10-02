-- Takes the purge login's reach on the artifacts' records back, and the
-- index by tenant alone.

DROP INDEX activity.ix_artifacts_org_id_session_id;
CREATE INDEX ix_artifacts_org_id ON activity.artifacts (org_id);

REVOKE SELECT, DELETE ON activity.artifacts FROM acme_purge;
