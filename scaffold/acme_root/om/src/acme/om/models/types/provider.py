"""The provider boundary's names, as the models swimlane holds them: error
kinds and the engine's answer to each, stop reasons, and usage in disjoint
classes. They are defined with the adapters, beneath the object model, so
an adapter speaks them without importing it (ADR 1005)."""

from acme.integrations.model_providers.types import ANSWERS as ANSWERS
from acme.integrations.model_providers.types import Effort as Effort
from acme.integrations.model_providers.types import ErrorAnswer as ErrorAnswer
from acme.integrations.model_providers.types import ErrorKind as ErrorKind
from acme.integrations.model_providers.types import ProviderName as ProviderName
from acme.integrations.model_providers.types import StopReason as StopReason
from acme.integrations.model_providers.types import Usage as Usage
