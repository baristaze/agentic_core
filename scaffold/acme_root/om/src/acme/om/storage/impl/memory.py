"""The in-memory storage root: the default for unit tests and the fast gate."""

from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.storage.impl.memory import AgentSessionStorageMemoryImpl
from acme.om.events.storage import EventStorageInterface
from acme.om.events.storage.impl.memory import EventStorageMemoryImpl
from acme.om.idempotency.storage import IdempotencyStorageInterface
from acme.om.idempotency.storage.impl.memory import IdempotencyStorageMemoryImpl
from acme.om.media.storage import MediaStorageInterface
from acme.om.media.storage.impl.memory import MediaStorageMemoryImpl
from acme.om.models.storage import FillSetStorageInterface
from acme.om.models.storage.impl.memory import FillSetStorageMemoryImpl
from acme.om.orchestrations.storage import OrchestrationsStorageInterface
from acme.om.orchestrations.storage.impl.memory import OrchestrationsStorageMemoryImpl
from acme.om.outbox.storage import OutboxStorageInterface
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.steps.storage import StepStorageInterface
from acme.om.steps.storage.impl.memory import StepStorageMemoryImpl
from acme.om.storage.root import StorageInterface
from acme.om.tenancy.storage import TenancyStorageInterface
from acme.om.tenancy.storage.impl.memory import TenancyStorageMemoryImpl
from acme.om.windows.storage import WindowStorageInterface
from acme.om.windows.storage.impl.memory import WindowStorageMemoryImpl
from acme.om.work.storage import WorkStorageInterface
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl


class StorageMemoryImpl(StorageInterface):
    def __init__(self) -> None:
        # The outbox and the markers first: the core-role impls land their
        # outbox rows in the one and fence a re-mint on the other, which is
        # how each impl gets what its Postgres twin reads in its own statement.
        self._outbox = OutboxStorageMemoryImpl()
        self._idempotency = IdempotencyStorageMemoryImpl()
        self._tenancy = TenancyStorageMemoryImpl(self._outbox, self._idempotency)
        self._work = WorkStorageMemoryImpl()
        self._orchestrations = OrchestrationsStorageMemoryImpl(self._outbox)
        self._media = MediaStorageMemoryImpl(self._outbox)
        self._events = EventStorageMemoryImpl()
        self._steps = StepStorageMemoryImpl()
        self._agent_sessions = AgentSessionStorageMemoryImpl(self._outbox)
        self._fill_sets = FillSetStorageMemoryImpl()
        self._windows = WindowStorageMemoryImpl()

    def get_tenancy_storage(self) -> TenancyStorageInterface:
        return self._tenancy

    def get_work_storage(self) -> WorkStorageInterface:
        return self._work

    def get_media_storage(self) -> MediaStorageInterface:
        return self._media

    def get_idempotency_storage(self) -> IdempotencyStorageInterface:
        return self._idempotency

    def get_event_storage(self) -> EventStorageInterface:
        return self._events

    def get_outbox_storage(self) -> OutboxStorageInterface:
        return self._outbox

    def get_orchestrations_storage(self) -> OrchestrationsStorageInterface:
        return self._orchestrations

    def get_step_storage(self) -> StepStorageInterface:
        return self._steps

    def get_agent_session_storage(self) -> AgentSessionStorageInterface:
        return self._agent_sessions

    def get_fill_set_storage(self) -> FillSetStorageInterface:
        return self._fill_sets

    def get_window_storage(self) -> WindowStorageInterface:
        return self._windows

    async def healthcheck(self) -> bool:
        return True

    async def close(self) -> None:
        return None
