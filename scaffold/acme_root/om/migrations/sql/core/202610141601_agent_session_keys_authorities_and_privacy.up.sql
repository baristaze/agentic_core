-- A session's keys, authorities, and privacy say agent session, as the
-- session itself does, and so does every column that names one: a copy's
-- own sessions take the bare word. A rename keeps the rows, and their
-- grants, fences, and policies go with the table.

ALTER TABLE core.session_keys RENAME TO agent_session_keys;
ALTER TABLE core.agent_session_keys RENAME CONSTRAINT pk_session_keys TO pk_agent_session_keys;
ALTER TABLE core.agent_session_keys RENAME COLUMN session_id TO agent_session_id;
ALTER INDEX core.uq_session_keys_org_id_session_id_version
    RENAME TO uq_agent_session_keys_org_id_agent_session_id_version;

ALTER TABLE core.session_authorities RENAME TO agent_session_authorities;
ALTER TABLE core.agent_session_authorities
    RENAME CONSTRAINT pk_session_authorities TO pk_agent_session_authorities;
ALTER INDEX core.ix_session_authorities_org_id RENAME TO ix_agent_session_authorities_org_id;

ALTER TABLE core.session_privacy RENAME TO agent_session_privacy;
ALTER TABLE core.agent_session_privacy
    RENAME CONSTRAINT pk_session_privacy TO pk_agent_session_privacy;
ALTER TABLE core.agent_session_privacy RENAME COLUMN session_id TO agent_session_id;
ALTER INDEX core.uq_session_privacy_org_id_session_id
    RENAME TO uq_agent_session_privacy_org_id_agent_session_id;

ALTER TABLE core.fill_sets RENAME COLUMN session_id TO agent_session_id;
ALTER INDEX core.uq_fill_sets_org_id_session_id_version
    RENAME TO uq_fill_sets_org_id_agent_session_id_version;

-- The values a session's waits and wakes are stored under move in place:
-- a request a session waits on, and a wake or an authority's change not yet
-- relayed. The fence binds the migration login too, so it is lifted for
-- these statements alone.
ALTER TABLE core.lease_requests NO FORCE ROW LEVEL SECURITY;
UPDATE core.lease_requests SET waiter_kind = 'agent_session' WHERE waiter_kind = 'session';
ALTER TABLE core.lease_requests FORCE ROW LEVEL SECURITY;

ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
UPDATE core.outbox_rows SET kind = CASE kind
        WHEN 'work.WAKE_SESSION' THEN 'work.WAKE_AGENT_SESSION'
        WHEN 'work.WAKE_SESSIONS' THEN 'work.WAKE_AGENT_SESSIONS'
        WHEN 'attribution.session_authority.created' THEN 'attribution.agent_session_authority.created'
        WHEN 'attribution.session_authority.updated' THEN 'attribution.agent_session_authority.updated'
    END
    WHERE kind IN (
        'work.WAKE_SESSION',
        'work.WAKE_SESSIONS',
        'attribution.session_authority.created',
        'attribution.session_authority.updated'
    );
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;
