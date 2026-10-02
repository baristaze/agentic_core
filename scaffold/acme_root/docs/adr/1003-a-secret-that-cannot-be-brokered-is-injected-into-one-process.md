# ADR 1003: A secret that cannot be brokered is injected into one process

**Status**: accepted (2026-10-02)

## Context

ASY-13: "The object model holds only references to secrets. A tenant's
secret is resolved at the point of use, for one operation, and
discarded. [...] No value enters an entity, a log line, an audit payload,
an error message, or a subprocess environment."

An agent's tools run commands, and some commands need a credential: a
command line that pushes a branch reads a token from its environment. The
engine's first answer is a broker. An egress proxy or a credential helper
outside the workspace attaches the credential per destination, and the
process never holds it. A tool declares such a secret `brokered`, and a
transport hands it to its broker.

Not every credential can be brokered. A program that signs its own
requests, or a tool with no proxy in front of it, needs the value in its
own environment.

## Decision

When a secret must enter a process, the transport that runs the command
resolves it by name, under the workspace's tenant, for that one command,
and injects it into that one process's environment. The environment is
built from nothing: a search path, the workspace as its home, a locale,
the command's own variables, and its injected secrets. Nothing of the
engine's environment, and none of its credentials, reaches the process.

The tool names each secret it may use in its spec. A call that uses one
is audited by the secret's name, in the event stream, before its command
runs; the value is in no step, no event, and no error.

Everything the process prints is redacted before it streams or returns:
the value raw, as base64 (the standard and the URL alphabets, at each
offset inside a longer run), as hex, and escaped for a URL, for JSON, and
for a Python literal. A stream holds back as many characters as the
longest of those forms, less one, so a value split across two chunks is
caught. The value is dropped when the command ends.

A brokered secret is never injected in its stead. With no broker, a
command that names one is refused, loudly.

## Consequences

- A secret in a process the agent controls is assumed disclosed to the
  agent. Redaction stops an accidental display, never a deliberate leak:
  a process can print the value in a form no rule matches. The scope and
  the lifetime of the credential are the defense, so the secret the store
  holds for such a tool is short-lived and scoped to what the tool's class
  may do, such as a token that pushes only the session's branch.
- A value the process writes to a file is not redacted when a later call
  reads the file: the value is gone by then.
- A secret is resolved on every command that uses it, one call to the
  store each.
- A value shorter than eight characters is matched raw only, since a
  shorter run of an encoding turns up in ordinary text.
