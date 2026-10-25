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
  and so does every session's title, which nothing at rest may hold. The replay ignores the gaps and runs
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
  then the page. Then the tenant is topped up to a full page of 50
  sessions, and its session list is read whole, twenty times over, each
  page with its titles opened, as the API's list route reads it.
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
rows and its tenant's sessions' rows as the database's superuser, and
every tool output object it kept. It counts the run's marker in them, as
it is and in base64. Each scan
also counts what it read, so a scan that reads nothing fails the run.

`make performance-check` replays 40 turns on memory storage and the key
twin, with a provider's key set. Every model response reads back as the
reply the twin streamed, every tool response as its tool's output, a
kept one whole, and every message as what was said. Below the sealing
layer, every step that says something is sealed, every title of the
list's page is sealed and opens as given, and nothing stored holds the
marker.

## What the numbers say

One session of 1,500 turns writes 24 steps a second on the KMS
emulator, and 25 on the key twin. A step takes 42 ms. The loop reads
its whole history once, at its start, and from then on only what others
wrote: what the run appends joins the history it holds unread, and an
append opens nothing it stored. A session's head, a request's
attribution, and the status at a run's end still read the run's own
steps back.

**Where one session's time goes**, on the KMS emulator, by share of
wall time:

1. **The database, 39%.** Each step written costs 9.9 transactions,
   and each one is a BEGIN, its statements, and a COMMIT. The first
   round trip of each transaction
   (`storage/impl/pg_base.py:ScopedConnection.execute`, 7.5%), the
   history's pages (`agent_steps/storage/impl/postgres.py:read_agent_steps`,
   6.8%), and the session's key ring
   (`privacy/storage/impl/postgres.py:read_keys`, 5.7%), read in one
   statement for every use of a key.
2. **Request rendering, 25%.** `windows/rules.py:prompt_bytes` turns the
   whole request into bytes each turn, to hash it (12%), and
   `exchanges` walks the whole history (5.2%).
