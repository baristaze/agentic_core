"""The agent_steps namespace's types: what kind a step of a history is, and
how a loop ended."""

from acme.client.schema.agent_steps import (
    AgentStepType,
    LoopOutcome,
)

__all__ = [
    "AgentStepType",
    "LoopOutcome",
]
