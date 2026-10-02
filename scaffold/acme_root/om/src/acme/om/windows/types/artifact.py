"""An artifact: a tool result too large for a step, kept whole outside it.
The record is its shape; the text lives in the object store, under the
tenant's prefix, and a read answers it a page at a time."""

from uuid import UUID

from pydantic import Field

from acme.om.base import Created, Identifiable, Platform


class Artifact(Identifiable, Created):
    """The record of an artifact: the session it belongs to, the tool
    response it came from, and how many characters it holds. Its id is
    derived from that response, so keeping one response twice keeps one
    artifact."""

    session_id: UUID
    step_id: UUID
    characters: int = Field(gt=0)


class ArtifactPage(Platform):
    """The characters of the artifact from `offset`, and whether more follow.
    `characters` is the whole artifact's length."""

    artifact_id: UUID
    offset: int = Field(ge=0)
    text: str
    characters: int = Field(ge=0)
    has_more: bool
