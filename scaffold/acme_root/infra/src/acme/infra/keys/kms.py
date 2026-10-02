from datetime import timedelta
from typing import Any
from uuid import UUID

import aioboto3

from acme.infra.aws_clients import AwsClientHolder, client_config
from acme.infra.aws_errors import ClientError, error_code, translated
from acme.infra.keys import DATA_KEY_BYTES, DataKey, KeyRefused, KeyServiceInterface, WrappedKey

REFUSED = frozenset({"InvalidCiphertextException", "IncorrectKeyException"})
"""What KMS answers for a blob that is not this key's under this context:
the name it was bound to differs, the tenant's key differs, or it was
altered."""


def encryption_context(org_id: UUID, key_id: UUID, version: int) -> dict[str, str]:
    """The name a wrapped key is bound to. KMS refuses a blob presented under
    any other, and records it beside every call in its own audit trail: ids,
    never content."""
    return {"org": str(org_id), "key": str(key_id), "version": str(version)}


class KeyServiceKmsImpl(KeyServiceInterface):
    """AWS KMS. One client, opened at start() and closed at close(). The
    tenant's wrapping key is the KMS key `key_id` names, with `{org_id}` in
    it when each tenant has a key of its own; one key for every tenant keeps
    them apart by the encryption context. KMS rotates the key's material on
    its own schedule, and `rewrap` (ReEncrypt) moves a data key onto the
    newest material inside KMS.

    The permission to use the key is the process's, granted on the key to
    the task roles alone: a database login, or a role that reads the
    database, holds the wrapped keys and cannot unwrap one."""

    def __init__(
        self,
        session: aioboto3.Session,
        *,
        region: str,
        key_id: str,
        timeout: timedelta,
    ) -> None:
        self._region = region
        self._key_id = key_id
        config = client_config(timeout)
        self._holder = AwsClientHolder(
            "kms",
            lambda: session.client("kms", region_name=region, config=config),
        )

    def _client(self) -> Any:
        return self._holder.client()

    def _key(self, org_id: UUID) -> str:
        return self._key_id.replace("{org_id}", str(org_id))

    async def generate(self, org_id: UUID, key_id: UUID, version: int) -> DataKey:
        with translated("kms", "generate"):
            response = await self._client().generate_data_key(
                KeyId=self._key(org_id),
                NumberOfBytes=DATA_KEY_BYTES,
                EncryptionContext=encryption_context(org_id, key_id, version),
            )
        return DataKey(
            plaintext=response["Plaintext"],
            wrapped=WrappedKey(blob=response["CiphertextBlob"], wrapping=response["KeyId"]),
        )

    async def unwrap(self, org_id: UUID, key_id: UUID, version: int, wrapped: WrappedKey) -> bytes:
        with translated("kms", "unwrap"):
            try:
                response = await self._client().decrypt(
                    CiphertextBlob=wrapped.blob,
                    KeyId=self._key(org_id),
                    EncryptionContext=encryption_context(org_id, key_id, version),
                )
            except ClientError as error:
                if error_code(error) in REFUSED:
                    raise KeyRefused(
                        f"version {version} of key {key_id} does not unwrap here"
                    ) from None
                raise
        return response["Plaintext"]

    async def rewrap(
        self, org_id: UUID, key_id: UUID, version: int, wrapped: WrappedKey
    ) -> WrappedKey:
        context = encryption_context(org_id, key_id, version)
        with translated("kms", "rewrap"):
            try:
                response = await self._client().re_encrypt(
                    CiphertextBlob=wrapped.blob,
                    SourceKeyId=self._key(org_id),
                    SourceEncryptionContext=context,
                    DestinationKeyId=self._key(org_id),
                    DestinationEncryptionContext=context,
                )
            except ClientError as error:
                if error_code(error) in REFUSED:
                    raise KeyRefused(
                        f"version {version} of key {key_id} does not unwrap here"
                    ) from None
                raise
        return WrappedKey(blob=response["CiphertextBlob"], wrapping=response["KeyId"])

    def describe(self) -> str:
        return f"keys=kms({self._region})"

    async def start(self) -> None:
        await self._holder.open()

    async def close(self) -> None:
        await self._holder.close()
