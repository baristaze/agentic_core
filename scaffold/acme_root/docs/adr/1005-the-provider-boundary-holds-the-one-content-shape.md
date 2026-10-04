# ADR 1005: The provider boundary holds the one content shape

**Status**: accepted (2026-10-02)

## Context

The engine has one content shape: text, image, document, thinking, tool
use, and tool result. A step's content is made of it, and each provider
adapter translates it both ways. There is no second content type.

The adapters are clients of hosted services, so they live under
`integrations/`. The object model imports the integrations, since the
tenancy namespace calls the identity provider, and nothing under
`integrations/` imports the object model. An adapter that read the
steps namespace's blocks would tie the two packages into a cycle; one
with blocks of its own would be a second content type.

A provider's SDK retries on its own, and the engine owns the retry
policy: it cannot see what an SDK swallowed. A provider's thinking
carries a signature that only the model that thought it can read.

## Decision

**The blocks live at the boundary.** The content blocks, the names the
boundary speaks in (provider, effort, stop reason, error kind and its
answer, usage), the call, and the reply are defined in
`integrations/model_providers/`. A step's content takes its blocks from
there, and the `steps` and `models` namespaces name them again under
their own modules, so a caller reads them where the spec places them.
The frozen mapping field a tool's input holds lives in infra's base,
which both packages read, and the object model's base names it again.

**An adapter speaks HTTP.** Each adapter calls its provider's API with
the HTTP client the integrations already pin, retries nothing, and
hands a failure to the engine with its kind and the provider's wait.
The wire shapes it translates are pinned by payloads recorded from a
real call.

**Thinking replays to the model that thought it.** A thinking block
names its provider and model, and replays only to that exact model, with
its signature. A request to any other model drops it and names the drop.

## Consequences

- One content shape runs from a provider's events to a stored step, and
  back to the next request.
- A second provider is a second module under `model_providers/`, with
  recorded payloads and the scripted provider as its twin; nothing in
  the object model changes.
- A provider's new block, stop reason, or event is named and dropped,
  or recorded as truncated, until its adapter learns it.
- A switch between two models of one provider drops the thinking even
  where the provider says the next model could read it: losing a summary
  of reasoning costs less than a request the next model refuses.
