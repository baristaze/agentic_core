"""A toolset: a named, versioned group of tools with a one-line summary. A
kind names its toolsets rather than every tool one by one, and its
registry is their union, rendered in the registry's fixed order, so a kind
built from toolsets renders the bytes the same tools listed alone do.

A kind may defer a toolset: its tools stay out of the registry, and out of
every request, until the agent loads it through `load_toolset`, whose
description names it in one line. A load is a step in the session's
history (a `switched` step), so a replay renders the same requests, and a
loaded toolset stays loaded for the session's life (ADR 1031)."""

from typing import Self

from pydantic import Field, model_validator

from acme.om.base import OmBase

LOAD_TOOLSET = "load_toolset"
"""The engine's tool that loads a deferred toolset. A kind that defers one
holds it."""

TOOLSET_NAME = r"^[a-z][a-z0-9_]{0,63}$"
SUMMARY_CHARS = 200


class Toolset(OmBase):
    """A group of tools, by name, at a version. Its summary is the one line
    `load_toolset`'s description gives it when a kind defers it."""

    name: str = Field(pattern=TOOLSET_NAME)
    version: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=SUMMARY_CHARS)
    tools: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_line_and_each_tool_once(self) -> Self:
        if "\n" in self.summary or "\r" in self.summary:
            raise ValueError("a toolset's summary is one line")
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("a toolset names each tool once")
        if LOAD_TOOLSET in self.tools:
            raise ValueError(f"no toolset holds {LOAD_TOOLSET}")
        return self
