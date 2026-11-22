"""The agent_steps namespace's types: what kind a step of a history is, who
produced it, and how a loop ended."""

from acme.client.schema.agent_steps import (
    Actor,
    AgentStepType,
    LoopOutcome,
)

__all__ = [
    "Actor",
    "AgentStepType",
    "LoopOutcome",
]
