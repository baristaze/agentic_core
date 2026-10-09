# Models

Group id: `models`. Covers Models of `agentic_core_spec.md`.

This group judges how the engine reaches a model: model roles and fills,
fill sets and switches, the provider boundary, provider errors, and the
scripted provider that stands in for every provider in a test. It leaves
the window a switch resizes to `windows`, the gate a call passes and the
cost it records to `bounds`, a park on a provider to `bounds`, and the
engine's half of zero data retention to `privacy`.

## MOD-01 A call names a model role, never a model

**Principle.** A call site names a task, never a model. That task is a
model role, `ModelRole` in code, never the guideline's `Role`. A fill
serves a model role: a provider, a model, an effort level, an output
bound, the output shape expected, the context window, and the
eligibility it carries, such as zero retention or a region. An injected
resolver turns model roles into fills. The engine never picks a model,
and an agent kind names only model roles.

**Source.** Models, Roles and Fills.

**Look for.** Every model call site and what it passes; the model roles
an agent kind names; where provider and model names appear outside the
resolver and its fills.

**Violation.** A call site or an agent kind that names a provider or a
model; the engine choosing a model per call, such as the cheapest; a
model role class named `Role`. (A call on a model the fill set does not
hold is MOD-02.)

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/models/types/fill.py` and
`scaffold/acme_root/om/src/acme/om/models/resolver.py`

**Check.** `agentic-check` decides that no string is written as a `model`
argument, key, field, or default outside the price table and the fills;
the rest is judged.

## MOD-02 A switch is explicit, never silent

**Principle.** A session resolves its fills once and keeps them as a
versioned fill set. A switch, on a failover to a declared fallback, a
retired or better model, or a policy, is always explicit: a new
fill-set version and a `switched` step naming both fills. The engine
re-sizes the window, compacts first if the new window is smaller, drops
thinking the new provider cannot replay, and pays the one cache write
knowingly. A switch lands where no tool-use cycle is open, or runs with
thinking off until the cycle closes.

**Source.** Models, Fill Sets and Switches.

**Look for.** Every path that changes the fill a call uses: a failover,
a retirement, a policy, `model_unavailable`; the fill-set version and the
step each writes; where a switch lands against an open tool-use cycle.

**Violation.** A fallback or a re-resolution that calls another model
with no new fill-set version or no `switched` step; fills resolved per
call instead of once per session; a switch into a smaller window with no
compaction first, or one inside an open tool-use cycle with thinking on.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/models/impl/manager.py` and
`scaffold/acme_root/om/src/acme/om/models/rules.py`

**Check.** review

## MOD-03 A fallback stays inside the session's eligibility

**Principle.** A fallback is drawn from the fill set's declared
fallbacks, filtered by the session's eligibility. With a tenant's own
key, resolution and fallback stay among the providers the tenant holds
keys for, and a session whose fill needs a key the tenant lacks parks
on the provider until one is saved: nothing starts silently on the
platform's key. A key the provider does not take at all is offered to no
call again, and no outage is marked for the provider. Under
zero data retention, only fills eligible for it may be resolved or
fallen back to, enforced like a tenant's own key.

**Source.** Models, Fill Sets and Switches; Bounds and Budgets, The
Tenant's Own Key; Privacy, Storage Modes and Retention.

**Look for.** The resolver's filter on eligibility; the fallback list
and how it is filtered; what happens when no eligible fill exists.

