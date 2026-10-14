-- The names and values before spend budget, and the rows kept.

ALTER TABLE activity.agent_steps NO FORCE ROW LEVEL SECURITY;
UPDATE activity.agent_steps
    SET header = jsonb_set(
        header #- '{park,job,spend_hold_id}', '{park,job,hold_id}', header -> 'park' -> 'job' -> 'spend_hold_id'
    )
    WHERE header ->> 'kind' = 'parked' AND header -> 'park' -> 'job' ? 'spend_hold_id';
UPDATE activity.agent_steps
    SET header = jsonb_set(header, '{park,reason}', '"budget"')
    WHERE header ->> 'kind' = 'parked' AND header -> 'park' ->> 'reason' = 'spend_budget';
UPDATE activity.agent_steps
    SET header = (header - 'spend_hold_id') || jsonb_build_object('hold_id', header -> 'spend_hold_id')
    WHERE header ->> 'kind' = 'model_request' AND header ? 'spend_hold_id';
ALTER TABLE activity.agent_steps FORCE ROW LEVEL SECURITY;

ALTER TABLE activity.spend_budget_holds NO FORCE ROW LEVEL SECURITY;
UPDATE activity.spend_budget_holds
    SET lines = (
        SELECT jsonb_agg(
            jsonb_set(
                (line - 'spend_budget_id') || jsonb_build_object('budget_id', line -> 'spend_budget_id'),
                '{scope,kind}',
                to_jsonb(CASE line -> 'scope' ->> 'kind'
                    WHEN 'agent_session' THEN 'session'
                    WHEN 'agent_project' THEN 'project'
                    ELSE line -> 'scope' ->> 'kind'
                END)
            )
            ORDER BY i
        )
        FROM jsonb_array_elements(lines) WITH ORDINALITY AS listed (line, i)
    )
    WHERE jsonb_array_length(lines) > 0;
ALTER TABLE activity.spend_budget_holds FORCE ROW LEVEL SECURITY;

ALTER INDEX activity.uq_usage_records_org_id_spend_hold_id RENAME TO uq_usage_records_org_id_hold_id;
ALTER TABLE activity.usage_records RENAME COLUMN spend_hold_id TO hold_id;

ALTER TABLE activity.spend_budget_tallies RENAME COLUMN spend_budget_id TO budget_id;
ALTER TABLE activity.spend_budget_tallies RENAME CONSTRAINT pk_spend_budget_tallies TO pk_budget_tallies;
ALTER TABLE activity.spend_budget_tallies RENAME TO budget_tallies;

ALTER INDEX activity.uq_spend_budget_settlements_org_id_spend_hold_id
    RENAME TO uq_budget_settlements_org_id_hold_id;
ALTER TABLE activity.spend_budget_settlements RENAME COLUMN spend_hold_id TO hold_id;
ALTER TABLE activity.spend_budget_settlements
    RENAME CONSTRAINT pk_spend_budget_settlements TO pk_budget_settlements;
ALTER TABLE activity.spend_budget_settlements RENAME TO budget_settlements;

ALTER INDEX activity.ix_spend_budget_holds_org_id RENAME TO ix_budget_holds_org_id;
ALTER TABLE activity.spend_budget_holds RENAME CONSTRAINT pk_spend_budget_holds TO pk_budget_holds;
ALTER TABLE activity.spend_budget_holds RENAME TO budget_holds;
