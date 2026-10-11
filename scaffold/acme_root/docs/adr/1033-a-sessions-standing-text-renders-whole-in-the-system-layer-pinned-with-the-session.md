# ADR 1033: A session's standing text renders whole in the system layer, pinned with the session

**Status**: accepted (2026-10-11)

## Context

A principal often has instructions that should hold for every session
of a kind: an organization's manual for how its agents work. Today they
have no place. Sent as a message, they sit in the window until a
compaction folds them, and from the next summary on the pinned zone
quotes them, but only up to its bound of 8,000 characters. Past it,
older instructions become 200-character excerpts. And before the first
summary the pinned zone renders nothing at all. A manual must be whole
on every request. It must also be cheap, since it rides every call, and
only the cached system layer gives both.

## Decision

**Where it renders.** A session's standing text renders in the system
layer, after the kind's prompts and before the engine's notice of what
data is. It renders as an instruction, under a one-line lead, never
inside a `<data>` element. It is whole on every request, the main role's
and a side role's alike, before and after any compaction, and it never
enters the pinned zone. The summarizer's request does not carry it: the
summarizer has a prompt of its own, and the text is no record to fold.

**Where it lives.** The text lives in the history, as a `switched` step
whose header carries its size and whose content holds the text. So it
is sealed at rest under the session's key, as every step's content is,
and every storage mode and delete applies to it unchanged. A column on
the session would keep it in the clear. A render reads the text off the
history, as it reads a toolset load, so a replay renders the same bytes
even after a switch. A column would hold only the latest text.

**Who sets it.** A session pins its standing text when it is made, from
the `standing` its maker sends: a start, a spawn, or a hand-off. The
engine's own spawn tool sends none, since the model never writes it. A
layer that sources the text fills the field. Nothing about where the
text comes from lives in the engine. The step arrives through the inbox
before anything else, under an id the making derives, so a retry pins
the one it pinned.

**A recorded switch.** `switch_standing` records a new text, or none,
through the inbox. A loop renders the text pinned before the input it
began from, so a switch waits for the next loop, and the loop running
keeps the text it began with. A session's instructions never change
under the work it is doing, as a kind's version never does. The rule
reads only the history, so a replay reads what its run read.

**It is an arrival.** The inbox appends inputs, controls, and a standing
switch. Every reader that asks whether a run wrote a step treats the
switch as an arrival: the open loop, and the status projection, which
leaves a pending session pending. The switch instructs, so the inbox
asks whether its sender may make every call the session's registry
offers, as it asks of a principal's message.

**The bound.** The text holds at most 24,000 characters, about 6,000
tokens. It is room every window of the session gives up, a small side
model's included. A request past the bound is refused when the text is
pinned, with the reason, and nothing is made. A procedure the work needs
only now and then is invoked when it is needed, never pinned.

## Consequences

- The system layer is the kind's prompts, the standing text, and the
  notice. Sessions of a kind version that pin the same text share one
  cached system layer; a different text is a different prefix. A switch
  costs one cache write at the next loop.
- A `switched` step names one of a fill switch, a toolset, or standing
  text. Only a standing switch holds content, one text block of the size
  its header names.
- The pinned zone keeps what principals said in the session. A standing
  instruction given in a message is still pinned there; one meant for
  every session is standing text.
- Nothing is migrated: a step's header is stored as JSON, and its
  content sealed as before.
