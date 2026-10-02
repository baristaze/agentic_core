---
name: agentic-scaffold-provider-adapter
description: "Add a model provider's adapter under integrations/: the one content shape translated both ways, what does not survive named, its errors read into kinds, no retries of its own, the scripted twin standing in for it, its settings, and tests over recorded payloads, in the shape of the engine's two adapters. Python."
allowed-tools: Read, Grep, Glob, Write, Edit, WebFetch, Bash(make check), Bash(uv run:*), Bash(git status:*)
---

# agentic-scaffold-provider-adapter

A path that starts with `../` is read from this skill's folder as
`realpath` resolves it.
Conventions: `../_shared/scaffold-conventions.md`.
Sections of `../../agentic_core_spec.md`: Models (The Provider Boundary,
Provider Errors), Bounds and Budgets (Usage and Cost, The Tenant's Own
Key), Testing and Conformance.
Lenses: `../../lenses/models.md`.

## Input

`<provider> [--base-url <url>]`, and the provider's API reference: a
link or its text, in the arguments or in the conversation.

Example: `mistral --base-url https://api.mistral.ai`.

- `<provider>` is the provider's name in snake case, the value of its
  `ProviderName`. `<PROVIDER>` is the member and the environment's
  word, and `<Provider>` the CamelCase.
- Without a reference, read the provider's own documentation of its
  streamed chat or responses API, its tool use, its usage, and its
  errors.

## Created

The shape is `anthropic.py` or `openai.py` under
`integrations/src/<name>/integrations/model_providers/`, whichever API
the provider's is closer to, and its test.

| File | Holds |
|------|-------|
| `integrations/src/<name>/integrations/model_providers/<provider>.py` | `ModelProvider<Provider>Impl(ModelProviderInterface)`, over the pure pieces its sibling has: `request_body`, the reply that turns the provider's events into stream parts and a reply, and `classify` |
| `integrations/tests/test_model_providers_<provider>.py` | the cases of step 4 |
| `integrations/tests/fixtures/model_providers/<provider>_*.sse`, `<provider>_errors.json` | the recorded payloads the tests read |

## Changed

| File | Change |
|------|--------|
| `integrations/src/<name>/integrations/model_providers/types.py` | `<PROVIDER>` in `ProviderName` |
| `integrations/src/<name>/integrations/impl/configured.py` | the adapter in the `live` registry of `model_providers_for` |
| `integrations/src/<name>/integrations/settings.py` | `<provider>_api_key`, in the validator that reads empty or `off` as none, and `<provider>_base_url` |
| `.env.example` | both settings under the model providers, as the others are |
| `services/api/tests/test_settings.py`, `workers/maintenance/tests/test_settings.py` | both settings, with the reason the others give |
| `integrations/tests/test_configured.py` | the live registry holds the adapter, and its key stays out of the settings' `repr` |
| `integrations/src/<name>/integrations/model_providers/__init__.py`, `integrations/README.md` | the adapter beside the others |

## Procedure

1. The adapter translates the one content shape both ways, and nothing
   of the provider's own types leaves it. What does not survive is
   named as `Dropped`: thinking replays only to the model that thought
   it, with its signature, and a cache marker means nothing to a
   provider that caches on its own. A stop reason the provider adds
   later maps to none, so its reply is recorded as truncated.
2. `classify` reads a failure's kind from its message as well as its
   status. The adapter retries nothing, and its client keeps no retries
   of its own. A call's credential, when it carries one, is used in
   place of the platform's key.
3. The scripted twin and the absent provider stand in for every
   `ProviderName`, so the new member has both once it is added. The
   `live` registry refuses a provider with no adapter.
4. The tests read recorded payloads and reach no network: a real
   streamed response with tool use, recorded once by a person with a
   key, or the provider's documented example where none was recorded;
   each error the provider documents, read into its kind; usage in
   disjoint classes; a request translated both ways, thinking from
   another provider dropped and named; and a credential on the call
   used in place of the platform's key.
5. A model of this provider reaches a call only through a fill, and a
   fill's model needs a price row: `agentic-scaffold-model-role`.

Then the gate, `make check`, as After writing in the conventions
runs it.

## Output

As `../_shared/scaffold-conventions.md` states.
