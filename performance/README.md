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
- **The reading.** A sampler reads the event loop's thread every 2 ms.
  When the loop runs code, the sample is that code's. When it waits, the
  sample is split between the sessions' tasks, each by what it waits on.
  Each sample goes to the first category its frames match, from the
  innermost out. The harness's own time, the twin's and the tools', is
  a category of its own.

## Run it

From the repository's root:

```bash
make performance        # the whole run on its own stack, about half an hour
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

READING

## The numbers

<!-- results: begin -->
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
