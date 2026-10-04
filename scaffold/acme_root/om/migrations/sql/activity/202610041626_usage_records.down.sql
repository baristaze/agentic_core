-- Drops the usage records, their fence, and their indexes.

DROP POLICY tenant_fence ON activity.usage_records;
DROP TABLE activity.usage_records;