3. **The rest, 21%.** Mostly a step's own validators, run on every step
   read (`agent_steps/types/agent_step.py:shape_refusal`, 2.0%, and the
   content's `storable`, 2.1%), and `infra/base.py:thaw_mapping` (2.3%).
4. **The key service, 5.8%.** 2.9 unwraps for each step written, each a
   KMS call: 0.7 to hash tool inputs, which each call hashes twice; 0.7
   to open the pages a run's start reads; 0.8 to open what a session
   wrote since its status, read again to find its head
   (`agent_sessions/impl/manager.py:_at_head`, attribution's `_range`);
   0.2 for the request's hash; and half of one to seal.
5. **The event loop's own work, 3.8%,** seal and open, 3.2%, and the
   harness, 1.6%.

Across those lines, a quarter of the wall time is a read of the history
(`agent_steps/impl/manager.py:get_agent_steps`, 25% inclusive). Most of
it is a run's start, which reads the history whole: 374 ms at the
median, and 729 ms for the last run, on the longest history.

**Where throughput stops.** Sessions at once scale to 99 steps a second
at four, and to 103 at eight, where the loop waits 2.8% of its time: the
process is bound by its one core. Past that, more sessions only queue.
The append's p99 goes from 6 ms with one session to 226 ms with 32. On
the key twin the ceiling is 117 steps a second, so the KMS client's own
work costs 12% of it. At 32 sessions, a session's head is read by
replaying its steps since its status
(`agent_sessions/impl/manager.py:_at_head`, 14% inclusive).

**The reads.** A page of 50 steps, read as the API reads it, takes
7.2 ms: the session, the key ring, one KMS call, and the page. A full
page of the session list, 50 sessions with their titles opened, takes
4.9 ms at the median and 7.1 ms at the 95th percentile: the page, one
read of its title keys, one KMS call for the tenant's title key, and
the 50 titles opened under it. A page takes that one call whatever its
size; one call per title would take 50.

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
From [`results/2026-10-25T0447Z.json`](results/2026-10-25T0447Z.json), started 2026-10-25T04:47:21Z: the shape `long-session.json` (1118 turns) replayed for 1500 turns alone, and for 150 turns in each session of the sweep, on Apple M4 Max (16 cores, 128 GB), macOS-26.6.2, Python 3.14.7.

### The runs

| keys | run | sessions | turns each | steps written | steps/s | append p50 | p95 | p99 | key calls / step written | key calls / step read | transactions / step | waiting |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KMS emulator | alone | 1 | 1500 | 6809 | 23.8 | 3.74 ms | 4.98 ms | 5.87 ms | 2.855 | 0.0201 | 9.87 | 32.7% |
| KMS emulator | sweep-1 | 1 | 150 | 658 | 53.6 | 3.67 ms | 4.99 ms | 6.17 ms | 2.283 | 0.0213 | 8.92 | 57.9% |
| KMS emulator | sweep-2 | 2 | 150 | 1316 | 84.1 | 3.94 ms | 5.4 ms | 6.72 ms | 2.283 | 0.0213 | 8.92 | 32.7% |
| KMS emulator | sweep-4 | 4 | 150 | 2632 | 98.5 | 5.84 ms | 13.58 ms | 20.29 ms | 2.284 | 0.0213 | 8.64 | 12.0% |
| KMS emulator | sweep-8 | 8 | 150 | 5264 | 103.0 | 10.5 ms | 25.75 ms | 41.06 ms | 2.284 | 0.0213 | 8.53 | 2.8% |
| KMS emulator | sweep-16 | 16 | 150 | 10528 | 100.9 | 21.69 ms | 49.87 ms | 99.43 ms | 2.284 | 0.0213 | 8.5 | 0.7% |
| KMS emulator | sweep-32 | 32 | 150 | 21056 | 94.0 | 45.42 ms | 109.93 ms | 226.33 ms | 2.283 | 0.0213 | 8.47 | 0.6% |
| key twin | alone | 1 | 1500 | 6809 | 24.7 | 2.8 ms | 4.02 ms | 4.74 ms | 2.855 | 0.0201 | 9.87 | 29.9% |
| key twin | sweep-1 | 1 | 150 | 658 | 62.0 | 2.66 ms | 3.87 ms | 6.1 ms | 2.283 | 0.0213 | 8.7 | 57.8% |
| key twin | sweep-2 | 2 | 150 | 1316 | 92.8 | 2.93 ms | 4.53 ms | 6.95 ms | 2.283 | 0.0213 | 8.79 | 35.6% |
| key twin | sweep-4 | 4 | 150 | 2632 | 112.1 | 4.25 ms | 10.88 ms | 19.54 ms | 2.284 | 0.0213 | 8.58 | 13.0% |
| key twin | sweep-8 | 8 | 150 | 5264 | 117.0 | 8.59 ms | 23.13 ms | 38.68 ms | 2.283 | 0.0213 | 8.53 | 2.9% |
| key twin | sweep-16 | 16 | 150 | 10528 | 114.7 | 18.03 ms | 45.85 ms | 104.54 ms | 2.283 | 0.0213 | 8.48 | 0.8% |
| key twin | sweep-32 | 32 | 150 | 21056 | 106.4 | 35.81 ms | 99.93 ms | 216.23 ms | 2.283 | 0.0213 | 8.46 | 0.6% |
| KMS emulator | sweep-1-unprofiled | 1 | 150 | 658 | 53.0 | 3.64 ms | 4.91 ms | 6.49 ms | 2.283 | 0.0213 | 9.46 |  |

### Where the wall time goes

| keys | run | seal and open | key service | database | object store | request rendering | harness | event loop | rest |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KMS emulator | alone | 3.2% | 5.8% | 39.0% | 0.0% | 25.2% | 1.6% | 3.8% | 21.3% |
| KMS emulator | sweep-1 | 2.8% | 9.6% | 64.0% | 0.1% | 7.8% | 0.4% | 6.4% | 9.0% |
| KMS emulator | sweep-2 | 4.5% | 7.5% | 57.9% | 0.0% | 10.9% | 0.5% | 5.7% | 13.0% |
| KMS emulator | sweep-4 | 5.3% | 7.4% | 50.1% | 0.0% | 14.2% | 0.9% | 6.7% | 15.2% |
| KMS emulator | sweep-8 | 5.3% | 8.6% | 42.9% | 0.0% | 16.7% | 0.9% | 8.5% | 17.2% |
| KMS emulator | sweep-16 | 5.5% | 8.7% | 43.0% | 0.0% | 16.8% | 0.9% | 7.4% | 17.7% |
| KMS emulator | sweep-32 | 5.8% | 8.3% | 41.9% | 0.0% | 16.8% | 1.2% | 6.8% | 19.2% |
| key twin | alone | 3.4% | 0.0% | 41.5% | 0.0% | 26.9% | 1.8% | 3.8% | 22.7% |
| key twin | sweep-1 | 3.1% | 0.0% | 70.5% | 0.1% | 9.3% | 0.4% | 7.2% | 9.4% |
| key twin | sweep-2 | 3.5% | 0.0% | 64.5% | 0.1% | 12.3% | 0.5% | 6.3% | 12.7% |
| key twin | sweep-4 | 4.7% | 0.0% | 54.3% | 0.0% | 16.1% | 0.7% | 7.2% | 16.9% |
| key twin | sweep-8 | 5.6% | 0.0% | 47.2% | 0.0% | 19.4% | 1.0% | 8.2% | 18.5% |
| key twin | sweep-16 | 5.8% | 0.0% | 46.5% | 0.0% | 19.1% | 1.0% | 7.5% | 20.1% |
| key twin | sweep-32 | 6.0% | 0.1% | 45.7% | 0.0% | 18.5% | 1.5% | 6.7% | 21.5% |

### The reads, as the API pages them

Open is the seal and open category: AES-GCM, the JSON, and the step's model, less its Python validators.

| keys | run | read | pages | items / page | pages/s | page p50 | p95 | p99 | key calls / page | open | key service | database | rest |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| KMS emulator | alone | history, 50 steps a page | 137 | 49.7 | 126.4 | 7.18 ms | 9.28 ms | 49.06 ms | 1.0 | 4.1% | 14.3% | 57.9% | 17.3% |
| KMS emulator | alone | session list, 50 a page | 20 | 50.0 | 185.6 | 4.94 ms | 7.08 ms | 7.22 ms | 1.0 | 2.6% | 16.7% | 64.2% | 6.9% |
| KMS emulator | sweep-1 | history, 50 steps a page | 14 | 47.0 | 135.7 | 7.44 ms | 9.18 ms | 9.68 ms | 1.0 | 9.5% | 15.2% | 58.1% | 7.8% |
| KMS emulator | sweep-1 | session list, 50 a page | 20 | 50.0 | 181.6 | 5.04 ms | 9.25 ms | 11.2 ms | 1.0 | 4.9% | 16.6% | 70.9% | 5.2% |
| KMS emulator | sweep-2 | history, 50 steps a page | 28 | 47.0 | 104.0 | 7.5 ms | 40.33 ms | 106.84 ms | 1.0 | 3.5% | 9.3% | 73.6% | 9.0% |
| KMS emulator | sweep-2 | session list, 50 a page | 20 | 50.0 | 148.8 | 6.01 ms | 10.55 ms | 11.76 ms | 1.0 | 0.0% | 10.3% | 80.8% | 8.9% |
| KMS emulator | sweep-4 | history, 50 steps a page | 56 | 47.0 | 123.1 | 7.88 ms | 10.85 ms | 12.26 ms | 1.0 | 4.3% | 15.1% | 62.4% | 13.4% |
| KMS emulator | sweep-4 | session list, 50 a page | 20 | 50.0 | 163.2 | 5.25 ms | 12.75 ms | 16.36 ms | 1.0 | 7.0% | 19.6% | 58.4% | 7.4% |
| KMS emulator | sweep-8 | history, 50 steps a page | 112 | 47.0 | 118.1 | 7.8 ms | 9.71 ms | 67.1 ms | 1.0 | 4.3% | 10.9% | 58.3% | 18.1% |
| KMS emulator | sweep-8 | session list, 50 a page | 20 | 50.0 | 155.1 | 5.91 ms | 10.36 ms | 11.38 ms | 1.0 | 1.9% | 19.1% | 71.5% | 4.0% |
| KMS emulator | sweep-16 | history, 50 steps a page | 224 | 47.0 | 119.4 | 7.8 ms | 10.3 ms | 13.07 ms | 1.0 | 4.0% | 11.5% | 62.6% | 15.9% |
| KMS emulator | sweep-16 | session list, 50 a page | 20 | 50.0 | 131.8 | 7.09 ms | 13.38 ms | 13.73 ms | 1.0 | 5.9% | 13.8% | 69.1% | 3.4% |
| KMS emulator | sweep-32 | history, 50 steps a page | 448 | 47.0 | 115.3 | 7.84 ms | 10.11 ms | 16.01 ms | 1.0 | 3.7% | 9.4% | 65.5% | 14.0% |
| KMS emulator | sweep-32 | session list, 50 a page | 20 | 50.0 | 147.0 | 6.46 ms | 11.61 ms | 14.32 ms | 1.0 | 0.0% | 17.4% | 69.9% | 7.2% |
| key twin | alone | history, 50 steps a page | 137 | 49.7 | 145.1 | 6.24 ms | 7.83 ms | 45.96 ms | 1.0 | 2.1% | 0.0% | 77.3% | 10.8% |
| key twin | alone | session list, 50 a page | 20 | 50.0 | 111.5 | 4.18 ms | 90.92 ms | 165.01 ms | 1.0 | 0.0% | 0.0% | 94.7% | 4.1% |
| key twin | sweep-1 | history, 50 steps a page | 14 | 47.0 | 159.4 | 6.22 ms | 7.77 ms | 8.03 ms | 1.0 | 7.4% | 0.0% | 71.9% | 14.7% |
| key twin | sweep-1 | session list, 50 a page | 20 | 50.0 | 213.7 | 4.32 ms | 6.45 ms | 6.46 ms | 1.0 | 0.0% | 0.0% | 85.8% | 3.5% |
| key twin | sweep-2 | history, 50 steps a page | 28 | 47.0 | 158.9 | 6.25 ms | 7.59 ms | 7.7 ms | 1.0 | 1.8% | 0.0% | 65.5% | 21.2% |
| key twin | sweep-2 | session list, 50 a page | 20 | 50.0 | 217.1 | 4.24 ms | 7.45 ms | 7.71 ms | 1.0 | 0.0% | 0.0% | 81.6% | 2.8% |
| key twin | sweep-4 | history, 50 steps a page | 56 | 47.0 | 154.1 | 6.41 ms | 7.94 ms | 9.39 ms | 1.0 | 4.1% | 0.0% | 70.8% | 14.8% |
| key twin | sweep-4 | session list, 50 a page | 20 | 50.0 | 218.4 | 4.13 ms | 7.34 ms | 7.99 ms | 1.0 | 2.5% | 0.0% | 87.4% | 2.5% |
| key twin | sweep-8 | history, 50 steps a page | 112 | 47.0 | 135.4 | 6.49 ms | 9.11 ms | 76.13 ms | 1.0 | 4.5% | 0.0% | 65.6% | 19.4% |
| key twin | sweep-8 | session list, 50 a page | 20 | 50.0 | 175.4 | 5.25 ms | 10.04 ms | 11.76 ms | 1.0 | 2.7% | 0.0% | 75.9% | 7.4% |
| key twin | sweep-16 | history, 50 steps a page | 224 | 47.0 | 132.3 | 7.02 ms | 9.34 ms | 10.25 ms | 1.0 | 4.0% | 0.0% | 70.0% | 18.1% |
| key twin | sweep-16 | session list, 50 a page | 20 | 50.0 | 206.4 | 4.33 ms | 9.37 ms | 11.96 ms | 1.0 | 0.0% | 0.0% | 85.3% | 4.8% |
| key twin | sweep-32 | history, 50 steps a page | 448 | 47.0 | 131.9 | 6.73 ms | 8.59 ms | 11.21 ms | 1.0 | 3.1% | 0.0% | 72.6% | 15.7% |
| key twin | sweep-32 | session list, 50 a page | 20 | 50.0 | 201.5 | 4.73 ms | 6.35 ms | 6.92 ms | 1.0 | 7.7% | 0.0% | 84.7% | 5.0% |
| KMS emulator | sweep-1-unprofiled | history, 50 steps a page | 14 | 47.0 | 126.8 | 7.46 ms | 10.99 ms | 11.74 ms | 1.0 |  |  |  |  |
| KMS emulator | sweep-1-unprofiled | session list, 50 a page | 20 | 50.0 | 173.8 | 5.3 ms | 9.24 ms | 10.13 ms | 1.0 |  |  |  |  |

### Ranked: one session, KMS emulator

| rank | where | share of wall time | of it, waiting | the code that spends it |
| ---: | --- | ---: | ---: | --- |
| 1 | database | 39.0% | 27.4% | `acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 7.5%<br>`acme/om/agent_steps/storage/impl/postgres.py:AgentStepStoragePostgresImpl.read_agent_steps` 6.8%<br>`acme/om/privacy/storage/impl/postgres.py:PrivacyStoragePostgresImpl.read_keys` 5.7% |
| 2 | request rendering | 25.2% | 0.0% | `acme/om/windows/rules.py:prompt_bytes` 12.2%<br>`acme/om/windows/rules.py:exchanges` 5.2%<br>`acme/om/windows/rules.py:_conversation` 1.6% |
| 3 | rest | 21.3% | 1.2% | `acme/infra/base.py:thaw_mapping` 2.3%<br>`acme/integrations/model_providers/content.py:storable` 2.1%<br>`acme/om/agent_steps/types/agent_step.py:shape_refusal` 2.0% |
| 4 | key service | 5.8% | 4.1% | `acme/infra/keys/kms.py:KeyServiceKmsImpl.unwrap` 5.8% |
| 5 | event loop | 3.8% | 0.0% | the loop's own callbacks 3.7%<br>`acme/om/agents/impl/loop_model_turn.py:LoopModelTurn._stream` 0.1%<br>`acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 0.0% |
| 6 | seal and open | 3.2% | 0.0% | `acme/om/privacy/impl/sealed_agent_steps.py:opened` 2.5%<br>`acme/om/privacy/impl/manager.py:PrivacyManagerImpl.keyed_hash` 0.4%<br>`acme/om/privacy/impl/sealed_agent_steps.py:sealed` 0.1% |
| 7 | harness | 1.6% | 0.0% | `performance/replay/engine.py:request_chars` 1.0%<br>`performance/replay/engine.py:Engine.run` 0.4%<br>`performance/replay/engine.py:request_chars.<locals>.<genexpr>` 0.2% |
| 8 | object store | 0.0% | 0.0% | `acme/infra/buckets/s3.py:BucketsS3Impl.put` 0.0% |

### Ranked: one session, key twin

| rank | where | share of wall time | of it, waiting | the code that spends it |
| ---: | --- | ---: | ---: | --- |
| 1 | database | 41.5% | 29.0% | `acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 7.8%<br>`acme/om/agent_steps/storage/impl/postgres.py:AgentStepStoragePostgresImpl.read_agent_steps` 7.3%<br>`acme/om/privacy/storage/impl/postgres.py:PrivacyStoragePostgresImpl.read_keys` 6.1% |
| 2 | request rendering | 26.9% | 0.0% | `acme/om/windows/rules.py:prompt_bytes` 13.0%<br>`acme/om/windows/rules.py:exchanges` 5.8%<br>`acme/om/windows/rules.py:_conversation` 1.6% |
| 3 | rest | 22.7% | 1.0% | `acme/infra/base.py:thaw_mapping` 2.5%<br>`acme/om/agent_steps/types/agent_step.py:shape_refusal` 2.5%<br>`acme/integrations/model_providers/content.py:storable` 2.1% |
| 4 | event loop | 3.8% | 0.0% | the loop's own callbacks 3.6%<br>`acme/om/agents/impl/loop_model_turn.py:LoopModelTurn._stream` 0.1%<br>`acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 0.0% |
| 5 | seal and open | 3.4% | 0.0% | `acme/om/privacy/impl/sealed_agent_steps.py:opened` 2.8%<br>`acme/om/privacy/impl/manager.py:PrivacyManagerImpl.keyed_hash` 0.4%<br>`acme/om/privacy/impl/sealed_agent_steps.py:bound_to` 0.1% |
| 6 | harness | 1.8% | 0.0% | `performance/replay/engine.py:request_chars` 1.1%<br>`performance/replay/engine.py:Engine.run` 0.4%<br>`performance/replay/engine.py:request_chars.<locals>.<genexpr>` 0.2% |
| 7 | object store | 0.0% | 0.0% | `acme/infra/buckets/s3.py:BucketsS3Impl.put` 0.0% |

### Ranked: 32 sessions at once, KMS emulator

| rank | where | share of wall time | of it, waiting | the code that spends it |
| ---: | --- | ---: | ---: | --- |
| 1 | database | 41.9% | 0.5% | `acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 13.0%<br>`acme/om/privacy/storage/impl/postgres.py:PrivacyStoragePostgresImpl.read_keys` 3.3%<br>the loop's own callbacks 2.9% |
| 2 | rest | 19.2% | 0.1% | `acme/om/agent_steps/types/agent_step.py:shape_refusal` 3.4%<br>`acme/om/tools/registry.py:ToolRegistry.render.<locals>.<genexpr>` 1.5%<br>`acme/integrations/model_providers/content.py:storable` 1.4% |
| 3 | request rendering | 16.8% | 0.0% | `acme/om/windows/rules.py:prompt_bytes` 10.2%<br>`acme/om/windows/rules.py:exchanges` 2.1%<br>`acme/om/windows/rules.py:_call.<locals>.<genexpr>` 1.9% |
| 4 | key service | 8.3% | 0.0% | `acme/infra/keys/kms.py:KeyServiceKmsImpl.unwrap` 8.3%<br>`acme/infra/keys/kms.py:KeyServiceKmsImpl.generate` 0.0%<br>`acme/infra/keys/kms.py:encryption_context` 0.0% |
| 5 | event loop | 6.8% | 0.0% | the loop's own callbacks 6.1%<br>`acme/om/storage/impl/pg_base.py:scoped_session` 0.2%<br>`acme/om/storage/impl/pg_base.py:ScopedConnection.execute` 0.1% |
| 6 | seal and open | 5.8% | 0.0% | `acme/om/privacy/impl/sealed_agent_steps.py:opened` 4.5%<br>`acme/om/privacy/impl/manager.py:PrivacyManagerImpl.keyed_hash` 0.7%<br>`acme/om/privacy/impl/sealed_agent_steps.py:sealed` 0.5% |
| 7 | harness | 1.2% | 0.0% | `performance/replay/engine.py:request_chars` 0.5%<br>`acme/integrations/model_providers/scripted.py:parts_of.<locals>.<genexpr>` 0.4%<br>`performance/replay/engine.py:Engine.run` 0.2% |
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
| KMS emulator | alone | 249 | 373.57 ms | 672.1 ms | 728.02 ms | 728.84 ms |
| KMS emulator | sweep-1 | 14 | 89.42 ms | 152.0 ms | 157.89 ms | 106.29 ms |
| KMS emulator | sweep-2 | 28 | 128.98 ms | 209.56 ms | 209.81 ms | 155.02 ms |
| KMS emulator | sweep-4 | 56 | 203.64 ms | 325.86 ms | 329.79 ms | 185.24 ms |
| KMS emulator | sweep-8 | 112 | 467.41 ms | 620.31 ms | 646.76 ms | 549.26 ms |
| KMS emulator | sweep-16 | 224 | 956.58 ms | 1314.74 ms | 1343.87 ms | 1263.75 ms |
| KMS emulator | sweep-32 | 448 | 1524.62 ms | 2098.21 ms | 2305.87 ms | 1570.39 ms |
| key twin | alone | 249 | 365.94 ms | 666.37 ms | 711.54 ms | 633.86 ms |
| key twin | sweep-1 | 14 | 79.99 ms | 161.5 ms | 192.98 ms | 95.93 ms |
| key twin | sweep-2 | 28 | 113.72 ms | 185.25 ms | 188.6 ms | 141.74 ms |
| key twin | sweep-4 | 56 | 199.57 ms | 297.18 ms | 325.72 ms | 176.37 ms |
| key twin | sweep-8 | 112 | 428.52 ms | 582.31 ms | 592.04 ms | 528.98 ms |
| key twin | sweep-16 | 224 | 908.71 ms | 1267.53 ms | 1299.86 ms | 1211.31 ms |
| key twin | sweep-32 | 448 | 1427.47 ms | 2054.71 ms | 2333.03 ms | 1227.0 ms |
| KMS emulator | sweep-1-unprofiled | 14 | 88.06 ms | 183.39 ms | 194.51 ms | 178.76 ms |

### One round trip on this stack

| keys | a database round trip | an unwrap |
| --- | ---: | ---: |
| KMS emulator | 0.298 ms | 0.609 ms |
| key twin | 0.302 ms | 0.006 ms |
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
