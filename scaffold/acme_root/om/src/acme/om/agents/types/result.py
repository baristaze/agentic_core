"""What a turn means under a kind's done rule, and a result submitted
through a result tool with the verdict its gate gives."""

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.om.base import Platform
from acme.om.steps.types.header import LoopOutcome


class Turn(StrEnum):
    """What a model response means under its kind's done rule."""

    CALLS = "calls"  # it asked for tools: run them and go on
    ANSWERED = "answered"  # an assistant's answer: the loop is done
    SUBMITTED = "submitted"  # it called the result tool: the result passes the gate
    NUDGE = "nudge"  # a delivery turn with no call: nudge it to continue or submit
    EXHAUSTED = "exhausted"  # nudged too often: the loop ends inconclusive


class Claim(StrEnum):
    SUCCEEDED = "succeeded"  # the objective is met
    FAILED = "failed"  # the objective cannot be met as asked: a conclusion, not an error


class Result(Platform):
    """A result submitted through a kind's result tool: what it claims, and
    the steps it cites as evidence, such as the tool responses that show
    it."""

    claim: Claim
    evidence: tuple[UUID, ...] = ()


class Verdict(Platform):
    """The gate's answer. An accepted result ends the loop with the outcome
    it claims; a refused one goes back to the model with `reason`.
    `verified` is False where no gate that knows the evidence looked at it,
    so nothing downstream mistakes it for checked."""

    accepted: bool
    verified: bool = False
    reason: str | None = Field(default=None, min_length=1)
    outcome: LoopOutcome | None = None

    @model_validator(mode="after")
    def _a_refusal_says_why(self) -> Self:
        if self.accepted == (self.reason is not None):
            raise ValueError("a refused result says why, and an accepted one does not")
        if self.accepted != (self.outcome is not None):
            raise ValueError("an accepted result names its outcome, and a refused one does not")
        if self.verified and not self.accepted:
            raise ValueError("a refused result is not verified")
        return self
