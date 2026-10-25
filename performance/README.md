# performance

The engine under a long session's load, on the path a deployment runs.
It replays the shape of one real session, fast, through the scaffold's
engine as the session runner builds it. Every step is sealed. The steps
go to Postgres, the tool outputs to the object store, and each key comes
from a key service across the network. Then it ranks where the time
goes.

## What it runs

- **The shape.** [`capture.py`](capture.py) reads one long session of a
  coding agent, from its transcript on the machine that ran it. It keeps
  numbers alone: for each model turn, the size of its thinking (with its
  signature), its text, and each tool call's input; for each tool
  result, its size and whether it failed; the order; and the gaps. A
  tool is kept as the engine's tool class, by a rule in the capture's
  code, and its name is dropped. No word, name, id, or path is kept.
  [`shapes/long-session.json`](shapes/long-session.json) is that shape:
  1,118 model turns, 1,240 tool calls, and 192 runs of the loop.
- **Thinking.** The transcript keeps no thinking text. A turn's thinking
  size is its thinking tokens as its usage reports them, or its output
  tokens less its visible ones, in characters at the transcript's own
  rate per visible token.
- **The replay.** The scripted twin is the model. It answers each turn
  with synthetic words of the shape's sizes, from a fixed seed, streamed
  in parts. Synthetic tools, one per class, answer at once with outputs
  of the shape's sizes. Every text opens with a marker for the run,
  which nothing at rest may hold. The replay ignores the gaps and runs
  as fast as the engine goes. It plays the shape in laps: 1,500 turns
  alone is the shape once and 382 turns of it again.
- **The engine.** [`replay/engine.py`](replay/engine.py) builds the
  managers with the runner's own `RunnerContainer.over`, from the
  runner's settings, which [`stack.env`](stack.env) sets. Each role has
  its own Postgres, the cache and the topics run on Valkey, and tool
  outputs over 24,000 characters go to MinIO. The key service is the
  real KMS adapter at a KMS emulator, or the in-process key twin, so the
  cost of a key call over the network shows.
- **The load.** One session of 1,500 turns. Then a sweep of 1 to 32
  sessions at once in one process, 150 turns each.
- **The reads.** After each replay, each session's history is read from
  its start, 50 steps a page, as the API's route reads it: the session,
  then the page. Then the tenant's session list is read whole, 50 a
  page, twenty times over.
- **The reading.** A sampler reads the event loop's thread every 2 ms.
  It shortens the interpreter's switch interval to 0.1 ms while it
  runs, so a sample lands where the loop is, not where it next gives up
  the lock. When the loop runs code, the sample is that code's. When it waits, the
  sample is split between the sessions' tasks, each by what it waits on.
  Each sample goes to the first category its frames match, from the
  innermost out. The harness's own time, the twin's and the tools', is
  a category of its own.

## Run it

From the repository's root:

```bash
make performance        # the whole run on its own stack, about 35 minutes
make performance-check  # a short replay on memory storage, checked; CI runs it
```

`make performance` starts the compose project `agentic-performance`
([`compose.yml`](compose.yml)) on ports 61379 to 61901, migrates it,
runs, and stops it. It refuses to start while that project runs. Its
data stays in the project's volumes;
`docker compose -p agentic-performance down -v` removes them. It writes
`results/<time>.json`. `python -m replay --report results/<file>.json`,
run from this folder, renders the tables below from that file, and the
root tests hold them equal to the file they cite.

While each point writes, and once it is done, a scan reads its steps'
rows as the database's superuser and every tool output object it kept.
It counts the run's marker in them, as it is and in base64. Each scan
also counts what it read, so a scan that reads nothing fails the run.

`make performance-check` replays 40 turns on memory storage and the key
twin, with a provider's key set. Every model response reads back as the
reply the twin streamed, every tool response as its tool's output, a
kept one whole, and every message as what was said. Below the sealing
layer, every step that says something is sealed, and nothing stored
holds the marker.

## What the numbers say

