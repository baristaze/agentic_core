"""The key service contract: what every impl of `KeyServiceInterface` holds,
the memory twin and the cloud key service alike. A data key comes back from
its wrapped copy only under the name it was wrapped for, and a rotation of
the tenant's wrapping key moves a wrapped key without changing the data key
it holds. It runs over the memory twin, and over the KMS impl against a
stubbed client that wraps the way KMS does: under a key's current material,
bound to the encryption context, refusing a blob presented under any other
context or key, or altered. Then what each impl holds beyond it."""

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from botocore.exceptions import ClientError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from acme.infra.base import new_id
from acme.infra.exceptions import BackendFailed
from acme.infra.keys import DataKey, KeyRefused, KeyServiceInterface, WrappedKey
from acme.infra.keys.kms import KeyServiceKmsImpl
from acme.infra.keys.memory import KeyServiceMemoryImpl, root_key

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


ROOT = "bG9jYWwtb25seS1rZXlzLXJvb3Qtbm90LXNlY3JldCE="


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "from the driver"}}, "Op")


class StubKms:
    """KMS in a dict. Each key, by its ARN, is a list of materials, the last
    one current; an alias names one key, and a name never seen before is an
    alias of a key made for it. A blob names its key's ARN and its material
    and seals the data key under it, with the encryption context as the
    associated data, as KMS binds it. A call that names a key opens a blob
    of that key alone, as KMS's `IncorrectKeyException` says."""

    def __init__(self) -> None:
        self.materials: dict[str, list[bytes]] = {}
        self.aliases: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def resolve(self, key_id: str) -> str:
        """The ARN a key id names: itself, or the key an alias names."""
        if key_id in self.materials:
            return key_id
        if key_id not in self.aliases:
            self.repoint(key_id)
        return self.aliases[key_id]

    def repoint(self, alias: str) -> str:
        """The alias, moved onto a key made now; the key it named stays."""
        arn = f"arn:aws:kms:us-east-1:111122223333:key/{new_id()}"
        self.materials[arn] = [os.urandom(32)]
        self.aliases[alias] = arn
        return arn

    def rotate(self, key_id: str) -> None:
        self.materials[self.resolve(key_id)].append(os.urandom(32))

    @staticmethod
    def _bound(context: dict[str, str]) -> bytes:
        return repr(sorted(context.items())).encode()

    def _seal(self, arn: str, plaintext: bytes, context: dict[str, str]) -> bytes:
        materials = self.materials[arn]
        nonce = os.urandom(12)
        sealed = AESGCM(materials[-1]).encrypt(nonce, plaintext, self._bound(context))
        return f"{arn}|{len(materials) - 1}|".encode() + nonce + sealed

    def _open(self, blob: bytes, key_id: str, context: dict[str, str]) -> bytes:
        named, material, rest = blob.split(b"|", 2)
        if named.decode() != self.resolve(key_id):
            raise client_error("IncorrectKeyException")
        try:
            return AESGCM(self.materials[named.decode()][int(material)]).decrypt(
                rest[:12], rest[12:], self._bound(context)
            )
        except InvalidTag, KeyError, IndexError:
            raise client_error("InvalidCiphertextException") from None

    async def generate_data_key(self, **request: Any) -> dict[str, Any]:
        key_id, context = request["KeyId"], request["EncryptionContext"]
        self.calls.append(("generate_data_key", key_id, context))
        arn = self.resolve(key_id)
        plaintext = os.urandom(request["NumberOfBytes"])
        return {
            "Plaintext": plaintext,
            "CiphertextBlob": self._seal(arn, plaintext, context),
            "KeyId": arn,
        }

    async def decrypt(self, **request: Any) -> dict[str, Any]:
        key_id, context = request["KeyId"], request["EncryptionContext"]
        self.calls.append(("decrypt", key_id, context))
        return {
            "Plaintext": self._open(request["CiphertextBlob"], key_id, context),
            "KeyId": self.resolve(key_id),
        }

    async def re_encrypt(self, **request: Any) -> dict[str, Any]:
        source, context = request["SourceKeyId"], request["SourceEncryptionContext"]
        self.calls.append(("re_encrypt", source, context))
        plaintext = self._open(request["CiphertextBlob"], source, context)
        target = self.resolve(request["DestinationKeyId"])
        return {
            "CiphertextBlob": self._seal(
                target, plaintext, request["DestinationEncryptionContext"]
            ),
            "KeyId": target,
            "SourceKeyId": self.resolve(source),
        }


class FakeSession:
    def __init__(self, client: Any) -> None:
        self.stub = client

    def client(self, *args: Any, **kwargs: Any) -> Any:
        @asynccontextmanager
        async def open_client() -> AsyncIterator[Any]:
            yield self.stub

        return open_client()


