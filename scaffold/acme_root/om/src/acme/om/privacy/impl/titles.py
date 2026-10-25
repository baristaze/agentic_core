"""The seal a session's title takes on its way into storage, under a key of
the session's own, as a step's content is sealed under its session's key.

The title is sealed with AES-GCM under the session's title key, bound to the
tenant, the session, and the version, so a blob copied to another session
opens nothing. The title key is kept on the session's privacy record,
wrapped under the tenant's title key, which the key service wraps. So a
page of titles opens with one read of their title keys and one unwrap,
whatever its size, and revoking the session's key clears its title key in
the commit that destroys the rest: the title is noise from then on, and the
session keeps its place (ADR 1042)."""

from collections.abc import Mapping
from uuid import UUID

from acme.om.agent_sessions.seal import TitleSealInterface
from acme.om.privacy.impl.blobs import VERSION_BYTES, Bound, open_blob, seal_blob
from acme.om.privacy.keys import AgentSessionKeysInterface, OpenKey

TITLE_VERSION = 1
"""A title key has one version: a title is written once, and never sealed
again."""


def bound_to(org_id: UUID, agent_session_id: UUID) -> Bound:
    """What a sealed title is bound to: the tenant, the session, and the
    version of its key."""
    return lambda version: (
        b"session title"
        + org_id.bytes
        + agent_session_id.bytes
        + version.to_bytes(VERSION_BYTES, "big")
    )


class TitleSealKeysImpl(TitleSealInterface):
    def __init__(self, keys: AgentSessionKeysInterface) -> None:
        self._keys = keys

    async def seal(self, org_id: UUID, agent_session_id: UUID, title: str) -> bytes:
        key = await self._keys.title_key(org_id, agent_session_id)
        return seal_blob(
            OpenKey(version=TITLE_VERSION, key=key),
            title.encode(),
            bound_to(org_id, agent_session_id),
        )

    async def open(self, org_id: UUID, sealed: Mapping[UUID, bytes]) -> dict[UUID, str]:
        keys = await self._keys.title_keys(org_id, sealed.keys())
        return {
            agent_session_id: open_blob(
                keys[agent_session_id],
                blob,
                bound_to(org_id, agent_session_id),
                f"the title of session {agent_session_id}",
            ).decode()
            for agent_session_id, blob in sealed.items()
            if agent_session_id in keys
        }
