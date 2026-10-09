# ADR 1025: A session parks on the guideline's outage signal, and a call that answers clears it

**Status**: accepted (2026-10-09)

## Context

A provider that fails fast makes every session that calls it spend its
own retries. The engine kept a signal of its own for that: a mark keyed
by the provider and the name of a key, with the kind of the error that
showed it, read and written against the loop's clock. A fleet's shared
impl was its platform's to supply. ADR 1009 records that a known outage
parks.

The guideline now holds the signal, at the same path in the base (ADR
0088): a mark keyed by the org that holds the credential, the provider,
and the secret's name, with a time to retry. Its cache impl shares the
mark over the shared cache and fails open, and one process takes its
null impl. Two signals at one path would disagree on the key, and on
what clears a mark.

## Decision

**The signal is the guideline's.** The engine keeps no outage signal of
its own. The loop reads the one `InfraInterface.get_outages()` gives
its root: the null impl in one process, the cache impl on a shared
cache.

**The key is the call's key, by name.** The loop names the key a call
goes out on, as the call credentials answer it. The platform's own key
is marked under the system scope, by the name `LoopOptions.credential`
gives it. A tenant's own key is marked under the tenant's org, by its
reference. One tenant's failing key parks no other tenant's session,
and none on the platform's key.

**The loop is the caller.** It reads the mark before a model call, once
the key is resolved and before anything is rendered, held, or spent. It
marks the pair when the retries of an error worth retrying are spent,
for the longer of the next retry's wait and `LoopOptions.outage_wait`. It clears the pair when a call on it answers.

**A session that reads a mark parks.** It parks on `provider`, naming
the provider, until the mark's retry time by the loop's own clock, and
makes no call before then. The park lands the session's own wake at
that time, as every timed park does.

**The mark holds no error kind.** The engine's mark carried the kind of
the error that showed it, and no park read it. A session's park needs
the provider and the retry time, and the guideline's mark holds both.

## Consequences

- Every process on the shared cache reads one mark per provider and
  key, so the retries one session spent spare every other session's.
- A call that answers ends the mark at once. A session already parked
  on it still wakes at its own retry time.
- A platform supplies no outage signal of its own: the base's cache
  impl is the fleet's.
- A cache that cannot answer reads as no mark. The session calls, and
  the provider's own errors retry it, then fall it back or park it.
