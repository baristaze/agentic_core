-- The names and values before agent session, and the rows kept.

ALTER TABLE core.outbox_rows NO FORCE ROW LEVEL SECURITY;
UPDATE core.outbox_rows SET kind = CASE kind
        WHEN 'work.WAKE_AGENT_SESSION' THEN 'work.WAKE_SESSION'
        WHEN 'work.WAKE_AGENT_SESSIONS' THEN 'work.WAKE_SESSIONS'
        WHEN 'attribution.agent_session_authority.created' THEN 'attribution.session_authority.created'
        WHEN 'attribution.agent_session_authority.updated' THEN 'attribution.session_authority.updated'
    END
    WHERE kind IN (
        'work.WAKE_AGENT_SESSION',
        'work.WAKE_AGENT_SESSIONS',
        'attribution.agent_session_authority.created',
        'attribution.agent_session_authority.updated'
    );
ALTER TABLE core.outbox_rows FORCE ROW LEVEL SECURITY;

ALTER TABLE core.lease_requests NO FORCE ROW LEVEL SECURITY;
UPDATE core.lease_requests SET waiter_kind = 'session' WHERE waiter_kind = 'agent_session';
ALTER TABLE core.lease_requests FORCE ROW LEVEL SECURITY;

ALTER INDEX core.uq_fill_sets_org_id_agent_session_id_version
    RENAME TO uq_fill_sets_org_id_session_id_version;
ALTER TABLE core.fill_sets RENAME COLUMN agent_session_id TO session_id;

ALTER INDEX core.uq_agent_session_privacy_org_id_agent_session_id
    RENAME TO uq_session_privacy_org_id_session_id;
ALTER TABLE core.agent_session_privacy RENAME COLUMN agent_session_id TO session_id;
ALTER TABLE core.agent_session_privacy
    RENAME CONSTRAINT pk_agent_session_privacy TO pk_session_privacy;
ALTER TABLE core.agent_session_privacy RENAME TO session_privacy;

ALTER INDEX core.ix_agent_session_authorities_org_id RENAME TO ix_session_authorities_org_id;
ALTER TABLE core.agent_session_authorities
    RENAME CONSTRAINT pk_agent_session_authorities TO pk_session_authorities;
ALTER TABLE core.agent_session_authorities RENAME TO session_authorities;

ALTER INDEX core.uq_agent_session_keys_org_id_agent_session_id_version
    RENAME TO uq_session_keys_org_id_session_id_version;
ALTER TABLE core.agent_session_keys RENAME COLUMN agent_session_id TO session_id;
ALTER TABLE core.agent_session_keys RENAME CONSTRAINT pk_agent_session_keys TO pk_session_keys;
ALTER TABLE core.agent_session_keys RENAME TO session_keys;
