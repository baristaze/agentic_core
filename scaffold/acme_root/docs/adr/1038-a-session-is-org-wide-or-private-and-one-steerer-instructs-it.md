# ADR 1038: A session is org-wide or private, and one steerer instructs it

**Status**: accepted (2026-10-12)

## Context

A session's history and its artifacts opened on the tenant's `read`
alone, so a viewer could read the results of an admin's delegated calls
that the viewer's own permissions could not fetch. The spec says opening
content takes a permission of its own. The session's participants were
never read.

A message from any member who could make the session's calls counted as
an instruction, and its author became the speaker and the spender. So a
member could make an admin's delegated tools run by asking, and pay with
the admin's budget in a steady session the admin made.

Team work needs the opposite default for a delivery session: one anyone
in the org can find, join, and revive months later.

## Decision

**Org-wide or private.** A session is one or the other, and the org is
the team: there is no axis between the two. An org-wide session is seen
by every member of its org, and any member may join it as a participant.
A private session is seen by its creator, the participants its creator
adds, and `manage_sessions`, a new permission the owner and the admin
hold, for audit. A sub-agent is seen as its root is. To anyone else, a
session, its history, and its artifacts answer `NotFound`, as one that
never existed; the steps and the windows managers ask before they open
anything, and the sessions manager before every operation that names a
session.

**The default follows the kind's authority mode.** A steady kind's
sessions are org-wide: they behave the same for whoever resumes them. A
delegated kind's are private: their tools run with one person's own
permissions, and their results may hold what others cannot fetch. A
session made from a private one, a sub-agent or a hand-off, is private
too. The creator switches it either way, a `switched` step the status's
projection folds, and only the creator's switch counts.

**One steerer at a time.** The steerer is the session's principal, its
creator until a hand-off. Only the steerer's messages instruct. A
participant's message is data, quoted and labelled with its author: it
never wakes the session, never becomes the speaker, and never pays. The
history decides which a message is, as it writes it, from the sender's
place in the session; a member who takes no part is refused until they
join. Only the steerer pins standing text, and only one who takes part
takes the environment over.

**A hand-off, recorded.** `PUT /principal` with a body naming a
participant is the steerer's hand-off. With no body the caller takes the
steer: for a steerer who lapsed, someone who holds `manage_sessions`,
who joins the session as they take it. Each move is a `switched` step,
under an id the move derives, so a retry records it once. A sub-agent's
steer is its parent's, and it is never taken over.

**One permission for audit and for a lapsed steer.** `manage_sessions`
opens every session and takes over one whose steerer lapsed. Whoever may
take a session over may read it, so the steerer always sees the session
they steer. The service role holds it too, since a run of a session's
loop reads its session whoever it is visible to.

## Consequences

- A product lists a person's own sessions (`among=mine`) and the ones
  they may explore and join (`among=visible`), joins one, switches who
  sees it, and hands its steer on, each through the session API.
- A session stored before this change is private until its creator
  switches it: one hidden from a teammate costs a switch, while one left
  open would show another person's delegated results.
- A participant's message marks the session untrusted, as any data does,
  so a marked session that holds private data asks a person before it
  acts outward.
- A delegated session's calls run under the speaker its latest request
  recorded, so after a hand-off they run under the previous steerer
  until the new one speaks.
- A hand-off to someone who left the org lapses at once, as a steerer
  who leaves does, and `manage_sessions` takes it.
- A list of an org's sessions filters on the creator, the participants,
  and the visibility over the tenant's index by id. Not now: an index of
  its own, when the list's p95 passes 100 ms for an org.