async def kms(stub: Any, key_id: str = "alias/sessions-{org_id}") -> KeyServiceKmsImpl:
    impl = KeyServiceKmsImpl(
        FakeSession(stub),  # type: ignore[arg-type]
        region="us-east-1",
        key_id=key_id,
        timeout=timedelta(seconds=1),
    )
    await impl.start()
    return impl


class TestKeyServiceMemory(KeyServiceContract):
    @pytest.fixture
    def twin(self) -> KeyServiceMemoryImpl:
        return KeyServiceMemoryImpl()

    @pytest.fixture
    def keys(self, twin: KeyServiceMemoryImpl) -> KeyServiceInterface:
        return twin

    @pytest.fixture
    def rotate(self, twin: KeyServiceMemoryImpl) -> Rotate:
        async def step(org_id: UUID) -> None:
            twin.rotate(org_id)

        return step


class TestKeyServiceKms(KeyServiceContract):
    @pytest.fixture
    def stub(self) -> StubKms:
        return StubKms()

    @pytest.fixture
    async def keys(self, stub: StubKms) -> KeyServiceInterface:
        return await kms(stub)

    @pytest.fixture
    def rotate(self, stub: StubKms) -> Rotate:
        async def step(org_id: UUID) -> None:
            stub.rotate(f"alias/sessions-{org_id}")

        return step


async def test_the_memory_twin_unwraps_after_a_restart_with_its_root() -> None:
    org, key = new_id(), new_id()
    data = await KeyServiceMemoryImpl(root_key(ROOT)).generate(org, key, 1)
    assert await KeyServiceMemoryImpl(root_key(ROOT)).unwrap(org, key, 1, data.wrapped) == (
        data.plaintext
    )
    with pytest.raises(KeyRefused):
        await KeyServiceMemoryImpl().unwrap(org, key, 1, data.wrapped)


def test_a_root_key_of_another_shape_refuses_the_boot() -> None:
    for wrong in ("", "not base64!", "c2hvcnQ="):
        with pytest.raises(ValueError, match="32 bytes"):
            root_key(wrong)


async def test_kms_binds_each_call_to_the_tenants_key_and_the_ids_alone() -> None:
    """A new key is made under the key the alias names, and the copy records
    that key's ARN; it is opened and re-wrapped from under that ARN. Every
    call carries an encryption context of ids, the tenant, the key, and the
    version, and nothing else reaches KMS's own audit trail."""
    stub = StubKms()
    keys = await kms(stub)
    org, key = new_id(), new_id()
    data = await keys.generate(org, key, 3)
    arn = stub.aliases[f"alias/sessions-{org}"]
    assert data.wrapped.wrapping == arn
    await keys.unwrap(org, key, 3, data.wrapped)
    await keys.rewrap(org, key, 3, data.wrapped)
    context = {"org": str(org), "key": str(key), "version": "3"}
    assert stub.calls == [
        ("generate_data_key", f"alias/sessions-{org}", context),
        ("decrypt", arn, context),
        ("re_encrypt", arn, context),
    ]


async def test_kms_opens_a_copy_under_its_own_key_after_the_alias_names_another() -> None:
    """The alias moves to a new key, as it does when the key is made again:
    a copy wrapped before opens under the key it records, a rewrap moves it
    onto the key the alias names now, and a new key is made under that one."""
    stub = StubKms()
    keys = await kms(stub, key_id="alias/sessions")
    org, key = new_id(), new_id()
    before = await keys.generate(org, key, 1)
    first = stub.aliases["alias/sessions"]
    second = stub.repoint("alias/sessions")
    assert before.wrapped.wrapping == first != second
    assert await keys.unwrap(org, key, 1, before.wrapped) == before.plaintext
    moved = await keys.rewrap(org, key, 1, before.wrapped)
    assert moved.wrapping == second
    assert await keys.unwrap(org, key, 1, moved) == before.plaintext
    after = await keys.generate(org, key, 2)
    assert after.wrapped.wrapping == second
    assert await keys.unwrap(org, key, 2, after.wrapped) == after.plaintext


async def test_kms_with_one_key_keeps_tenants_apart_by_their_context() -> None:
    stub = StubKms()
    keys = await kms(stub, key_id="alias/sessions")
    org, key = new_id(), new_id()
    data = await keys.generate(org, key, 1)
    with pytest.raises(KeyRefused):
        await keys.unwrap(new_id(), key, 1, data.wrapped)
    assert await keys.unwrap(org, key, 1, data.wrapped) == data.plaintext


async def test_any_other_kms_answer_is_a_backend_failure() -> None:
    class Denied(StubKms):
        async def decrypt(self, **request: Any) -> dict[str, Any]:
            raise client_error("AccessDeniedException")

    keys = await kms(Denied())
    org, key = new_id(), new_id()
    data = await keys.generate(org, key, 1)
    with pytest.raises(BackendFailed):
        await keys.unwrap(org, key, 1, data.wrapped)
