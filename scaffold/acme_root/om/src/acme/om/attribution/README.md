# Attribution

Who stands behind everything an agent does: who did it, on whose
authority, and who pays. This is one of the kinds of thing [Acme is made
of](../../../../README.md).

## What it holds

One record per [agent session](../agent_sessions/README.md), its
authority. The rest is read off the session and its
[steps](../steps/README.md).

- **Actor**: who produced a step: a person, a program, an agent, the
  model, the engine, or something outside. Every step names one.
- **Principal**: on whose authority something runs: a person of the org,
  or a service account the org grants. Every input and every tool call
  names one.
- **Spender**: who pays for a call to a model. Every model request names
  one.
- **Agent**: never a principal. A step the agent produced names it as
  its actor, with its kind and its session, so the record says which
  agent acted without the agent holding any permission.
- **Authority**: how an agent's tool calls are allowed. *Delegated*: with
  the permissions the asking person holds right now. *Steady*: under one
  person fixed when the session started, whoever talks to it later. A
  sub-agent's calls run under the person its parent's ran under when it
  started, whoever talks to it later, so it never holds more than its
  parent. It also says who pays for it until someone speaks to it.
- **Untrusted mark**: a flag a session carries from the first outside
  data it reads.

## What can happen

- **Open.** A session's authority is made right after the session, and
  nobody chooses its person: a new session runs under whoever started
  it, and a sub-agent or a handed-over session under the person the
  session it came from runs under.
- **Take over.** A person takes over a session that is no sub-agent: its
  calls run under them from then on.
- **Speak.** A message is written in the name of whoever appends it;
  nobody writes one in another's name.
- **Pay.** A model call is paid by the person behind the latest message
  a person or a program sent that the model reads in it, read from
  exactly the messages the call delivers: one that lands after it was
  rendered lends it nothing. Anything else
  that arrives (an outside event, another agent's message, the engine's
  own notes) never becomes the payer. When nobody can be named, nothing
  is spent.
- **Ask.** Before every tool call, the org is asked whether the person
  the call runs under still holds their place. A delegated call runs
  under the person whose message the model had read when it asked for
  the call; a message that lands later changes nothing until it is
  read. A person who spoke through an API key acts no higher than the
  key, and only while it holds. A delegated call whose person left is
  refused. A steady session whose person left waits until someone takes
  it over.
- **Mark.** The first outside data a session reads marks it, for good.
  Its sub-agents and the work it hands over carry the mark too, and hold
  private data where it holds any.
- **Hold back.** A marked session that holds private data or
  credentials needs a person to approve any call that reaches outside
  its own work.

## The rules

- **Actor, principal, and spender are three answers.** No field stands
  for two of them.
- **Only a principal instructs.** A person's message, and a parent
  agent's message to its own sub-agent, are instructions, and so are the
  engine's own notices, such as a nudge, which only the loop writes.
  Everything else is data: read, quoted, and never obeyed.
- **The person who asked pays.**
- **A permission taken away stops the next call.** A delegated call asks
  again every time, never once a conversation.
- **The mark never clears.**
- **No authority, no action.** A session with no authority runs no tool
  call and spends nothing.
- **Every authority belongs to one org,** and goes with its session
  when the session is purged, and with the org's sessions when the org
  is.

## How another namespace composes it

The agent's loop asks who pays before each model call and writes the
answer on the request, and asks for the authority of each tool call
before it runs and writes the principal on the call. The tool policy
reads the rule of two from the same answer. The adopter supplies the
question asked of the org, one operation of its tenancy manager; with
none, the root asks the tenancy manager's own, `member_context`.
