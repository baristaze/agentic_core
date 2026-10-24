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

One session of 1,500 turns writes 18 steps a second on the KMS
emulator, and 19 on the key twin. A step takes 56 ms. The seal is not
where it goes: the engine spends it reading what it already wrote.

**Where one session's time goes**, on the KMS emulator, by share of
wall time:

1. **The database, 42%.** Each step written costs 12 transactions,
   and each one is a BEGIN, its statements, and a COMMIT. The largest
   single reader is the session's key ring
   (`privacy/storage/impl/postgres.py:read_keys`, 9.5%), read again for
   every use of a key. Then the history's pages
   (`agent_steps/storage/impl/postgres.py:read_agent_steps`, 7.6%), and
   the first round trip of each transaction
   (`storage/impl/pg_base.py:ScopedConnection.execute`, 7.0%).
2. **The rest, 22%.** Mostly a step's own validators, run on every step
   read back (`agent_steps/types/agent_step.py:shape_refusal`, and the
   content's `storable`).
3. **Request rendering, 19%.** `windows/rules.py:prompt_bytes` turns the
   whole request into bytes each turn, to hash it (9.4%), and
   `exchanges` walks the whole history (4.1%).
4. **The key service, 6.5%.** 4.4 unwraps for each step written, each
   a KMS call: about three to open what an append stores and each page
   the loop reads, one for each keyed hash, and half of one to seal.
5. **Seal and open, 4.4%,** the event loop's own work, 4.3%, and the
   harness, 1.0%.

Across those lines, a third of the wall time is a read of the history
(`agent_steps/impl/manager.py:get_agent_steps`, 35% inclusive). Each
run reads the session's whole history at its start
(`agents/impl/loop_recovery.py:_recover_calls`, 14%), and each turn reads
again what it added.

**Where throughput stops.** Sessions at once scale to 84 steps a second
at four, and to 87 at eight, where the loop waits 2.5% of its time: the
process is bound by its one core. Past that, more sessions only queue.
The append's p99 goes from 10 ms with one session to 338 ms with 32. On
the key twin the ceiling is 103 steps a second, so the KMS client's own
work costs 15% of it. At 32 sessions, a session's head is read by
replaying its steps since its status
(`agent_sessions/impl/manager.py:_at_head`, 13% inclusive).

**The reads.** A page of 50 steps, read as the API reads it, takes
7.6 ms: the session, the key ring, one KMS call, and the page. The ring
and the call are a quarter of it. A page of the session list takes
1.3 ms with one session in it and 2.9 ms with 32, with no key call:
nothing on it is sealed yet.

**At rest.** Sealed content takes 1.39 bytes for each byte said:
base64's 4/3, the nonce, and the tag. A tool output object takes 1.001,
since it is kept as bytes.

**What would remove them.** Each is a reading of these numbers, not a
change made:

- Keep a session's opened keys for its run. The ring reads and the
  unwraps go: 16.5% of one session's wall time and 19.6% of the ceiling
  at 32 sessions, so about 1.2 times the steps, and a quarter of each
  history page. A step's key calls go from 4.4 to none. A deployment's
  KMS answers slower than the emulator, so it gains more: 4.4 of its
  round trips for each step.
- Read at a run's start only what the run needs, not the whole history:
  up to 14% at 1,500 turns, and more as a session grows.
- Keep opened steps for the run, so a turn does not read and validate
  again what it already holds: the history's reads are 35% inclusive.
- Hash the request as it grows instead of whole each turn: about 12%.
- Keep a session's head current instead of replaying it: about 13% at
  the ceiling.

The sampler's own cost is within the run's noise: one session of 150
turns writes 44.9 steps a second sampled and 44.4 without.

## The numbers

<!-- results: begin -->
From [`results/2026-10-24.json`](results/2026-10-24.json), started 2026-10-24T23:21:13Z: the shape `long-session.json` (1118 turns) replayed for 1500 turns alone, and for 150 turns in each session of the sweep, on Apple M4 Max (16 cores, 128 GB), macOS-26.6.2, Python 3.14.7.

### The runs

| keys | run | sessions | turns each | steps written | steps/s | append p50 | p95 | p99 | key calls / step written | key calls / step read | transactions / step | waiting |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KMS emulator | alone | 1 | 1500 | 6809 | 18.0 | 6.24 ms | 7.88 ms | 9.16 ms | 4.407 | 0.0201 | 11.99 | 33.3% |
| KMS emulator | sweep-1 | 1 | 150 | 658 | 44.9 | 6.11 ms | 7.92 ms | 10.32 ms | 3.267 | 0.0213 | 9.36 | 59.8% |
| KMS emulator | sweep-2 | 2 | 150 | 1316 | 69.6 | 6.63 ms | 8.94 ms | 10.87 ms | 3.267 | 0.0213 | 9.33 | 33.6% |
| KMS emulator | sweep-4 | 4 | 150 | 2632 | 84.0 | 9.4 ms | 17.13 ms | 25.16 ms | 3.267 | 0.0213 | 9.44 | 12.0% |
| KMS emulator | sweep-8 | 8 | 150 | 5264 | 87.0 | 17.36 ms | 39.75 ms | 76.35 ms | 3.267 | 0.0213 | 9.45 | 2.5% |
| KMS emulator | sweep-16 | 16 | 150 | 10528 | 85.3 | 34.58 ms | 80.64 ms | 126.5 ms | 3.267 | 0.0213 | 9.47 | 0.7% |
| KMS emulator | sweep-32 | 32 | 150 | 21056 | 78.3 | 67.81 ms | 207.05 ms | 337.5 ms | 3.267 | 0.0213 | 9.48 | 0.6% |
| key twin | alone | 1 | 1500 | 6809 | 19.0 | 4.53 ms | 5.86 ms | 6.9 ms | 4.407 | 0.0201 | 11.95 | 30.5% |
| key twin | sweep-1 | 1 | 150 | 658 | 52.7 | 4.26 ms | 5.86 ms | 7.8 ms | 3.267 | 0.0213 | 9.06 | 56.9% |
| key twin | sweep-2 | 2 | 150 | 1316 | 81.2 | 4.42 ms | 6.44 ms | 8.28 ms | 3.267 | 0.0213 | 9.34 | 36.8% |
| key twin | sweep-4 | 4 | 150 | 2632 | 97.7 | 6.65 ms | 16.4 ms | 23.97 ms | 3.267 | 0.0213 | 9.41 | 12.5% |
| key twin | sweep-8 | 8 | 150 | 5264 | 102.5 | 10.92 ms | 21.63 ms | 50.25 ms | 3.267 | 0.0213 | 9.45 | 3.0% |
| key twin | sweep-16 | 16 | 150 | 10528 | 99.9 | 24.81 ms | 61.01 ms | 112.7 ms | 3.267 | 0.0213 | 9.46 | 0.6% |
| key twin | sweep-32 | 32 | 150 | 21056 | 90.9 | 44.93 ms | 179.4 ms | 312.43 ms | 3.267 | 0.0213 | 9.47 | 0.5% |
| KMS emulator | sweep-1-unprofiled | 1 | 150 | 658 | 44.4 | 6.01 ms | 8.09 ms | 10.59 ms | 3.267 | 0.0213 | 9.33 |  |

### Where the wall time goes

| keys | run | seal and open | key service | database | object store | request rendering | harness | event loop | rest |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KMS emulator | alone | 4.4% | 6.5% | 42.4% | 0.0% | 19.4% | 1.0% | 4.3% | 21.9% |
| KMS emulator | sweep-1 | 2.8% | 10.8% | 64.1% | 0.1% | 6.4% | 0.3% | 6.5% | 9.0% |
| KMS emulator | sweep-2 | 4.1% | 8.8% | 57.8% | 0.1% | 10.2% | 0.5% | 5.9% | 12.5% |
| KMS emulator | sweep-4 | 5.0% | 9.0% | 50.6% | 0.0% | 12.6% | 0.7% | 6.8% | 15.2% |
| KMS emulator | sweep-8 | 5.5% | 9.9% | 45.6% | 0.0% | 13.4% | 0.8% | 8.1% | 16.7% |
| KMS emulator | sweep-16 | 5.8% | 9.8% | 44.0% | 0.0% | 14.5% | 0.8% | 7.6% | 17.5% |
| KMS emulator | sweep-32 | 5.9% | 9.7% | 43.4% | 0.0% | 14.0% | 0.9% | 6.8% | 19.3% |
| key twin | alone | 4.4% | 0.0% | 44.9% | 0.0% | 21.3% | 1.0% | 4.5% | 23.9% |
| key twin | sweep-1 | 3.2% | 0.0% | 71.4% | 0.1% | 7.6% | 0.4% | 7.6% | 9.9% |
| key twin | sweep-2 | 3.6% | 0.0% | 64.7% | 0.1% | 11.3% | 0.4% | 6.6% | 13.4% |
| key twin | sweep-4 | 5.4% | 0.0% | 54.6% | 0.0% | 14.9% | 0.6% | 7.2% | 17.4% |
| key twin | sweep-8 | 5.2% | 0.0% | 49.4% | 0.0% | 15.4% | 0.7% | 9.3% | 19.9% |
| key twin | sweep-16 | 5.8% | 0.0% | 48.3% | 0.0% | 16.5% | 0.7% | 8.0% | 20.8% |
| key twin | sweep-32 | 6.2% | 0.1% | 47.7% | 0.0% | 16.5% | 1.0% | 6.8% | 21.7% |

### The reads, as the API pages them

Open is the seal and open category: AES-GCM, the JSON, and the step's model, less its Python validators.

| keys | run | read | pages | items / page | pages/s | page p50 | p95 | p99 | key calls / page | open | key service | database | rest |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KMS emulator | alone | history, 50 steps a page | 137 | 49.7 | 120.1 | 7.58 ms | 9.85 ms | 57.52 ms | 1.0 | 3.6% | 10.7% | 65.1% | 10.2% |
| KMS emulator | alone | session list, 50 a page | 20 | 1.0 | 754.5 | 1.26 ms | 2.46 ms | 3.4 ms | 0.0 | 0.0% | 0.0% | 100.0% | 0.0% |
| KMS emulator | sweep-1 | history, 50 steps a page | 14 | 47.0 | 127.3 | 7.78 ms | 9.2 ms | 9.26 ms | 1.0 | 4.7% | 14.0% | 65.0% | 4.8% |
| KMS emulator | sweep-1 | session list, 50 a page | 20 | 1.0 | 794.9 | 1.16 ms | 2.35 ms | 3.07 ms | 0.0 | 0.0% | 0.0% | 100.0% | 0.0% |
| KMS emulator | sweep-2 | history, 50 steps a page | 28 | 47.0 | 128.6 | 7.72 ms | 10.11 ms | 11.17 ms | 1.0 | 3.9% | 10.4% | 62.9% | 11.8% |
| KMS emulator | sweep-2 | session list, 50 a page | 20 | 2.0 | 711.4 | 1.33 ms | 2.34 ms | 2.77 ms | 0.0 | 0.0% | 0.0% | 100.0% | 0.0% |
| KMS emulator | sweep-4 | history, 50 steps a page | 56 | 47.0 | 108.6 | 7.78 ms | 11.39 ms | 101.39 ms | 1.0 | 2.9% | 9.5% | 57.3% | 24.1% |
| KMS emulator | sweep-4 | session list, 50 a page | 20 | 4.0 | 676.6 | 1.32 ms | 2.69 ms | 3.27 ms | 0.0 | 0.0% | 0.0% | 91.2% | 0.0% |
| KMS emulator | sweep-8 | history, 50 steps a page | 112 | 47.0 | 114.5 | 7.85 ms | 10.84 ms | 64.03 ms | 1.0 | 2.6% | 8.3% | 58.4% | 19.3% |
| KMS emulator | sweep-8 | session list, 50 a page | 20 | 8.0 | 624.3 | 1.4 ms | 2.4 ms | 2.83 ms | 0.0 | 0.0% | 0.0% | 84.3% | 0.0% |
| KMS emulator | sweep-16 | history, 50 steps a page | 224 | 47.0 | 115.9 | 8.04 ms | 10.66 ms | 12.6 ms | 1.0 | 3.9% | 9.4% | 62.1% | 15.3% |
| KMS emulator | sweep-16 | session list, 50 a page | 20 | 16.0 | 480.2 | 2.1 ms | 3.02 ms | 3.44 ms | 0.0 | 0.0% | 0.0% | 93.1% | 0.0% |
| KMS emulator | sweep-32 | history, 50 steps a page | 448 | 47.0 | 110.6 | 8.1 ms | 10.16 ms | 13.64 ms | 1.0 | 3.3% | 11.8% | 63.8% | 14.0% |
| KMS emulator | sweep-32 | session list, 50 a page | 20 | 32.0 | 346.9 | 2.94 ms | 4.88 ms | 6.16 ms | 0.0 | 0.0% | 0.0% | 100.0% | 0.0% |
| key twin | alone | history, 50 steps a page | 137 | 49.7 | 144.0 | 6.29 ms | 8.01 ms | 47.55 ms | 1.0 | 3.6% | 0.0% | 67.3% | 20.2% |
| key twin | alone | session list, 50 a page | 20 | 1.0 | 812.5 | 1.17 ms | 2.2 ms | 2.98 ms | 0.0 | 0.0% | 0.0% | 81.6% | 0.0% |
| key twin | sweep-1 | history, 50 steps a page | 14 | 47.0 | 152.6 | 6.49 ms | 7.85 ms | 7.98 ms | 1.0 | 2.6% | 0.0% | 76.8% | 4.4% |
| key twin | sweep-1 | session list, 50 a page | 20 | 1.0 | 777.9 | 1.21 ms | 2.13 ms | 2.73 ms | 0.0 | 0.0% | 0.0% | 100.0% | 0.0% |
| key twin | sweep-2 | history, 50 steps a page | 28 | 47.0 | 153.8 | 6.51 ms | 7.7 ms | 7.8 ms | 1.0 | 5.5% | 0.0% | 79.7% | 3.0% |
| key twin | sweep-2 | session list, 50 a page | 20 | 2.0 | 801.9 | 1.24 ms | 2.16 ms | 2.54 ms | 0.0 | 0.0% | 0.0% | 100.0% | 0.0% |
| key twin | sweep-4 | history, 50 steps a page | 56 | 47.0 | 141.7 | 6.8 ms | 9.31 ms | 12.21 ms | 1.0 | 3.0% | 0.0% | 72.8% | 12.5% |
| key twin | sweep-4 | session list, 50 a page | 20 | 4.0 | 710.3 | 1.29 ms | 2.17 ms | 2.49 ms | 0.0 | 0.0% | 0.0% | 84.2% | 0.0% |
| key twin | sweep-8 | history, 50 steps a page | 112 | 47.0 | 125.0 | 7.03 ms | 10.0 ms | 72.85 ms | 1.0 | 4.9% | 0.0% | 67.8% | 20.1% |
| key twin | sweep-8 | session list, 50 a page | 20 | 8.0 | 621.9 | 1.43 ms | 2.37 ms | 2.74 ms | 0.0 | 0.0% | 0.0% | 92.6% | 0.0% |
| key twin | sweep-16 | history, 50 steps a page | 224 | 47.0 | 126.3 | 6.94 ms | 9.1 ms | 66.94 ms | 1.0 | 4.6% | 0.0% | 65.0% | 23.0% |
| key twin | sweep-16 | session list, 50 a page | 20 | 16.0 | 493.5 | 2.01 ms | 2.82 ms | 2.91 ms | 0.0 | 0.0% | 0.0% | 94.5% | 0.0% |
| key twin | sweep-32 | history, 50 steps a page | 448 | 47.0 | 126.5 | 7.01 ms | 8.86 ms | 11.03 ms | 1.0 | 3.5% | 0.0% | 66.8% | 20.8% |
| key twin | sweep-32 | session list, 50 a page | 20 | 32.0 | 433.5 | 2.47 ms | 3.11 ms | 3.44 ms | 0.0 | 0.0% | 0.0% | 94.7% | 0.0% |
| KMS emulator | sweep-1-unprofiled | history, 50 steps a page | 14 | 47.0 | 113.6 | 7.98 ms | 14.3 ms | 15.02 ms | 1.0 |  |  |  |  |
| KMS emulator | sweep-1-unprofiled | session list, 50 a page | 20 | 1.0 | 794.5 | 1.21 ms | 2.4 ms | 3.31 ms | 0.0 |  |  |  |  |

### Ranked: one session, KMS emulator

| rank | where | share of wall time | of it, waiting | the code that spends it |
| ---: | --- | ---: | ---: | --- |
| 1 | database | 42.4% | 27.9% | `acme/om/privacy/storage/impl/postgres.py:PrivacyStoragePostgresImpl.read_keys` 9.5%<br>`acme/om/agent_steps/storage/impl/postgres.py:AgentStepStoragePostgresImpl.read_agent_steps` 7.6%<br>`acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 7.0% |
| 2 | rest | 21.9% | 1.0% | `acme/om/agent_steps/types/agent_step.py:shape_refusal` 3.3%<br>`acme/integrations/model_providers/content.py:storable` 2.3%<br>`acme/infra/base.py:thaw_mapping` 1.9% |
| 3 | request rendering | 19.4% | 0.0% | `acme/om/windows/rules.py:prompt_bytes` 9.4%<br>`acme/om/windows/rules.py:exchanges` 4.1%<br>`acme/om/windows/rules.py:_call.<locals>.<genexpr>` 1.2% |
| 4 | key service | 6.5% | 4.3% | `acme/infra/keys/kms.py:KeyServiceKmsImpl.unwrap` 6.5% |
| 5 | seal and open | 4.4% | 0.0% | `acme/om/privacy/impl/sealed_agent_steps.py:opened` 3.8%<br>`acme/om/privacy/impl/manager.py:PrivacyManagerImpl.keyed_hash` 0.3%<br>`acme/om/privacy/impl/sealed_agent_steps.py:bound_to` 0.1% |
| 6 | event loop | 4.3% | 0.0% | the loop's own callbacks 4.2%<br>`acme/om/agents/impl/loop_model_turn.py:LoopModelTurn._stream` 0.0%<br>`acme/infra/keys/kms.py:KeyServiceKmsImpl.unwrap` 0.0% |
| 7 | harness | 1.0% | 0.0% | `performance/replay/engine.py:request_chars` 0.8%<br>`performance/replay/engine.py:request_chars.<locals>.<genexpr>` 0.2%<br>`acme/integrations/model_providers/scripted.py:parts_of.<locals>.<genexpr>` 0.0% |
| 8 | object store | 0.0% | 0.0% | `acme/infra/buckets/s3.py:BucketsS3Impl.put` 0.0% |

### Ranked: one session, key twin

| rank | where | share of wall time | of it, waiting | the code that spends it |
| ---: | --- | ---: | ---: | --- |
| 1 | database | 44.9% | 29.7% | `acme/om/privacy/storage/impl/postgres.py:PrivacyStoragePostgresImpl.read_keys` 9.6%<br>`acme/om/agent_steps/storage/impl/postgres.py:AgentStepStoragePostgresImpl.read_agent_steps` 8.3%<br>`acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 7.5% |
| 2 | rest | 23.9% | 0.8% | `acme/om/agent_steps/types/agent_step.py:shape_refusal` 3.9%<br>`acme/integrations/model_providers/content.py:storable` 2.6%<br>`acme/infra/base.py:thaw_mapping` 2.1% |
| 3 | request rendering | 21.3% | 0.0% | `acme/om/windows/rules.py:prompt_bytes` 10.2%<br>`acme/om/windows/rules.py:exchanges` 4.6%<br>`acme/om/windows/rules.py:_conversation` 1.3% |
| 4 | event loop | 4.5% | 0.0% | the loop's own callbacks 4.4%<br>`acme/om/agents/impl/loop_model_turn.py:LoopModelTurn._stream` 0.1%<br>`acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 0.0% |
| 5 | seal and open | 4.4% | 0.0% | `acme/om/privacy/impl/sealed_agent_steps.py:opened` 3.9%<br>`acme/om/privacy/impl/manager.py:PrivacyManagerImpl.keyed_hash` 0.3%<br>`acme/om/privacy/impl/sealed_agent_steps.py:bound_to` 0.2% |
| 6 | harness | 1.0% | 0.0% | `performance/replay/engine.py:request_chars` 0.8%<br>`performance/replay/engine.py:request_chars.<locals>.<genexpr>` 0.2%<br>`acme/integrations/model_providers/scripted.py:parts_of.<locals>.<genexpr>` 0.0% |
| 7 | object store | 0.0% | 0.0% | `acme/infra/buckets/s3.py:BucketsS3Impl.put` 0.0% |

### Ranked: 32 sessions at once, KMS emulator

| rank | where | share of wall time | of it, waiting | the code that spends it |
| ---: | --- | ---: | ---: | --- |
| 1 | database | 43.4% | 0.6% | `acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 11.9%<br>`acme/om/privacy/storage/impl/postgres.py:PrivacyStoragePostgresImpl.read_keys` 7.0%<br>the loop's own callbacks 2.7% |
| 2 | rest | 19.3% | 0.1% | `acme/om/agent_steps/types/agent_step.py:shape_refusal` 3.7%<br>`acme/integrations/model_providers/content.py:storable` 1.5%<br>`acme/infra/base.py:_plain` 1.3% |
| 3 | request rendering | 14.0% | 0.0% | `acme/om/windows/rules.py:prompt_bytes` 8.6%<br>`acme/om/windows/rules.py:exchanges` 1.8%<br>`acme/om/windows/rules.py:_call.<locals>.<genexpr>` 1.3% |
| 4 | key service | 9.7% | 0.0% | `acme/infra/keys/kms.py:KeyServiceKmsImpl.unwrap` 9.7%<br>`acme/infra/keys/kms.py:encryption_context` 0.0%<br>`acme/infra/keys/kms.py:KeyServiceKmsImpl.generate` 0.0% |
| 5 | event loop | 6.8% | 0.0% | the loop's own callbacks 6.2%<br>`acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 0.1%<br>`acme/om/storage/impl/pg_base.py:scoped_session` 0.1% |
| 6 | seal and open | 5.9% | 0.0% | `acme/om/privacy/impl/sealed_agent_steps.py:opened` 4.9%<br>`acme/om/privacy/impl/manager.py:PrivacyManagerImpl.keyed_hash` 0.6%<br>`acme/om/privacy/impl/sealed_agent_steps.py:sealed` 0.4% |
| 7 | harness | 0.9% | 0.0% | `performance/replay/engine.py:request_chars` 0.4%<br>`acme/integrations/model_providers/scripted.py:parts_of.<locals>.<genexpr>` 0.3%<br>`performance/replay/engine.py:request_chars.<locals>.<genexpr>` 0.1% |
| 8 | object store | 0.0% | 0.0% | `acme/infra/buckets/s3.py:BucketsS3Impl.put` 0.0% |

### Bytes at rest

| keys | run | bytes said | content at rest | per byte said | tool output text | objects | per byte kept |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| KMS emulator | alone | 7249235 | 10059051 | 1.388 | 297654 | 297942 | 1.001 |
| key twin | alone | 7249235 | 10059051 | 1.388 | 297654 | 297942 | 1.001 |

### One round trip on this stack

| keys | a database round trip | an unwrap |
| --- | ---: | ---: |
| KMS emulator | 0.3 ms | 0.625 ms |
| key twin | 0.294 ms | 0.006 ms |
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
