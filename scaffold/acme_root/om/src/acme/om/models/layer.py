"""What a layer above the engine sets of its models: the client each call
runs on. A root takes one layer, or none and keeps the engine's own: the
platform's key."""

from collections.abc import Callable
from dataclasses import dataclass

from acme.integrations.model_providers import ModelProvidersInterface
from acme.om.models.credentials import CallCredentialsInterface


@dataclass(frozen=True)
class ModelsLayer:
    """`credentials` builds the client each call runs on over the providers'
    registry, which holds the platform's key."""

    credentials: Callable[[ModelProvidersInterface], CallCredentialsInterface]
