# dreamcatcher

![The engine's run as a scene: a person, the engine in our cloud, the model in its cloud, and the hands outside every cloud, wired, with a light running along a wire for each step the engine records](dreamcatcher.gif)

The engine, in this process, played as a scene. It runs the scaffold's
engine as it sits, on its memory storage, with the scripted twin as its
model and a scripted director as its person. It reads no key, opens no
connection, and bills nothing.

## What it shows

One task in three acts, played as one run at speed: an agent fixes a
failing check in a small workspace.

1. **Simple.** One loop: plan, read, run, edit, run, done.
2. **Smart.** The person steers while a tool runs, and the next model
   request carries the steer. The agent asks the person, and parks
   until the answer comes. Two sub-agents review the change, and their
   reports reach the parent's history.
3. **Power.** The budget gate holds each call's worst case before the
   call starts. It parks the loop before a call that would cross the
   cap, and a raise wakes it. A runner dies in the middle of a tool
   call. The next run takes a new writer epoch and settles the call by
   its effect, so the edit lands once, and the old runner's write is
   refused.

## How to read the scene

Each step the engine records is a light on a wire, in the direction it
travels. The legend at the left gives each light's color.

- **The person**, on the left, apart from every cloud. Their message,
  their steer, their answer, and the raise of the budget travel to the
  engine. The engine's question travels back to them.
- **The engine**, in our cloud: a dreamcatcher's hoop with a
  three-bladed rotor at its hub. Each light that reaches it turns the
  rotor 120° clockwise, and a ripple runs through the web. Above it
  stands the session's state as its history says it: running, parked on
  its reason, or idle. Beside it, the writer epoch.
- **The model**, in the model's cloud. A request travels to it, and its
  response streams back as a burst of lights.
- **The hands**, outside every cloud, where the workspace's tools act.
  A tool call reaches them, the fingers move, and the screen shows the
  effect: the file read, the check failing and then passing, the edit
  landing.
- **The sub-agents**: two small engines that appear beside the engine,
  wired to the model and the hands. Their reports travel back to it.
- **The numbers**, along the bottom, roll like a gas meter: steps,
  loops, model calls, tool calls, tokens, the epoch, and the spend
  against the cap, with the hold the gate takes before each call.

When act 3's runner is lost, the engine goes dark and a new runner
takes the session at the next epoch. The edit lands once, and the old
runner's late write comes back as a red light the engine refuses.

The page plays a tape: every step of every session the engine recorded,
in the order it wrote them, each with the numbers the record gives at
that step. The page computes no number of its own.

## Run it

From the repository's root:

```bash
make demo         # runs the engine, writes the tape, and opens the scene
make demo-check   # headless: each act, the ledger, and the tape checked against the record
make demo-gif     # records dreamcatcher.gif from the scene, frame by frame
```

The scene needs no build and no server: it opens from
`scene/index.html`, and it reaches no network. The tape is committed, so a
clone opens it as it is.

`make demo-check` runs with a provider's key in its environment and
every outbound connection refused. It holds that the only model
provider the demo builds is the scripted twin, that each act happens in
the history, and that the closing ledger equals the record read back.
It holds the tape to the record too: every step of every session, in
the order the engine wrote them, its last numbers equal to the ledger
read back, and the committed tape the one a fresh run writes. CI runs
it.

`make demo-gif` drives the page in headless Chromium through
Playwright, on the browser build Playwright installs once, and joins
the frames with ffmpeg. It aborts any request outside the page's
folder, and fails on a page error, a console error, such a request, or
a GIF of 6 MB or more. CI does not run it.

## Where things are

- [`dreamcatcher/script.json`](dreamcatcher/script.json): the words.
  Each act's model turns, in the scripted twin's own format, and what
  the person says.
- [`dreamcatcher/acts.py`](dreamcatcher/acts.py): the cues. When a run
  starts, when the person speaks, when a runner is lost.
- [`dreamcatcher/engine.py`](dreamcatcher/engine.py): the engine, wired
  in process.
- [`dreamcatcher/workspace.py`](dreamcatcher/workspace.py): the
  workspace and its three tools.
- [`dreamcatcher/projection.py`](dreamcatcher/projection.py): each
  number, as a query over the record, and the record as it stood at
  any step.
- [`dreamcatcher/tape.py`](dreamcatcher/tape.py): the run, written as
  the tape the scene plays.
- [`dreamcatcher/checks.py`](dreamcatcher/checks.py): what `--check`
  holds.
- [`scene/`](scene/): the page. `index.html` and `scene.js` draw it,
  and `tape.js` is the run they play.
- [`record.py`](record.py): the recorder.

It sits outside the scaffold, so no copy of the scaffold carries it.
