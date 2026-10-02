"""The key service contract over the memory twin, and over the KMS impl
against a stubbed client that wraps the way KMS does: under a key's current
material, bound to the encryption context, refusing a blob presented under
any other context or key, or altered. And what each impl holds beyond the
contract."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from botocore.exceptions import ClientError
from contracts.key_service import KeyServiceContract, Rotate
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from acme.infra.base import new_id
from acme.infra.exceptions import BackendFailed
from acme.infra.keys import KeyRefused, KeyServiceInterface
from acme.infra.keys.kms import KeyServiceKmsImpl
from acme.infra.keys.memory import KeyServiceMemoryImpl, root_key

ROOT = "bG9jYWwtb25seS1rZXlzLXJvb3Qtbm90LXNlY3JldCE="


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "from the driver"}}, "Op")


class StubKms:
    """KMS in a dict: each key a list of materials, the last one current. A
    blob names its key and material and seals the data key under it, with
    the encryption context as the associated data, as KMS binds it."""

    def __init__(self) -> None:
        self.materials: dict[str, list[bytes]] = {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def rotate(self, key_id: str) -> None:
        self.materials.setdefault(key_id, [os.urandom(32)]).append(os.urandom(32))

    @staticmethod
    def _bound(context: dict[str, str]) -> bytes:
        return repr(sorted(context.items())).encode()

    def _seal(self, key_id: str, plaintext: bytes, context: dict[str, str]) -> bytes:
        materials = self.materials.setdefault(key_id, [os.urandom(32)])
        nonce = os.urandom(12)
        sealed = AESGCM(materials[-1]).encrypt(nonce, plaintext, self._bound(context))
        head = f"{key_id}|{len(materials) - 1}|".encode()
        return head + nonce + sealed

    def _open(self, blob: bytes, key_id: str, context: dict[str, str]) -> bytes:
        named, material, rest = blob.split(b"|", 2)
        if named.decode() != key_id:
            raise client_error("IncorrectKeyException")
        try:
            return AESGCM(self.materials[key_id][int(material)]).decrypt(
                rest[:12], rest[12:], self._bound(context)
            )
        except InvalidTag, KeyError, IndexError:
            raise client_error("InvalidCiphertextException") from None

    async def generate_data_key(self, **request: Any) -> dict[str, Any]:
        key_id, context = request["KeyId"], request["EncryptionContext"]
        self.calls.append(("generate_data_key", key_id, context))
        plaintext = os.urandom(request["NumberOfBytes"])
        return {
            "Plaintext": plaintext,
            "CiphertextBlob": self._seal(key_id, plaintext, context),
            "KeyId": f"arn:kms:{key_id}",
        }

    async def decrypt(self, **request: Any) -> dict[str, Any]:
        key_id, context = request["KeyId"], request["EncryptionContext"]
        self.calls.append(("decrypt", key_id, context))
        return {
            "Plaintext": self._open(request["CiphertextBlob"], key_id, context),
            "KeyId": f"arn:kms:{key_id}",
        }

    async def re_encrypt(self, **request: Any) -> dict[str, Any]:
        source, context = request["SourceKeyId"], request["SourceEncryptionContext"]
        self.calls.append(("re_encrypt", source, context))
        plaintext = self._open(request["CiphertextBlob"], source, context)
        target = request["DestinationKeyId"]
        return {
            "CiphertextBlob": self._seal(target, plaintext, request["DestinationEncryptionContext"]),
            "KeyId": f"arn:kms:{target}",
            "SourceKeyId": f"arn:kms:{source}",
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
    """Every call names the tenant's key and an encryption context of ids:
    the tenant, the key, and the version. Nothing else reaches KMS's own
    audit trail."""
    stub = StubKms()
    keys = await kms(stub)
    org, key = new_id(), new_id()
    data = await keys.generate(org, key, 3)
    await keys.unwrap(org, key, 3, data.wrapped)
    await keys.rewrap(org, key, 3, data.wrapped)
    context = {"org": str(org), "key": str(key), "version": "3"}
    assert stub.calls == [
        (call, f"alias/sessions-{org}", context)
        for call in ("generate_data_key", "decrypt", "re_encrypt")
    ]
    assert data.wrapped.wrapping == f"arn:kms:alias/sessions-{org}"


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
