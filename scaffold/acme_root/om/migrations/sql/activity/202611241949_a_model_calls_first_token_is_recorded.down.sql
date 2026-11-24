-- Takes the time to the first token back out. A time recorded since is
-- lost with it.

ALTER TABLE activity.usage_records DROP COLUMN first_token_ms;
