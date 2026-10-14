"""The seal a tool output's text takes on its way to the object store, under
its session's key, as a step's content is sealed on its way into storage.

The text is sealed under the current version of the session's key with
AES-GCM, bound to the tenant, the session, the tool output, and the version,
so a blob copied to another tool output opens nothing. The blob carries the
version it was sealed under, and opens with that version alone: once the
version is destroyed, the blob is noise and the tool output's record stays. A
session that keeps no content at rest has its tool output sealed the same
way, and held in the runtime's memory instead of the store, so revoking
its key erases it there too."""

from uuid import UUID

from acme.om.context import TenantContext
from acme.om.privacy.impl.blobs import VERSION_BYTES, blob_version, open_blob, seal_blob
from acme.om.privacy.keys import AgentSessionKeysInterface
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.types.agent_session_privacy import StorageMode
from acme.om.windows.seal import SealedToolOutput, ToolOutputSealInterface


def bound_to(org_id: UUID, agent_session_id: UUID, tool_output_id: UUID, version: int) -> bytes:
    """What a sealed tool output is bound to: the tenant, the session, the
    tool output, and the version of the key."""
    return (
        b"tool output content"
        + org_id.bytes
        + agent_session_id.bytes
        + tool_output_id.bytes
        + version.to_bytes(VERSION_BYTES, "big")
    )


class ToolOutputSealKeysImpl(ToolOutputSealInterface):
    def __init__(self, keys: AgentSessionKeysInterface, policies: PrivacyStorageInterface) -> None:
        self._keys = keys
        self._policies = policies

    async def seal(
        self, ctx: TenantContext, agent_session_id: UUID, tool_output_id: UUID, data: bytes
    ) -> SealedToolOutput:
        record = await self._policies.read_privacy(ctx.org_id, agent_session_id)
        at_rest = record is None or record.policy.mode is not StorageMode.MEMORY_ONLY
        current = await self._keys.current(ctx.org_id, agent_session_id)
        blob = seal_blob(
            current,
            data,
            lambda version: bound_to(ctx.org_id, agent_session_id, tool_output_id, version),
        )
        return SealedToolOutput(blob=blob, at_rest=at_rest)

    async def open(
        self, ctx: TenantContext, agent_session_id: UUID, tool_output_id: UUID, sealed: bytes
    ) -> bytes | None:
        what = f"tool output {tool_output_id}"
        version = blob_version(sealed, what)
        keys = await self._keys.opened(ctx.org_id, agent_session_id, {version})
        key = keys.get(version)
        if key is None:
            return None
        return open_blob(
            key,
            sealed,
            lambda version: bound_to(ctx.org_id, agent_session_id, tool_output_id, version),
            what,
        )