One session of 1,500 turns writes 23 steps a second on the KMS
emulator, and 24 on the key twin. A step takes 43 ms. The loop reads
its whole history once, at its start, and from then on only what others
wrote: what the run appends joins the history it holds unread, and an
append opens nothing it stored. A session's head, a request's
attribution, and the status at a run's end still read the run's own
steps back.

**Where one session's time goes**, on the KMS emulator, by share of
wall time:

1. **The database, 39%.** Each step written costs 9.8 transactions,
   and each one is a BEGIN, its statements, and a COMMIT. The first
   round trip of each transaction
   (`storage/impl/pg_base.py:ScopedConnection.execute`, 7.4%), the
   history's pages (`agent_steps/storage/impl/postgres.py:read_agent_steps`,
   6.8%), and the session's key ring
   (`privacy/storage/impl/postgres.py:read_keys`, 5.8%), read in one
   statement for every use of a key.
2. **Request rendering, 25%.** `windows/rules.py:prompt_bytes` turns the
   whole request into bytes each turn, to hash it (12%), and
   `exchanges` walks the whole history (5.2%).
3. **The rest, 21%.** Mostly a step's own validators, run on every step
   read (`agent_steps/types/agent_step.py:shape_refusal`, 2.1%, and the
   content's `storable`, 1.8%), and `infra/base.py:thaw_mapping` (2.5%).
4. **The key service, 5.9%.** 2.9 unwraps for each step written, each a
   KMS call: 0.7 to hash tool inputs, which each call hashes twice; 0.7
   to open the pages a run's start reads; 0.8 to open what a session
   wrote since its status, read again to find its head
   (`agent_sessions/impl/manager.py:_at_head`, attribution's `_range`);
   0.2 for the request's hash; and half of one to seal.
5. **The event loop's own work, 3.8%,** seal and open, 3.3%, and the
   harness, 1.6%.

Across those lines, a quarter of the wall time is a read of the history
(`agent_steps/impl/manager.py:get_agent_steps`, 25% inclusive). Most of
it is a run's start, which reads the history whole: 383 ms at the
median, and 730 ms for the last run, on the longest history.

**Where throughput stops.** Sessions at once scale to 97 steps a second
at four, and to 101 at eight, where the loop waits 2.9% of its time: the
process is bound by its one core. Past that, more sessions only queue.
The append's p99 goes from 6 ms with one session to 228 ms with 32. On
the key twin the ceiling is 115 steps a second, so the KMS client's own
work costs 12% of it. At 32 sessions, a session's head is read by
replaying its steps since its status
(`agent_sessions/impl/manager.py:_at_head`, 14% inclusive).

**The reads.** A page of 50 steps, read as the API reads it, takes
7.4 ms: the session, the key ring, one KMS call, and the page. A page of
the session list takes 1.3 ms with one session in it and 2.8 ms with 32,
with no key call: nothing on it is sealed yet.

**At rest.** Sealed content takes 1.39 bytes for each byte said:
base64's 4/3, the nonce, and the tag. A tool output object takes 1.001,
since it is kept as bytes.

**What would remove them.** Each is a reading of these numbers, not a
change made:

- Read at a run's start only what the run needs, not the whole history:
  most of a run's start, 383 ms at the median. The toolsets a session
  loaded, its standing text, its plan, the messages its window pins, and
  the asks it waits in line on can each lie anywhere in the history, and
  nothing records where, so today only a read from its first step finds
  them.
- Keep a session's opened key for its run. The ring reads and the
  unwraps go: 5.8% and 5.9% of one session's wall time. A revocation
  then reaches a run only through a statement its writes already make,
  and an append today still takes a step that says nothing after a
  revocation, so a loop can end.
- Hash the request as it grows instead of whole each turn: about 12%.
- Keep a session's head current instead of replaying it: 14% at the
  ceiling.

The sampler's own cost is within the run's noise: one session of 150
turns writes 52.3 steps a second sampled and 53.1 without.

## The numbers

<!-- results: begin -->
From [`results/2026-10-25T0145Z.json`](results/2026-10-25T0145Z.json), started 2026-10-25T01:45:24Z: the shape `long-session.json` (1118 turns) replayed for 1500 turns alone, and for 150 turns in each session of the sweep, on Apple M4 Max (16 cores, 128 GB), macOS-26.6.2, Python 3.14.7.

### The runs

| keys | run | sessions | turns each | steps written | steps/s | append p50 | p95 | p99 | key calls / step written | key calls / step read | transactions / step | waiting |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KMS emulator | alone | 1 | 1500 | 6809 | 23.2 | 3.8 ms | 5.09 ms | 6.1 ms | 2.855 | 0.0201 | 9.77 | 32.3% |
| KMS emulator | sweep-1 | 1 | 150 | 658 | 52.3 | 3.77 ms | 5.23 ms | 7.17 ms | 2.281 | 0.0213 | 8.4 | 57.7% |
| KMS emulator | sweep-2 | 2 | 150 | 1316 | 82.3 | 3.97 ms | 5.71 ms | 8.04 ms | 2.281 | 0.0213 | 8.43 | 32.8% |
| KMS emulator | sweep-4 | 4 | 150 | 2632 | 96.7 | 6.31 ms | 15.12 ms | 26.1 ms | 2.281 | 0.0213 | 8.4 | 11.0% |
| KMS emulator | sweep-8 | 8 | 150 | 5264 | 101.3 | 10.67 ms | 25.44 ms | 44.85 ms | 2.281 | 0.0213 | 8.44 | 2.9% |
| KMS emulator | sweep-16 | 16 | 150 | 10528 | 99.6 | 21.7 ms | 54.81 ms | 100.68 ms | 2.281 | 0.0213 | 8.45 | 0.7% |
| KMS emulator | sweep-32 | 32 | 150 | 21056 | 92.8 | 46.39 ms | 113.95 ms | 228.36 ms | 2.281 | 0.0213 | 8.46 | 0.6% |
| key twin | alone | 1 | 1500 | 6809 | 24.3 | 2.83 ms | 4.06 ms | 4.73 ms | 2.855 | 0.0201 | 9.78 | 29.8% |
| key twin | sweep-1 | 1 | 150 | 658 | 60.6 | 2.67 ms | 3.98 ms | 5.64 ms | 2.281 | 0.0213 | 8.22 | 57.0% |
| key twin | sweep-2 | 2 | 150 | 1316 | 92.4 | 2.95 ms | 4.36 ms | 5.52 ms | 2.281 | 0.0213 | 8.32 | 34.7% |
| key twin | sweep-4 | 4 | 150 | 2632 | 111.2 | 4.51 ms | 11.29 ms | 16.71 ms | 2.281 | 0.0213 | 8.32 | 12.6% |
| key twin | sweep-8 | 8 | 150 | 5264 | 115.2 | 8.36 ms | 21.75 ms | 34.68 ms | 2.281 | 0.0213 | 8.41 | 2.9% |
| key twin | sweep-16 | 16 | 150 | 10528 | 113.5 | 17.96 ms | 48.72 ms | 88.25 ms | 2.281 | 0.0213 | 8.45 | 0.7% |
| key twin | sweep-32 | 32 | 150 | 21056 | 105.0 | 36.25 ms | 98.83 ms | 212.03 ms | 2.281 | 0.0213 | 8.45 | 0.6% |
| KMS emulator | sweep-1-unprofiled | 1 | 150 | 658 | 53.1 | 3.67 ms | 5.01 ms | 6.05 ms | 2.281 | 0.0213 | 8.14 |  |

### Where the wall time goes

| keys | run | seal and open | key service | database | object store | request rendering | harness | event loop | rest |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KMS emulator | alone | 3.3% | 5.9% | 38.9% | 0.0% | 25.4% | 1.6% | 3.8% | 21.1% |
| KMS emulator | sweep-1 | 3.1% | 9.2% | 64.5% | 0.1% | 7.6% | 0.3% | 6.0% | 9.3% |
| KMS emulator | sweep-2 | 4.3% | 6.9% | 58.4% | 0.0% | 10.8% | 0.4% | 5.9% | 13.2% |
| KMS emulator | sweep-4 | 5.8% | 8.2% | 49.8% | 0.0% | 13.9% | 0.8% | 6.6% | 15.0% |
| KMS emulator | sweep-8 | 5.7% | 8.3% | 43.8% | 0.0% | 16.2% | 1.1% | 8.0% | 17.0% |
| KMS emulator | sweep-16 | 5.5% | 8.8% | 42.6% | 0.0% | 16.7% | 1.0% | 7.6% | 17.8% |
| KMS emulator | sweep-32 | 5.8% | 8.5% | 41.9% | 0.0% | 15.9% | 1.3% | 6.6% | 20.0% |
| key twin | alone | 3.5% | 0.0% | 41.4% | 0.0% | 27.7% | 1.8% | 3.7% | 21.8% |
| key twin | sweep-1 | 2.9% | 0.0% | 71.6% | 0.1% | 8.6% | 0.5% | 7.4% | 9.0% |
| key twin | sweep-2 | 3.6% | 0.0% | 63.2% | 0.1% | 12.3% | 0.5% | 5.9% | 14.5% |
| key twin | sweep-4 | 5.3% | 0.0% | 53.5% | 0.0% | 16.0% | 1.1% | 7.0% | 17.0% |
| key twin | sweep-8 | 5.9% | 0.0% | 47.1% | 0.0% | 18.4% | 1.2% | 8.1% | 19.3% |
| key twin | sweep-16 | 5.9% | 0.0% | 47.1% | 0.0% | 18.3% | 1.2% | 7.7% | 19.7% |
| key twin | sweep-32 | 6.2% | 0.1% | 46.8% | 0.0% | 18.8% | 1.2% | 6.7% | 20.3% |

### The reads, as the API pages them

Open is the seal and open category: AES-GCM, the JSON, and the step's model, less its Python validators.

| keys | run | read | pages | items / page | pages/s | page p50 | p95 | p99 | key calls / page | open | key service | database | rest |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KMS emulator | alone | history, 50 steps a page | 137 | 49.7 | 124.7 | 7.37 ms | 9.33 ms | 44.85 ms | 1.0 | 1.0% | 12.2% | 69.4% | 10.1% |
| KMS emulator | alone | session list, 50 a page | 20 | 1.0 | 716.5 | 1.31 ms | 2.75 ms | 3.91 ms | 0.0 | 0.0% | 0.0% | 100.0% | 0.0% |
| KMS emulator | sweep-1 | history, 50 steps a page | 14 | 47.0 | 131.9 | 7.18 ms | 9.95 ms | 10.07 ms | 1.0 | 3.1% | 9.6% | 71.6% | 4.9% |
| KMS emulator | sweep-1 | session list, 50 a page | 20 | 1.0 | 757.7 | 1.25 ms | 2.33 ms | 3.16 ms | 0.0 | 0.0% | 0.0% | 100.0% | 0.0% |
| KMS emulator | sweep-2 | history, 50 steps a page | 28 | 47.0 | 132.8 | 7.29 ms | 10.51 ms | 12.27 ms | 1.0 | 2.9% | 8.9% | 64.9% | 15.0% |
| KMS emulator | sweep-2 | session list, 50 a page | 20 | 2.0 | 737.0 | 1.25 ms | 2.59 ms | 3.1 ms | 0.0 | 0.0% | 0.0% | 100.0% | 0.0% |
| KMS emulator | sweep-4 | history, 50 steps a page | 56 | 47.0 | 127.4 | 7.78 ms | 9.42 ms | 11.22 ms | 1.0 | 6.1% | 10.4% | 59.1% | 12.9% |
| KMS emulator | sweep-4 | session list, 50 a page | 20 | 4.0 | 660.7 | 1.38 ms | 2.57 ms | 3.1 ms | 0.0 | 0.0% | 0.0% | 83.0% | 0.0% |
| KMS emulator | sweep-8 | history, 50 steps a page | 112 | 47.0 | 115.8 | 7.95 ms | 9.69 ms | 70.76 ms | 1.0 | 3.9% | 9.9% | 67.3% | 9.6% |
| KMS emulator | sweep-8 | session list, 50 a page | 20 | 8.0 | 608.9 | 1.46 ms | 2.47 ms | 2.86 ms | 0.0 | 0.0% | 0.0% | 93.0% | 0.0% |
| KMS emulator | sweep-16 | history, 50 steps a page | 224 | 47.0 | 118.4 | 7.96 ms | 9.81 ms | 12.91 ms | 1.0 | 3.5% | 9.0% | 61.9% | 15.7% |
| KMS emulator | sweep-16 | session list, 50 a page | 20 | 16.0 | 533.1 | 1.97 ms | 2.71 ms | 3.13 ms | 0.0 | 0.0% | 0.0% | 93.7% | 0.0% |
| KMS emulator | sweep-32 | history, 50 steps a page | 448 | 47.0 | 111.8 | 7.95 ms | 10.04 ms | 29.06 ms | 1.0 | 3.9% | 9.8% | 65.2% | 12.6% |
| KMS emulator | sweep-32 | session list, 50 a page | 20 | 32.0 | 355.3 | 2.79 ms | 4.61 ms | 5.18 ms | 0.0 | 0.0% | 0.0% | 94.4% | 0.0% |
| key twin | alone | history, 50 steps a page | 137 | 49.7 | 140.7 | 6.32 ms | 9.33 ms | 47.89 ms | 1.0 | 3.0% | 0.0% | 68.5% | 17.6% |
| key twin | alone | session list, 50 a page | 20 | 1.0 | 752.4 | 1.25 ms | 2.23 ms | 2.93 ms | 0.0 | 0.0% | 0.0% | 100.0% | 0.0% |
| key twin | sweep-1 | history, 50 steps a page | 14 | 47.0 | 148.9 | 6.79 ms | 8.19 ms | 8.49 ms | 1.0 | 0.0% | 0.0% | 69.9% | 20.4% |
| key twin | sweep-1 | session list, 50 a page | 20 | 1.0 | 535.4 | 1.47 ms | 3.94 ms | 4.64 ms | 0.0 | 0.0% | 0.0% | 93.8% | 0.0% |
| key twin | sweep-2 | history, 50 steps a page | 28 | 47.0 | 157.4 | 6.36 ms | 7.79 ms | 8.15 ms | 1.0 | 9.9% | 0.0% | 74.0% | 6.8% |
| key twin | sweep-2 | session list, 50 a page | 20 | 2.0 | 734.3 | 1.3 ms | 2.31 ms | 2.7 ms | 0.0 | 0.0% | 0.0% | 80.8% | 0.0% |
| key twin | sweep-4 | history, 50 steps a page | 56 | 47.0 | 155.2 | 6.41 ms | 7.66 ms | 8.21 ms | 1.0 | 2.3% | 0.0% | 76.1% | 9.0% |
| key twin | sweep-4 | session list, 50 a page | 20 | 4.0 | 637.7 | 1.38 ms | 2.69 ms | 3.19 ms | 0.0 | 0.0% | 0.0% | 91.4% | 0.0% |
| key twin | sweep-8 | history, 50 steps a page | 112 | 47.0 | 134.0 | 6.61 ms | 8.83 ms | 68.14 ms | 1.0 | 3.4% | 0.0% | 75.6% | 9.9% |
| key twin | sweep-8 | session list, 50 a page | 20 | 8.0 | 595.8 | 1.45 ms | 2.73 ms | 3.18 ms | 0.0 | 0.0% | 0.0% | 79.8% | 0.0% |
| key twin | sweep-16 | history, 50 steps a page | 224 | 47.0 | 129.6 | 6.78 ms | 8.65 ms | 69.78 ms | 1.0 | 3.9% | 0.0% | 70.0% | 18.2% |
| key twin | sweep-16 | session list, 50 a page | 20 | 16.0 | 487.4 | 2.22 ms | 2.83 ms | 3.05 ms | 0.0 | 0.0% | 0.0% | 94.7% | 0.0% |
| key twin | sweep-32 | history, 50 steps a page | 448 | 47.0 | 130.1 | 6.78 ms | 8.86 ms | 12.62 ms | 1.0 | 3.4% | 0.0% | 73.0% | 15.6% |
| key twin | sweep-32 | session list, 50 a page | 20 | 32.0 | 445.1 | 2.4 ms | 3.1 ms | 3.41 ms | 0.0 | 0.0% | 0.0% | 88.3% | 6.0% |
| KMS emulator | sweep-1-unprofiled | history, 50 steps a page | 14 | 47.0 | 128.0 | 7.67 ms | 8.87 ms | 8.95 ms | 1.0 |  |  |  |  |
| KMS emulator | sweep-1-unprofiled | session list, 50 a page | 20 | 1.0 | 613.1 | 1.29 ms | 4.15 ms | 5.01 ms | 0.0 |  |  |  |  |

### Ranked: one session, KMS emulator

| rank | where | share of wall time | of it, waiting | the code that spends it |
| ---: | --- | ---: | ---: | --- |
| 1 | database | 38.9% | 27.0% | `acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 7.4%<br>`acme/om/agent_steps/storage/impl/postgres.py:AgentStepStoragePostgresImpl.read_agent_steps` 6.8%<br>`acme/om/privacy/storage/impl/postgres.py:PrivacyStoragePostgresImpl.read_keys` 5.8% |
| 2 | request rendering | 25.4% | 0.0% | `acme/om/windows/rules.py:prompt_bytes` 12.2%<br>`acme/om/windows/rules.py:exchanges` 5.2%<br>`acme/om/windows/rules.py:_call.<locals>.<genexpr>` 1.7% |
| 3 | rest | 21.1% | 1.2% | `acme/infra/base.py:thaw_mapping` 2.5%<br>`acme/om/agent_steps/types/agent_step.py:shape_refusal` 2.1%<br>`acme/integrations/model_providers/content.py:storable` 1.8% |
| 4 | key service | 5.9% | 4.1% | `acme/infra/keys/kms.py:KeyServiceKmsImpl.unwrap` 5.9% |
| 5 | event loop | 3.8% | 0.0% | the loop's own callbacks 3.7%<br>`acme/om/agents/impl/loop_model_turn.py:LoopModelTurn._stream` 0.1%<br>`acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 0.0% |
| 6 | seal and open | 3.3% | 0.0% | `acme/om/privacy/impl/sealed_agent_steps.py:opened` 2.7%<br>`acme/om/privacy/impl/manager.py:PrivacyManagerImpl.keyed_hash` 0.4%<br>`acme/om/privacy/impl/sealed_agent_steps.py:bound_to` 0.1% |
| 7 | harness | 1.6% | 0.0% | `performance/replay/engine.py:request_chars` 1.0%<br>`performance/replay/engine.py:Engine.run` 0.4%<br>`performance/replay/engine.py:request_chars.<locals>.<genexpr>` 0.2% |
| 8 | object store | 0.0% | 0.0% | `acme/infra/buckets/s3.py:BucketsS3Impl.put` 0.0% |

### Ranked: one session, key twin

| rank | where | share of wall time | of it, waiting | the code that spends it |
| ---: | --- | ---: | ---: | --- |
| 1 | database | 41.4% | 28.9% | `acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 7.8%<br>`acme/om/agent_steps/storage/impl/postgres.py:AgentStepStoragePostgresImpl.read_agent_steps` 7.5%<br>`acme/om/privacy/storage/impl/postgres.py:PrivacyStoragePostgresImpl.read_keys` 5.9% |
| 2 | request rendering | 27.7% | 0.0% | `acme/om/windows/rules.py:prompt_bytes` 13.2%<br>`acme/om/windows/rules.py:exchanges` 5.9%<br>`acme/om/windows/rules.py:_conversation` 1.7% |
| 3 | rest | 21.8% | 0.9% | `acme/infra/base.py:thaw_mapping` 2.5%<br>`acme/om/agent_steps/types/agent_step.py:shape_refusal` 2.2%<br>`acme/integrations/model_providers/content.py:storable` 2.0% |
| 4 | event loop | 3.7% | 0.0% | the loop's own callbacks 3.6%<br>`acme/om/agents/impl/loop_model_turn.py:LoopModelTurn._stream` 0.1%<br>`acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 0.0% |
| 5 | seal and open | 3.5% | 0.0% | `acme/om/privacy/impl/sealed_agent_steps.py:opened` 2.9%<br>`acme/om/privacy/impl/manager.py:PrivacyManagerImpl.keyed_hash` 0.4%<br>`acme/om/privacy/impl/sealed_agent_steps.py:bound_to` 0.1% |
| 6 | harness | 1.8% | 0.0% | `performance/replay/engine.py:request_chars` 1.1%<br>`performance/replay/engine.py:Engine.run` 0.4%<br>`performance/replay/engine.py:request_chars.<locals>.<genexpr>` 0.2% |
| 7 | object store | 0.0% | 0.0% | `acme/infra/buckets/s3.py:BucketsS3Impl.put` 0.0% |

### Ranked: 32 sessions at once, KMS emulator

| rank | where | share of wall time | of it, waiting | the code that spends it |
| ---: | --- | ---: | ---: | --- |
| 1 | database | 41.9% | 0.5% | `acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 12.9%<br>`acme/om/privacy/storage/impl/postgres.py:PrivacyStoragePostgresImpl.read_keys` 3.3%<br>the loop's own callbacks 2.8% |
| 2 | rest | 20.0% | 0.0% | `acme/om/agent_steps/types/agent_step.py:shape_refusal` 3.2%<br>`acme/integrations/model_providers/content.py:storable` 1.7%<br>`acme/infra/base.py:_plain` 1.6% |
| 3 | request rendering | 15.9% | 0.0% | `acme/om/windows/rules.py:prompt_bytes` 9.9%<br>`acme/om/windows/rules.py:exchanges` 2.1%<br>`acme/om/windows/rules.py:_call.<locals>.<genexpr>` 1.3% |
| 4 | key service | 8.5% | 0.0% | `acme/infra/keys/kms.py:KeyServiceKmsImpl.unwrap` 8.5%<br>`acme/infra/keys/kms.py:KeyServiceKmsImpl.generate` 0.0%<br>`acme/infra/keys/kms.py:encryption_context` 0.0% |
| 5 | event loop | 6.6% | 0.0% | the loop's own callbacks 6.0%<br>`acme/om/storage/impl/pg_base.py:scoped_session` 0.1%<br>`acme/om/agents/impl/loop_model_turn.py:LoopModelTurn._stream` 0.1% |
| 6 | seal and open | 5.8% | 0.0% | `acme/om/privacy/impl/sealed_agent_steps.py:opened` 4.5%<br>`acme/om/privacy/impl/manager.py:PrivacyManagerImpl.keyed_hash` 0.7%<br>`acme/om/privacy/impl/sealed_agent_steps.py:sealed` 0.5% |
| 7 | harness | 1.3% | 0.0% | `performance/replay/engine.py:request_chars` 0.5%<br>`acme/integrations/model_providers/scripted.py:parts_of.<locals>.<genexpr>` 0.4%<br>`performance/replay/engine.py:Engine.run` 0.1% |
| 8 | object store | 0.0% | 0.0% | `acme/infra/buckets/s3.py:BucketsS3Impl.put` 0.0% |

### Bytes at rest

| keys | run | bytes said | content at rest | per byte said | tool output text | objects | per byte kept |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| KMS emulator | alone | 7249235 | 10059051 | 1.388 | 297654 | 297942 | 1.001 |
| key twin | alone | 7249235 | 10059051 | 1.388 | 297654 | 297942 | 1.001 |

### A run's start

From the loop's run to its first model call, over every run of the point. The last is the point's last run: alone, the one on the longest history.

| keys | run | runs | run start p50 | p95 | p99 | the last |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| KMS emulator | alone | 249 | 383.06 ms | 687.6 ms | 746.93 ms | 730.1 ms |
| KMS emulator | sweep-1 | 14 | 89.39 ms | 111.95 ms | 115.12 ms | 110.63 ms |
| KMS emulator | sweep-2 | 28 | 130.88 ms | 215.35 ms | 217.97 ms | 216.37 ms |
| KMS emulator | sweep-4 | 56 | 201.5 ms | 320.07 ms | 320.92 ms | 200.2 ms |
| KMS emulator | sweep-8 | 112 | 452.81 ms | 630.47 ms | 649.75 ms | 479.94 ms |
| KMS emulator | sweep-16 | 224 | 1014.91 ms | 1374.59 ms | 1386.87 ms | 1385.2 ms |
| KMS emulator | sweep-32 | 448 | 1532.41 ms | 2158.73 ms | 2369.07 ms | 1517.59 ms |
| key twin | alone | 249 | 367.76 ms | 683.87 ms | 722.07 ms | 669.58 ms |
| key twin | sweep-1 | 14 | 79.51 ms | 155.95 ms | 181.44 ms | 100.79 ms |
| key twin | sweep-2 | 28 | 118.2 ms | 178.55 ms | 181.7 ms | 152.84 ms |
| key twin | sweep-4 | 56 | 217.04 ms | 328.58 ms | 330.9 ms | 330.43 ms |
| key twin | sweep-8 | 112 | 439.64 ms | 622.64 ms | 644.92 ms | 424.16 ms |
| key twin | sweep-16 | 224 | 940.2 ms | 1232.96 ms | 1255.34 ms | 1098.49 ms |
| key twin | sweep-32 | 448 | 1406.79 ms | 1916.07 ms | 2050.6 ms | 1434.05 ms |
| KMS emulator | sweep-1-unprofiled | 14 | 90.57 ms | 151.96 ms | 173.71 ms | 106.66 ms |

### One round trip on this stack

| keys | a database round trip | an unwrap |
| --- | ---: | ---: |
| KMS emulator | 0.314 ms | 0.639 ms |
| key twin | 0.302 ms | 0.007 ms |
<!-- results: end -->

## What it leaves out

- **Approvals.** A synthetic tool says it acts on nothing outward, which
  is true of it. So an integration's call runs under its class's policy
  and no person's approval is replayed.
- **The queue.** The relay's work goes to the memory queue. It is
  written once a run, not once a step.
- **The network.** The stack runs on this machine. A round trip here is
  the one the last table gives, not a deployment's.

## Where things are

- [`capture.py`](capture.py): a transcript to a shape.
- [`replay/shape.py`](replay/shape.py): the shape's schema, which
  admits numbers, booleans, and tool classes alone.
- [`replay/generate.py`](replay/generate.py) and
  [`replay/words.py`](replay/words.py): the twin's script and its
  words.
- [`replay/tools.py`](replay/tools.py): the synthetic tools.
- [`replay/engine.py`](replay/engine.py): the engine, wired as the
  runner wires it, and the twin.
- [`replay/run.py`](replay/run.py): one point, and what it measures.
- [`replay/profile.py`](replay/profile.py): the sampler and its
  categories.
- [`replay/full.py`](replay/full.py): the whole run, its scans, and its
  calibration.
- [`replay/checks.py`](replay/checks.py): what `--check` holds.
- [`replay/report.py`](replay/report.py): the tables, from a results
  file.

It sits outside the scaffold, so no copy of the scaffold carries it.
