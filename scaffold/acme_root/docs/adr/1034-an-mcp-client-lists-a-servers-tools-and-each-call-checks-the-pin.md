# ADR 1034: An MCP client lists a server's tools, and each call checks the pin

**Status**: accepted (2026-10-11)

## Context

The engine binds a server's tool through `McpBinding`, pinned by the
hash of the definition someone reviewed, and calls it through
`McpCall`. Nothing connected to a server: each adopter wrote its own
client. Most of a role's tools outside code come as MCP servers: a
cloud's read API, a tracker, a wiki.

A client decides what the binding cannot: when a changed definition is
caught, where the server's credential lives, what a server's answer
becomes, and what bounds a server that misbehaves.

## Decision

**The client.** `integrations/mcp` speaks the protocol through the
official SDK, pinned exactly, over stdio and streamable HTTP. It never
imports the object model. An exchange is one connection: the client
connects, lists the server's tools, and closes; or lists them, asks its
caller about the one it is about to call, calls it, and closes. No
connection outlives its call, so nothing is pooled, no server process
outlives its call, and a worker that dies holds nothing open.

**The pin, at the listing and at the call.** `McpServers.tools` lists
the servers a set of bindings names, once each, and builds a tool of
each binding whose definition hashes to its pin. When any moved, it
refuses the lot, naming each binding and why. That holds the registry
when a process starts. A server can change after that, so each call
lists the server's tools again on its own connection and refuses a
moved tool before it runs. The model reads the reason as a `permanent`
failure. The listing costs one request a call; the protocol's newest
version asks a client to list a tool before it calls it anyway.

**The credential.** A server names a tenant's secret, never a value.
`McpServers` resolves it at the call, under the call's tenant, through
the engine's secrets, and hands it to the client for the one exchange.
Over HTTP it goes in a header, and only over https or to this host.
Over stdio it goes in one variable of the server's environment, which
holds only what the server's configuration names. Everything the server
sends back, its listing, its answer, and its error's reason, is
redacted of the value, in every form the transports redact, before it
leaves the client. The client writes its errors in its own words, with
no cause attached, since the SDK's text quotes the server. The SDK's own
logger writes nothing (`SILENT_LOGGERS`), since it quotes what a server
sent, and a stdio server's error stream is dropped. The SDK's HTTP
client writes from WARNING up, as the other HTTP clients do.

**The answer, as data.** A text part is passed on; an image, an audio
clip, or a binary resource is named and not passed on; a link is named
by its address. The text is the tool's `McpOutput`, which the response
quotes as it quotes any tool's output. The server's own word that the
tool failed is a `permanent` failure with its text. The call's
idempotency key travels in its request's `_meta`, as `idempotencyKey`.

**The bound.** An exchange runs in one cancel scope that ends at the
call's deadline, the tool's own time. A server that hangs is cut off
then, and a stdio server's process tree is stopped. A line the
transport cannot read as the protocol ends the exchange at once, since
the SDK would wait for the answer it never parsed. A server that
answers outside the protocol is `permanent`, and one that cannot be
reached is `transient`.

**A stdio server on the engine's host.** A stdio server is a program the
adopter installs and names in configuration; no model chooses it. It
runs on the engine's host, so it is held as the engine's own code is:
it reaches the system it binds. A server whose tools run commands or
touch files would reach past the transport to the host, so it is
reached over HTTP from where tools execute. The engine cannot see what
a server's program does, so a review holds that, by the tools lens.

**Not now: a pool.** A connection kept per server and tenant would save
each call its connect and its listing, and a stdio server its start.
The pin would then need the server's notice that its tools changed, or
a listing anyway. Trigger: the connect and the listing passing a fifth
of MCP calls' time at the median, read from the calls' spans.

## Consequences

- `McpCall` takes the binding and the call's deadline. An adopter's own
  client moves to the new signature, or is replaced by `McpServers`.
- Every call pays a connection and a listing, and a stdio server a
  process start.
- A server's changed tool is refused at its next call, not at the next
  start.
- The SDK's own log is gone, and a stdio server's error stream with it.
  The client's errors say what failed.
- A credential injected into a stdio server's environment is the
  engine's deviation on secrets, as a command's is.
