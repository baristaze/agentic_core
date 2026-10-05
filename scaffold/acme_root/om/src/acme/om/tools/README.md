# Tools

Where an agent touches the world: what it may do, who must agree first,
and what may be done again after a crash. This is one of the kinds of
thing [Acme is made of](../../../../README.md).

## What it holds

- **Tool**: one thing an agent can do, such as run a command or push a
  branch. It says what it is called, what it does, what input it takes,
  and how long it may run. It names its **class**, the kind of power it
  uses (reading, writing files, running code, reaching the network,
  acting on a bound system, starting other agents, changing
  configuration or credentials, or something that cannot be undone), and
  its **effect**: whether doing it twice is harmless. A tool comes from
  the product's own code, from a server over the Model Context Protocol,
  or from another agent, and all three say the same things.
- **Registry**: the tools one kind of agent has. An agent can do only
  what its registry holds, whatever the model asks for.
- **Policy**: who must agree to a call. It looks at the tool, its class,
  its effect, and what the call acts on (whether a branch is protected),
  and never at what the model says about the call. The agent's kind sets
  the defaults, the org narrows or loosens them, and the platform's
  ceilings hold above both: what cannot be undone, or acts outside, waits
  for a person. A session that has read outside data and holds private
  data or credentials waits for a person before any call that acts
  outside, whatever policy says. A command run in a workspace whose
  network is open acts outside too.
- **Approval**: a person's yes or no to one exact call, the tool and its
  input. It expires.
- **Job**: work that outlives a run, such as a long build. Its tool
  starts it and names it, and the agent waits for it without holding
  anything. It has a deadline of its own, never later than the whole
  task's. A job that costs money by the hour says how much, at most.
- **The engine's own tools**: five a kind may name. Four act on nothing
  outside the session: asking its person, writing its plan, reading part
  of a file attached to the session, and waiting on its sub-agents. They
  read and keep the session's own records, so their class is reading.
  The fifth starts a sub-agent, and its class is `spawn`.

## What can happen

- **Ask.** A call that needs a person waits. Whoever the org lets approve
  that class of call says yes or no; a no comes back to the agent with
  the person's note, and the agent changes its plan.
- **Run.** An allowed call runs in the session's workspace, by the
  shortest of its own time, the engine's limit, and what is left of the
  whole task's time. Everything it started stops when its time is up.
- **Fail.** A failure says what kind it is (bad input, worth retrying,
  out of time, refused, cut off, will not work), with advice the agent
  reads. A test that fails is not a failure of the tool: it is the
  result. A call worth retrying, of a tool that is safe to repeat, is run
  again once by the engine before the agent hears of it. An input the
  agent wrote that is not one JSON object is bad input, and never runs.
- **Recover.** After a crash, a call that is safe to repeat runs again; one
  that is not is never repeated: the workspace's own record says how it
  ended, or the agent is told its outcome is unknown. The record keeps
  what the command printed sealed under the session's key.
- **Wait on a job.** A job's call starts the work and the agent's loop
  waits on it. The system the job runs on reports how it ended, naming
  the job; the report wakes the agent, which reads it as the call's
  answer before anything else. A job that costs money is checked against
  the budgets before it starts, and a job that would pass one never
  starts. A job with no report by its deadline is stopped and answered
  as out of time, and so is one whose agent is stopped or whose loop
  ends any other way (ADR 1013).
- **Ask the person.** The agent asks its person a question, or stops
  and says what it needs. Its loop waits, holding nothing, and the
  person's next message is the answer it goes on with.
- **Keep a plan.** Each plan the agent writes is kept in the session's
  history as one more version. The latest is shown to the agent at the
  end of each request, and a person reads it among the session's steps.
- **Read an attachment** a range of lines or pages at a time, at most a
  bounded amount a call. A line or page longer than one read is read on
  from the offset the read before stopped at. How a file turns into text
  is the product's.
- **Start a sub-agent** with a title, an objective that stands on its
  own, and a kind, the agent's own unless it names another. The child
  takes the call's id, so a call asked again finds the child it made,
  and a bound the tree sets is the call's failure.
- **Wait on sub-agents.** The call answers the children still running,
  and the agent's loop waits, holding nothing, until one reports. With
  none running, it is refused.
- **Purge.** When a deleted session is purged, its workspace and the
  records of its commands go with its history.

## The rules

- **The model's words decide nothing.** Policy reads the call's facts,
  never its explanation.
- **A message buys no power.** A person starts or talks to an agent only
  if the org lets them make every kind of call it has (ADR 1012).
- **An approval is for one call.** A different input is a different call,
  and asks again. The call is known by a hash of its input keyed by the
  session ([privacy](../privacy/README.md)), so the same input hashes
  apart in two sessions, and confirms nothing once the key is revoked.
- **A secret never enters a step.** A tool names the secrets it may use.
  Each use is recorded by name, the value is kept out of everything the
  agent sees, and the engine's own credentials never reach a tool.
- **Isolation is never weakened.** A workspace that cannot be had as the
  session asks is refused, never swapped for something weaker.
- **A report wakes only its own job.** A job's report counts only when
  it names a job the agent waits on, by the tool's name for it, in the
  session it is sent to, and only the first one does. Any other is
  refused, with nothing written and nothing woken.
- **A call reads its own session.** A tool that reads the session's
  records reads the one the call was made in, never one its input names:
  a file another session holds answers as one that does not exist.
- **Every policy belongs to one org.**

## How another namespace composes it

The agent's loop asks where each call stands, runs it, and writes the
response it gets back into the session's [steps](../steps/README.md). A
person's decision lands in the same history. Where a tool runs, the
workspace and the commands in it, is the infrastructure's
([infra](../../../../../infra/README.md)).