**Violation.** A fallback to an undeclared model, or to a provider the
tenant holds no key for; a call that runs on the platform's key when the
tenant's is missing; a refused key read as the provider's outage; a
session under zero data retention resolved to a
fill that retains.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/models/impl/resolver.py` and
`scaffold/acme_root/om/src/acme/om/models/rules.py`

**Check.** review

## MOD-04 One content shape, and each adapter translates both ways

**Principle.** One content shape runs through the engine. Each provider
adapter translates it both ways and names what does not survive
translation: thinking replays only to the provider and model family that
produced it, with its signature, and cache markers mean nothing to a
provider without caching. The client for a call is chosen per call, by
provider and credential. A second adapter that runs the whole loop
unchanged is the proof the boundary holds. Adapters are clients of
external services, under `integrations/`, each with the scripted
provider as its twin.

**Source.** Models, The Provider Boundary; The Object Model.

**Look for.** The adapters and what crosses them; how thinking and its
signature are replayed; where a call's client is built; a test that
runs the loop on a second adapter.

**Violation.** A provider's own types used past its adapter; thinking
replayed to another provider or family, or without its signature; a
client fixed for the process, so a call cannot take its own provider and
credential; an adapter outside `integrations/`, or one with no twin.

**Severity.** medium

**Shape.**
`scaffold/acme_root/integrations/src/acme/integrations/model_providers`

**Check.** review

## MOD-05 A response records its stop reason and is never assumed whole

**Principle.** A `model_response` records its stop reason: end of turn,
tool use, output limit, refusal, content filter, or a provider's pause.
A response cut by its output limit, or by a broken stream, is continued
or recorded as truncated, never treated as complete. A model's refusal
is a response its agent kind handles, not a provider error.

**Source.** Models, The Provider Boundary.

**Look for.** The stop reason on a response and where it is read; the
handling of an output limit and of a broken stream; the handling of a
refusal.

**Violation.** A response with no stop reason; a truncated response
treated as complete; a refusal raised as a provider error and retried.
(A truncated tool-use block executed is STP-11.)

**Severity.** medium

**Shape.**
`scaffold/acme_root/integrations/src/acme/integrations/model_providers`

**Check.** review

## MOD-06 A provider error has a kind, and each kind its answer

**Principle.** A provider error is the engine's to handle; a tool
failure is the model's to read. Each error has a kind, read from the
message as well as the status. `transient`, `overloaded`, and
`rate_limited` retry in process while that is cheaper than parking, then
fall back if a fallback is declared, else park on the provider with a
retry time. `context_overflow` compacts once and retries, and a second
ends the loop `errored`. `model_unavailable` re-resolves the model role
and switches. `billing` and `credential` park at once, naming the
unlock. `invalid_request` and `permanent` end the loop `errored`, with
the evidence.

**Source.** Models, Provider Errors.

**Look for.** The error classifier and what it reads; the handler of
each kind.

**Violation.** A kind read from the status code alone; a billing or a
credential error retried or waited out; a transient error that ends the
loop; a provider error handed to the model as a tool failure.

**Severity.** medium

**Shape.**
`scaffold/acme_root/integrations/src/acme/integrations/model_providers`

**Check.** review

## MOD-07 The engine owns retries, and a session parks on an outage at once

**Principle.** Retries follow the guideline's direction of calls, never
sooner than the provider's retry-after. A provider SDK keeps its own
retries minimal, because the engine owns the policy and cannot see what
an SDK swallows. The guideline's breaker guards a call that spends its
timeout, and its outage signal (CON-24) a provider that fails fast. The
loop is the signal's caller, keyed by the call's key: a tenant's own
under its org, the platform's under the system scope. It reads the mark
before a model call, marks it when its retries are spent, and clears it
when a call answers. A session that reads a mark parks on the provider
at once, with no call until the mark's retry time.

**Source.** Models, Provider Errors.

**Look for.** The SDK client's retry settings; the engine's retry delay
against the retry-after; where the loop reads, marks, and clears the
outage signal, and the org and the credential it names; what a session
that reads a mark does before its next call.

**Violation.** An SDK left at its default retries; a retry sooner than
the retry-after; an outage signal of the engine's own beside the
guideline's; a tenant's key marked under the system scope, or the
platform's under a tenant; a session that reads a mark and calls anyway,
or ends instead of parking; a call that answers and leaves the mark.

**Severity.** medium

**Shape.**
`scaffold/acme_root/integrations/src/acme/integrations/model_providers`
and `scaffold/acme_root/om/src/acme/om/agents/impl/loop.py`

**Check.** review

## MOD-08 The scripted provider stands in for every provider

**Principle.** The model provider's twin is a scripted provider: it
streams scripted responses with usage and raises every provider error
kind, so every suite runs offline and the same way twice.

**Source.** Testing and Conformance.

**Look for.** The scripted provider; the error kinds it can raise; any
test that reaches a real provider.

**Violation.** A scripted provider that cannot raise an error kind the
engine handles, or that streams a response with no usage; a test that
needs a network or a provider's key.

**Severity.** medium

**Shape.**
`scaffold/acme_root/integrations/src/acme/integrations/model_providers` and
`scaffold/acme_root/integrations/tests/test_model_providers_scripted.py`

**Check.** review
