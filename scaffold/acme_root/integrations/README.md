# Integrations

The third-party providers Acme talks to: the identity provider and the
model providers. Each is one interface with a real client and a twin,
and a caller never knows which it holds. Nothing here imports the object
model: a provider's errors root at infra's exception family, since a
provider is a dependency the way a backend is.

## The identity provider

`identity.IdentityProviderInterface` proves who a person is and holds
an org's side of it:

- the hosted sign-in, the code exchange, the device sign-in for the
  command line, and the logout address that ends the provider's session
  in the browser;
- an org's organization at the provider, its single sign-on link, and
  its invitations;
- the deletion of a deleted account's user and a deleted team org's
  organization;
- the check of an inbound delivery (`verify_delivery`).

The provider hands Acme an issuer, a subject, and a verified email.
Everything else (the identity row, its sessions, its memberships) is
Acme's own; [the tenancy namespace](../om/src/acme/om/tenancy/README.md)
says how a sign-in becomes a person.

| Implementation | What it is |
|----------------|------------|
| `identity/workos.py` | WorkOS AuthKit through the pinned `workos` SDK. At start it proves the key is the application's, and refuses to boot on any other ([ADR 0033](../docs/adr/0033-the-workos-key-is-the-applications.md)). |
| `identity/twin.py` | The provider in memory, for tests and CI. It signs its own deliveries with the provider's scheme. Every id it mints starts with `twin_`. |
| `identity/absent.py` | The provider of a process with none configured, a local stack with no key among them: every call is `ProviderUnavailable`, which the API answers as `503`. The local sign-in by address still works. |

## The webhook check

The provider delivers its events to `POST /webhooks/identity`. The
route takes no credential; the signature is the authentication.
`identity/deliveries.py` checks the `WorkOS-Signature` header, an
HMAC-SHA256 over `<timestamp>.<body>` under `ACME_WORKOS_WEBHOOK_SECRET`,
inside a three-minute window, in constant time. A delivery that fails
is `400 webhook_signature_invalid`, and nothing is queued. One that
passes is queued on `webhooks` with a key that is a UUID v5 over the
provider's event id, so a redelivery carries the same key. The
maintenance worker applies it.

## Settings

| Setting | What |
|---------|------|
| `ACME_IDENTITY_PROVIDER` | `workos`, `twin`, or `none` (the default). The twin is refused at boot outside `local` and `test`. |
| `ACME_WORKOS_CLIENT_ID` | The application's client id. Not a secret. |
| `ACME_WORKOS_API_KEY` | The application's own API key: the exchange's client secret and the key of every management call. Empty or `off` leaves WorkOS unconfigured. |
| `ACME_WORKOS_WEBHOOK_SECRET` | The webhook endpoint's signing secret, injected at start. Unset, every delivery is refused as unavailable. |
| `ACME_WORKOS_BASE_URL`, `ACME_WORKOS_TIMEOUT_SECONDS` | Where the client calls, and the timeout of every call. |

[The WorkOS runbook](../docs/runbooks/providers/workos.md) sets them up.

## The model providers

`model_providers.ModelProviderInterface` calls a hosted model, streamed.
One content shape crosses it both ways: the blocks a step's content is
made of, which live here, beneath the object model
([ADR 1005](../docs/adr/1005-the-provider-boundary-holds-the-one-content-shape.md)).
Each adapter:

- translates the shape into its provider's request and the provider's
  events back into it, and names what does not survive (`Dropped`):
  thinking another model thought, a cache marker the provider cannot
  read, a block of a kind the engine does not hold;
- keeps each thinking block's place in its turn, and replays a turn in
  the order the provider sent it;
- records why a response stopped, and marks one cut by its bound or by a
  broken stream as truncated, never whole;
- reads usage into disjoint classes: input, cache read, cache write,
  output, and thinking;
- reads a failure from its status and its message into a kind
  (`ErrorKind`), whose answer the engine owns;
- retries nothing.

The registry, `ModelProvidersInterface`, holds an adapter for every
provider. A call may carry its own credential, such as a tenant's key,
in place of the platform's.

| Implementation | What it is |
|----------------|------------|
| `model_providers/anthropic.py` | The Messages API over HTTP. |
| `model_providers/openai.py` | The Responses API over HTTP, stored nowhere at the provider, so a reasoning model's thinking comes back encrypted and replays to the model that thought it. |
| `model_providers/scripted.py` | The twin of every provider: each call takes the next turn of its script, a reply streamed in parts with its usage, or a failure of any kind, before the stream or part of the way through it. |
| `model_providers/absent.py` | The provider of a process that calls no model: every call fails as a missing credential. |

Settings: `ACME_MODEL_PROVIDERS` (`live`, `scripted`, or `none`, the
default; the scripted twin is refused at boot outside `local` and
`test`), `ACME_ANTHROPIC_API_KEY` and `ACME_OPENAI_API_KEY` (the
platform's keys; empty or `off` leaves a provider with none),
`ACME_ANTHROPIC_BASE_URL`, `ACME_OPENAI_BASE_URL`, and
`ACME_MODEL_TIMEOUT_SECONDS`.

## What every integration holds to

- **A timeout on every call**, from settings.
- **A request's deadline on every call a request makes**, shared by
  every call of the request; a worker's calls carry none
  ([ADR 0069](../docs/adr/0069-a-request-has-a-deadline-its-provider-calls-share.md)).
- **One exception family.** `ProviderUnavailable` is `503`,
  `ProviderRefused` `400`, `ProviderConflict` `409`. The key never
  appears in a message.
- **A refused key is unavailable; a refused request is refused.** Work
  parks on the first and fails for good on the second
  ([ADR 0051](../docs/adr/0051-a-refused-key-is-unavailable-a-refused-request-is-refused.md)).
- **A twin that says it is one**, and is refused in a deployed
  environment.

## What a provider that hangs costs a call

Each SDK keeps its own retries. WorkOS's SDK retries a timeout, so a
provider that takes a call and never answers holds it for the timeout
on every attempt, plus the waits between them. At the default timeout
of 10 seconds:

| Provider | Attempts | Waits between them, at most | A call gives up after, at most |
|----------|----------|-----------------------------|--------------------------------|
| WorkOS | 4 | 1.5, 3, and 6 seconds | 50.5 seconds |
| A model provider | 1 | none | 120 seconds of silence |

The timeout bounds each wait on the network (to connect, to send, and
between bytes of the answer), not an attempt as a whole. WorkOS also
waits as long as a `Retry-After` on a 429 or a server error asks, with
no cap of its own. A model adapter retries nothing and waits on no
`Retry-After`: it hands the wait to the engine with its failure. A
streamed answer runs as long as its parts keep coming, and a model call
is a worker's, with no request deadline.

That is what a call costs a worker. A request makes its calls in turn,
and they share its deadline, `ACME_REQUEST_DEADLINE_SECONDS` (20
seconds) from when it was admitted, so a provider that hangs costs a
request that and no more (ADR 0069):

- a call that starts with no time left does not start;
- a call still waiting at the deadline, on an attempt, on the wait
  before the next, or on a wait the provider asked for, is cut there;
- WorkOS sends each attempt with the smaller of the timeout and what is
  left, and a `Retry-After` longer than what is left ends the call at
  once instead of being slept;
- the call then raises what it raises when the provider does not
  answer: `ProviderUnavailable` (`503 unavailable`).
