"""The key service contract: what every impl of `KeyServiceInterface` holds,
the memory twin and the cloud key service alike. A data key comes back from
its wrapped copy only under the name it was wrapped for, and a rotation of
the tenant's wrapping key moves a wrapped key without changing the data key
it holds.

A concrete class provides the service and `rotate`, the impl's own way of
moving a tenant's wrapping key one version up."""

from collections.abc import Awaitable, Callable
from uuid import UUID

import pytest

from acme.infra.base import new_id
from acme.infra.keys import DataKey, KeyRefused, KeyServiceInterface, WrappedKey

Rotate = Callable[[UUID], Awaitable[None]]


def altered(wrapped: WrappedKey) -> WrappedKey:
    """The same wrapped key with its last byte flipped."""
    blob = wrapped.blob[:-1] + bytes([wrapped.blob[-1] ^ 1])
    return wrapped.model_copy(update={"blob": blob})


class KeyServiceContract:
    @pytest.fixture
    def keys(self) -> KeyServiceInterface:
        raise NotImplementedError("the concrete test class provides the key service")

    @pytest.fixture
    def rotate(self) -> Rotate:
        raise NotImplementedError("the concrete test class provides the rotation")

    async def test_a_data_key_unwraps_to_itself(self, keys: KeyServiceInterface) -> None:
        org, key = new_id(), new_id()
        first = await keys.generate(org, key, 1)
        second = await keys.generate(org, key, 2)
        assert len(first.plaintext) == 32
        assert first.plaintext != second.plaintext
        assert first.plaintext not in first.wrapped.blob
        assert await keys.unwrap(org, key, 1, first.wrapped) == first.plaintext
        assert await keys.unwrap(org, key, 2, second.wrapped) == second.plaintext

    async def test_a_wrapped_key_opens_under_its_own_name_alone(
        self, keys: KeyServiceInterface
    ) -> None:
        """A wrapped key copied to another tenant, another session, or
        another version opens nothing there."""
        org, key = new_id(), new_id()
        data = await keys.generate(org, key, 1)
        for name in ((new_id(), key, 1), (org, new_id(), 1), (org, key, 2)):
            with pytest.raises(KeyRefused):
                await keys.unwrap(*name, data.wrapped)
            with pytest.raises(KeyRefused):
                await keys.rewrap(*name, data.wrapped)

    async def test_an_altered_key_is_refused(self, keys: KeyServiceInterface) -> None:
        org, key = new_id(), new_id()
        data = await keys.generate(org, key, 1)
        with pytest.raises(KeyRefused):
            await keys.unwrap(org, key, 1, altered(data.wrapped))

    async def test_a_rotation_keeps_every_key_and_a_rewrap_moves_it(
        self, keys: KeyServiceInterface, rotate: Rotate
    ) -> None:
        """A key wrapped before the tenant's wrapping key rotated still opens;
        its rewrap is a new wrapped copy under the current wrapping key that
        holds the same data key. Another tenant's keys do not move."""
        org, other, key = new_id(), new_id(), new_id()
        before: DataKey = await keys.generate(org, key, 1)
        elsewhere = await keys.generate(other, key, 1)
        await rotate(org)
        assert await keys.unwrap(org, key, 1, before.wrapped) == before.plaintext
        moved = await keys.rewrap(org, key, 1, before.wrapped)
        assert moved.blob != before.wrapped.blob
        assert await keys.unwrap(org, key, 1, moved) == before.plaintext
        after = await keys.generate(org, key, 2)
        assert await keys.unwrap(org, key, 2, after.wrapped) == after.plaintext
        assert await keys.unwrap(other, key, 1, elsewhere.wrapped) == elsewhere.plaintext
        with pytest.raises(KeyRefused):
            await keys.unwrap(org, key, 1, elsewhere.wrapped)
