# Privacy

Where an agent session's content lives, the key it is sealed under, and
how it is erased. This is one of the kinds of thing [Acme is made
of](../../../../README.md).

## What it holds

- **Content and shape**: what a step says is its content: the messages,
  the tool inputs and outputs, the model's thinking, the attachments'
  names. A tool's result kept whole as an
  [artifact](../windows/README.md) is content too. Everything else is its
  shape: its ids, its type, its place in
  the history, who wrote it, and its header. Content is sealed; shape is
  not.
- **Session key**: one per session, in versions. Each version is a key
  kept wrapped by the tenant's key service, never in the clear. New
  content is sealed under the newest version, and what was sealed before
  keeps its own.
- **Storage policy**: chosen once per session, before its history
  begins. **Sealed** is the default: content is kept, sealed. **Memory
  only** keeps content in the running process alone; the shape is still
  kept when the policy allows, so cost and audit survive.
- **Privacy record**: one per session: its policy, and when its key was
  revoked and by whom.

## What can happen

- **Choose a policy.** Once, before anything is written to the history.
- **Seal and open.** Each step that says something is sealed on its way
  into storage and opened on its way out. Nothing else in the engine
  sees it happen. An artifact's text is sealed the same way, under the
  same key, before it reaches the object store, and so is what a command
  printed, in its transport's record of how it ended.
- **Add a version.** What comes after is sealed under it.
- **Revoke the key.** Every version is destroyed at once. The content
  becomes unreadable and the shape stays: every step keeps its place,
  its type, and its cost, and reads as absent. An artifact keeps its
  record, and its text is noise. A command's record still says how it
  ended, and what it printed is noise. It cannot be undone, and
  the session takes no content again.
- **Rotate the tenant's key.** Each version is wrapped again under the
  new one. No content is read or rewritten.
- **Purge.** A deleted tenant's records and keys go with the tenant.

## The rules

- **A database reader sees ciphertext.** Opening content takes the
  session's key, and using the tenant's key is the running service's
  permission alone.
- **A sealed step opens only as itself.** Moved to another step, another
  session, or another tenant, it opens nothing.
- **A hash in the shape is keyed by the session**, so once the key is
  revoked a hash confirms nothing about what it stood for.
- **A memory-only session leaves nothing it said at rest**, sealed or
  not. Its artifact is sealed like any other and held in the memory of
  the runtime that holds the session, so revoking its key erases it there
  too.
- **Every record and key belongs to one org.** Another org that names a
  session finds nothing of it.

<!-- agents-only
The sealing layer is `impl/sealed_steps.py`, the memory-only layer
`impl/memory_only_steps.py`, and the router `impl/routed_steps.py`;
`root.private_history` wires all three over the one history. The
artifacts' seal is `impl/artifacts.py`, which the root wires behind the
windows' `ArtifactSealInterface`. A command's record takes the same blob
form (`impl/blobs.py`) through `impl/records.py`, which the root makes for
each session behind the transport's `RecordSealInterface`. A data key is
in the clear only inside `impl/keys.py`. Revocation is
`PrivacyStorageInterface.revoke`, one commit (ADR 1004).
-->

## How another namespace composes it

The root wires the sealing layer under the [steps](../steps/README.md),
so every append and read of the history passes through it by the
session's policy. A tool that records a hash of its input asks for one
keyed by the session.
