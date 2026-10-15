# Signals: agent_sessions

Sessions with an agent: each a series of loops over one history, whose
loops the session runner claims and runs.

## Kinds

- `LOOP`: one run of a session's loop, on the lane `loop`, which the
  session runner claims. A failed run leaves the session pending, and
  its item is retried until its attempts run out.
- `WAKE_AGENT_SESSION`: a parked session's retry time came; it unlocks
  the session still parked on that park. A failure leaves the session
  parked.
- `WAKE_AGENT_SESSIONS`: the reason an org's sessions parked for is
  gone, such as a raised or closed spend budget; it unlocks each session
  parked for it. A failure leaves them parked.
- `LEASE_NOTICE`: a lease request a session waits on was answered, or
  its lease was revoked; it unlocks the session parked in its wait line.
  A failure leaves the session parked in line.
