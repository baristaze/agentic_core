from datetime import datetime

from sqlalchemy import Index, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class TenantTitleKeys(IdentifiableMixin, CreatedMixin, Base):
    """One row per tenant: the key its sessions' title keys are wrapped
    under, wrapped by the key service. A rotation rewrites the wrapped copy
    and nothing it wraps."""

    __tablename__ = "tenant_title_keys"
    # A tenant's one key is found by the tenant: org_id is the unique index,
    # so it gets none of its own.
    __org_id_index__ = False
    __table_args__ = (Index("uq_tenant_title_keys_org_id", "org_id", unique=True),)
    wrapped: Mapped[bytes] = mapped_column(LargeBinary)
    wrapping: Mapped[str]
    wrapped_at: Mapped[datetime]
