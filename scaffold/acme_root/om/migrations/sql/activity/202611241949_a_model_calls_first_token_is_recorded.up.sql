-- A model call's time to its first token, beside its latency, in its usage
-- record: from the request sent to the first part the provider streamed
-- back. A row written before it, and one the release before writes, holds
-- none: the column is null there, as it is for a call no part came back
-- from. The table's grants cover it. Nothing else changes.

ALTER TABLE activity.usage_records ADD COLUMN first_token_ms bigint NULL;
