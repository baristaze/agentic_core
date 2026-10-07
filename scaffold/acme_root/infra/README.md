# Infrastructure

The capabilities the platform asks for and never implements itself:
cache, buckets, topics, queues, secrets, keys, the outage signal,
observability, and where an agent's tools run: workspaces and the
transport. Each is an
interface with a twin that runs on a laptop and an implementation that
runs in the cloud. The object model sees the interface alone, and
infra imports nothing from the object model.

## The capabilities

| Capability | What it promises | Locally | In the cloud |
|------------|------------------|---------|--------------|
| Cache | Named scopes, so unrelated consumers never share a key; one atomic windowed counter for rate limits; fails open | In-process, or Valkey | Valkey (ElastiCache) |
| Buckets | Blobs under the tenant's prefix (`user-file-uploads`, `exports`, `artifacts`): put, get, exists, list, delete, a presigned download, and a presigned form upload bounded by type and size | A folder, or MinIO | S3, one private versioned bucket each |
| Topics | Wake-ups and live updates (`work_available`, `entity_changed`), best effort; a publish answers whether the bus took it | In-process, or Valkey pub/sub | Valkey pub/sub |
| Queues | Work whose producer is outside the platform: `webhooks`, what a provider sends; at least once, so the consumer is idempotent | In-process, or ElasticMQ | SQS, with a dead-letter queue |
| Secrets | Get, has, put, and delete by name; the object model holds a name, never a value | The settings, from `.env` and the environment | Secrets Manager |
| Keys | Make, unwrap, and re-wrap a data key bound to its tenant, its key, and its version; keeps no copy | In-process, derived from a root key | KMS, under the account's key |
| Outage signal | A provider known to be failing for one credential, until its retry time; keyed by provider and credential; fails open | On the in-process cache, or on Valkey | On Valkey, shared by the fleet |
| Observability | Structured logs, Prometheus metrics, OpenTelemetry traces, error reports | Prometheus, Grafana, Jaeger, GlitchTip | CloudWatch, X-Ray, a Sentry-compatible backend |
| Workspaces | Prepare, release, and purge the place an agent works, to an isolation spec (a mode, an egress policy, limits); a spec the provider cannot meet is refused, never weakened | A directory on this host, one whose commands run as an account of the host ([ADR 1021](../docs/adr/1021-a-host-directory-runs-its-commands-as-an-account-of-its-own-one-workspace-at-a-time.md)), a container on the local Docker, or the twin | A container per workspace, or none; a directory on the host, as an account or not, is refused at boot |
| Transport | Run a command in a workspace, streamed, and read, write, and list its files; its whole process tree ends at its deadline; a secret is brokered, or injected into the one process and redacted from all it prints ([ADR 1003](../docs/adr/1003-a-secret-that-cannot-be-brokered-is-injected-into-one-process.md)); how each command ended is recorded beside the workspaces, its output sealed under its session's key by the seal the command comes with | This process, as itself or as an account, `docker exec`, or the twin | `docker exec` |

The environment name decides what a process may use: `local` and `test`
may use the in-process and compose backends, and every other
environment refuses them at boot.

## The outage signal

A provider that fails fast costs a session its retries before it parks.
The outage signal spares the others: the first session to learn of an
outage reports it, with its retry time, and every session that would call
the same provider on the same credential parks at once until then. The
key is the provider and the credential together, so one tenant's broken
key is no outage of the platform's own.

The signal lives on the shared cache, in a scope of its own. In one
process it is the in-process cache, and a fleet shares it on Valkey; it
follows the cache backend, so a deployed process, which refuses the
in-process cache, always shares it. Its null never signals: one process
needs none, since its sessions learn of an outage from the provider's
own errors. A cache that cannot be reached is a miss, so the signal
fails open, and those errors still park.

## What every capability holds to

- **A lifecycle.** The infra root opens every capability at boot and
  closes it at shutdown. Nothing opens a client per call.
- **A timeout on every client**, from settings. A test fails on a
  client built without one.
- **A request's deadline on the calls a request makes.** A queue send,
  a secret call, and an object call take the request's `deadline`, and
  the AWS impl cuts the call there
  ([ADR 0069](../docs/adr/0069-a-request-has-a-deadline-its-provider-calls-share.md)).
- **One exception family**, rooted at `InfraException`, with the status
  and code the platform's own exceptions carry
  ([ADR 0005](../docs/adr/0005-infra-exception-root.md)).
- **One breaker in front of Valkey.** A run of calls that spend their
  whole timeout opens it. While it is open, a read is a miss, a write
  and a publish are dropped, and a counter answers no count.
- **Payloads that only grow.** A topic payload gains only optional
  fields, so the two sides of a deploy roll out in either order.
- **Counted outcomes**, on the one outcome counter, each with a log
  line.

## How the object model composes it

A manager receives the interfaces it needs from the root at boot and
passes the org's id on every tenant call, so keys, prefixes, and
secrets of one org never meet another's. `EMPTY_UUID` names the
platform's own. A new bucket, queue, topic, or cache scope is one member
of its enum, and the cloud's resource is Terraform's.
