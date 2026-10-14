-- The ledger of spend budgets says spend: its holds, its settlements, and
-- its tallies, and every column that names a hold or a budget. A rename
-- keeps the rows, and their grants, fences, and policies go with the table.

ALTER TABLE activity.budget_holds RENAME TO spend_budget_holds;
ALTER TABLE activity.spend_budget_holds RENAME CONSTRAINT pk_budget_holds TO pk_spend_budget_holds;
ALTER INDEX activity.ix_budget_holds_org_id RENAME TO ix_spend_budget_holds_org_id;

ALTER TABLE activity.budget_settlements RENAME TO spend_budget_settlements;
ALTER TABLE activity.spend_budget_settlements
    RENAME CONSTRAINT pk_budget_settlements TO pk_spend_budget_settlements;
ALTER TABLE activity.spend_budget_settlements RENAME COLUMN hold_id TO spend_hold_id;
ALTER INDEX activity.uq_budget_settlements_org_id_hold_id
    RENAME TO uq_spend_budget_settlements_org_id_spend_hold_id;

ALTER TABLE activity.budget_tallies RENAME TO spend_budget_tallies;
ALTER TABLE activity.spend_budget_tallies RENAME CONSTRAINT pk_budget_tallies TO pk_spend_budget_tallies;
ALTER TABLE activity.spend_budget_tallies RENAME COLUMN budget_id TO spend_budget_id;

ALTER TABLE activity.usage_records RENAME COLUMN hold_id TO spend_hold_id;
ALTER INDEX activity.uq_usage_records_org_id_hold_id RENAME TO uq_usage_records_org_id_spend_hold_id;

-- A hold's lines name their spend budget under the new key, and the scope
-- each caps an agent session or an agent project; a model request and a
-- park on a job name their spend hold so, and a park the gate wrote says
-- spend_budget. The fence binds the migration login too, so it is lifted
-- for these statements alone.
ALTER TABLE activity.spend_budget_holds NO FORCE ROW LEVEL SECURITY;
UPDATE activity.spend_budget_holds
    SET lines = (
        SELECT jsonb_agg(
            jsonb_set(
                (line - 'budget_id') || jsonb_build_object('spend_budget_id', line -> 'budget_id'),
                '{scope,kind}',
                to_jsonb(CASE line -> 'scope' ->> 'kind'
                    WHEN 'session' THEN 'agent_session'
                    WHEN 'project' THEN 'agent_project'
                    ELSE line -> 'scope' ->> 'kind'
                END)
            )
            ORDER BY i
        )
        FROM jsonb_array_elements(lines) WITH ORDINALITY AS listed (line, i)
    )
    WHERE jsonb_array_length(lines) > 0;
ALTER TABLE activity.spend_budget_holds FORCE ROW LEVEL SECURITY;

ALTER TABLE activity.agent_steps NO FORCE ROW LEVEL SECURITY;
UPDATE activity.agent_steps
    SET header = (header - 'hold_id') || jsonb_build_object('spend_hold_id', header -> 'hold_id')
    WHERE header ->> 'kind' = 'model_request' AND header ? 'hold_id';
UPDATE activity.agent_steps
    SET header = jsonb_set(header, '{park,reason}', '"spend_budget"')
    WHERE header ->> 'kind' = 'parked' AND header -> 'park' ->> 'reason' = 'budget';
UPDATE activity.agent_steps
    SET header = jsonb_set(
        header #- '{park,job,hold_id}', '{park,job,spend_hold_id}', header -> 'park' -> 'job' -> 'hold_id'
    )
    WHERE header ->> 'kind' = 'parked' AND header -> 'park' -> 'job' ? 'hold_id';
ALTER TABLE activity.agent_steps FORCE ROW LEVEL SECURITY;
