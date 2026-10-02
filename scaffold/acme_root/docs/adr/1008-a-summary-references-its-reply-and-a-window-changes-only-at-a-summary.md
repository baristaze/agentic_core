# ADR 1008: A summary references its reply, and a window changes only at a summary

**Status**: accepted (2026-10-02)

## Context

A session's history only grows; a model reads a bounded amount. The
engine folds the oldest part of the agent's window into a `summary` step
when the window nears its limit. A summary is written by a model, the
summarizer, and every model call is a request and a response in the
history. A step is written once, and no step copies another.

A request's prompt is cached by the provider as a prefix. Anything that
changes the bytes of an earlier turn, between two requests, makes the
provider read the whole prompt again. A summary folds in data: tool
output, events, a stranger's words. A model can be talked into anything
by what it reads.

## Decision

**A summary references its reply.** A compaction records the
summarizer's call as its request and its response, and the `summary`
step holds no text of its own: it references the response, and its
header names the range of the history it stands for. A reply cut short,
refused, or empty is recorded as a response and writes no summary.

**The pinned zone is the engine's, never the summarizer's.** It quotes
principals' messages from the history, whole while they fit a bound and
as excerpts that cite their message beyond it. No model writes a word of
it, so a summary never becomes an instruction; it renders as data.

**What an earlier turn says changes only at a summary.** An input
renders as the turn before the response of the request that delivered
it, so each request's prompt is the prefix of the next one's. A tool
result above a bound renders as a stub only once a later response has
read it, and only before the latest summary, so the stub appears at a
compaction and never between two.

**A compaction keeps what the model has not read.** It never folds an
input no request has delivered, nor the latest exchange, whose tool
results the next request is the first to read. It runs at most once
before a request, and once more when the provider refuses that request
as too long; a second refusal, or a window with nothing left to fold,
ends the loop rather than compact again.

## Consequences

- The summary's text has one home, the summarizer's response; revoking
  the session's key erases it with every other step.
- A replay re-renders each recorded request from the steps before it and
  compares its hash; nothing outside the history decides the bytes.
- A window whose latest exchange alone is past the model's limit cannot
  be compacted below it: the provider refuses it, and the loop ends
  `errored`. A tool result above the size bound is kept as an artifact,
  with a preview in its step, so one result cannot fill a window.
- A principal's message is pinned whatever it says; the pinned zone
  grows with them, within its bound and its digest's.
