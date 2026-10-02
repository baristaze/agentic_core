# Infrastructure

The capabilities the platform asks for and never implements itself:
cache, buckets, topics, queues, secrets, keys, observability, and where an
agent's tools run: workspaces and the transport. Each is an
interface with a twin that runs on a laptop and an implementation that
runs in the cloud. The object model sees the interface alone, and
infra imports nothing from the object model.

## The capabilities

| Capability | What it promises | Locally | In the cloud |
|------------|------------------|---------|--------------|
| Cache | Named scopes, so unrelated consumers never share a key; one atomic windowed counter for rate limits; fails open | In-process, or Valkey | Valkey (ElastiCache) |
| Buckets | Blobs under the tenant's prefix (`user-file-uploads`, `exports`): put, get, exists, list, delete, a presigned download, and a presigned form upload bounded by type and size | A folder, or MinIO | S3, one private versioned bucket each |
| Topics | Wake-ups and live updates (`work_available`, `entity_changed`), best effort; a publish answers whether the bus took it | In-process, or Valkey pub/sub | Valkey pub/sub |
| Queues | Work whose producer is outside the platform: `webhooks`, what a provider sends; at least once, so the consumer is idempotent | In-process, or ElasticMQ | SQS, with a dead-letter queue |
| Secrets | Get, has, put, and delete by name; the object model holds a name, never a value | The settings, from `.env` and the environment | Secrets Manager |
| Keys | Make, unwrap, and re-wrap a data key bound to its tenant, its key, and its version; keeps no copy | In-process, derived from a root key | KMS, under the account's key |
| Observability | Structured logs, Prometheus metrics, OpenTelemetry traces, error reports | Prometheus, Grafana, Jaeger, GlitchTip | CloudWatch, X-Ray, a Sentry-compatible backend |
| Workspaces | Prepare, release, and purge the place an agent works, to an isolation spec (a mode, an egress policy, limits); a spec the provider cannot meet is refused, never weakened | A directory on this host, a container on the local Docker, or the twin | The same, chosen by `ACME_WORKSPACE_BACKEND` |
| Transport | Run a command in a workspace, streamed, and read, write, and list its files; its whole process tree ends at its deadline; a secret is brokered, or injected into the one process and redacted from all it prints ([ADR 1003](../docs/adr/1003-a-secret-that-cannot-be-brokered-is-injected-into-one-process.md)) | This process, `docker exec`, or the twin | The same |

The environment name decides what a process may use: `local` and `test`
may use the in-process and compose backends, and every other
environment refuses them at boot.

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
