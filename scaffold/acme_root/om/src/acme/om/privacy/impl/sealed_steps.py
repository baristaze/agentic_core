"""Step storage that seals content on its way in and opens it on its way
out, over another step storage, behind the same interface. Nothing above it
sees a sealed step, and nothing below it sees content in the clear.

A step that says something, its blocks or its children, is sealed under the
current version of its session's key with AES-GCM, bound to the tenant, the
session, the step, and the version, so a sealed blob copied to another step
opens nothing. A step that says nothing, a lifecycle mark or a request that
only references, needs no key and is kept as it is. A read opens each step
with the version it names; a version destroyed reads as absent, and the
step keeps its place, its type, and its header."""

import base64
import json
import os
from collections.abc import Sequence
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from acme.om.exceptions import ValidationFailed
from acme.om.privacy.keys import SessionKeysInterface
from acme.om.steps.storage import StepStorageInterface
from acme.om.steps.types.content import Children, Content, ContentState, Sealed
from acme.om.steps.types.page import StepCursor
from acme.om.steps.types.step import Step

NONCE_BYTES = 12


def says_something(step: Step) -> bool:
    """Whether a step carries content to seal: a block, or a child."""
    return bool(step.content.blocks) or step.children != Children()


def refuse_sealed(steps: Sequence[Step]) -> None:
    """Content arrives in the clear, or absent: only storage seals it, so a
    sealed step from above is one no key of this platform made."""
    if any(step.content.is_sealed() for step in steps):
        raise ValidationFailed("only storage seals a step's content")


def bound_to(org_id: UUID, step: Step, version: int) -> bytes:
    """What a sealed blob is bound to: the tenant, the session, the step,
    and the version of the key."""
    return (
        b"step content"
        + org_id.bytes
        + step.session_id.bytes
        + step.id.bytes
        + version.to_bytes(8, "big")
    )


def sealed(org_id: UUID, step: Step, version: int, key: bytes) -> Step:
    """The step with its blocks and children sealed, and nothing in the clear."""
    said = json.dumps(
        {
            "blocks": step.content.model_dump(mode="json")["blocks"],
            "children": step.children.model_dump(mode="json"),
        },
        separators=(",", ":"),
    ).encode()
    nonce = os.urandom(NONCE_BYTES)
    blob = nonce + AESGCM(key).encrypt(nonce, said, bound_to(org_id, step, version))
    seal = Sealed(version=version, ciphertext=base64.urlsafe_b64encode(blob).decode())
    return step.model_copy(
        update={
            "content": Content(state=ContentState.SEALED, sealed=seal),
            "children": Children(),
        }
    )


def opened(org_id: UUID, step: Step, key: bytes) -> Step:
    """The sealed step with its blocks and children back. A blob that does
    not open under the version it names was written by no sealing layer,
    and is refused rather than read as anything."""
    seal = step.content.sealed
    if seal is None:
        raise ValueError(f"step {step.id} is not sealed")
    blob = base64.urlsafe_b64decode(seal.ciphertext.encode())
    try:
        said = json.loads(
            AESGCM(key).decrypt(
                blob[:NONCE_BYTES], blob[NONCE_BYTES:], bound_to(org_id, step, seal.version)
            )
        )
    except InvalidTag:
        raise ValueError(
            f"step {step.id} does not open under version {seal.version} of its key"
        ) from None
    return Step.model_validate(
        {
            **step.model_dump(),
            "content": {"blocks": said["blocks"]},
            "children": said["children"],
        }
    )


def absent(step: Step) -> Step:
    """The step's shape, with its content gone: a hole in a known place."""
    return step.model_copy(
        update={"content": Content(state=ContentState.ABSENT), "children": Children()}
    )


class StepStorageSealedImpl(StepStorageInterface):
    def __init__(self, inner: StepStorageInterface, keys: SessionKeysInterface) -> None:
        self._inner = inner
        self._keys = keys

    async def begin_run(self, org_id: UUID, session_id: UUID) -> int:
        return await self._inner.begin_run(org_id, session_id)

    async def append_steps(
        self, org_id: UUID, session_id: UUID, epoch: int, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        batch = await self._sealed(org_id, session_id, steps)
        stored = await self._inner.append_steps(org_id, session_id, epoch, batch)
        return tuple(await self._opened(org_id, session_id, stored))

    async def append_inputs(
        self, org_id: UUID, session_id: UUID, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        batch = await self._sealed(org_id, session_id, steps)
        stored = await self._inner.append_inputs(org_id, session_id, batch)
        return tuple(await self._opened(org_id, session_id, stored))

    async def read_steps(
        self, org_id: UUID, session_id: UUID, after_seq: int, limit: int
    ) -> list[Step]:
        stored = await self._inner.read_steps(org_id, session_id, after_seq, limit)
        return await self._opened(org_id, session_id, stored)

    async def read_cursor(self, org_id: UUID, session_id: UUID) -> StepCursor:
        return await self._inner.read_cursor(org_id, session_id)

    async def _sealed(self, org_id: UUID, session_id: UUID, steps: Sequence[Step]) -> list[Step]:
        """Each step that says something, sealed under the session's current
        key; `KeyRevoked` when there is something to seal and the key is
        revoked. Content sealed already is refused: only this layer seals."""
        refuse_sealed(steps)
        if not any(says_something(step) for step in steps):
            return list(steps)
        current = await self._keys.current(org_id, session_id)
        return [
            sealed(org_id, step, current.version, current.key) if says_something(step) else step
            for step in steps
        ]

    async def _opened(self, org_id: UUID, session_id: UUID, steps: Sequence[Step]) -> list[Step]:
        versions = {
            step.content.sealed.version for step in steps if step.content.sealed is not None
        }
        keys = await self._keys.opened(org_id, session_id, versions)
        result: list[Step] = []
        for step in steps:
            seal = step.content.sealed
            if seal is None:
                result.append(step)
            elif seal.version in keys:
                result.append(opened(org_id, step, keys[seal.version]))
            else:
                result.append(absent(step))
        return result
