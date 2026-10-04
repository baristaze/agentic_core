# Economy

Group id: `economy`. Covers Economy of `agentic_core_spec.md`.

This group judges what an agent's requests carry and how its loops
delegate, for what they cost inside the bounds: the prefix every request
pays for, the weight of a tool definition, what crosses a delegation,
each child's caps, the model role a task names, how many calls a path
makes, what a check costs, and whether spend is a reading. It leaves a
request's layer order and its determinism to `windows`, a child's clean
start and the tree's shared bounds to `agents`, naming a model role at
all to `models`, a tool's contract to `tools`, and the gate, the hold,
and the figures a call records to `bounds`. It judges what a loop
spends inside its limits, never whether a limit holds.

## ECO-01 Every session of a kind version shares one prefix

**Principle.** The kind's prompts and its tool definitions, the two
layers every request opens with, hold no value that differs per session,
per person, or per tenant. Such a value renders below them, in the
pinned zone or with the new inputs, so every session of a kind version
shares one cached prefix. The order of the layers, and that the same
steps render the same bytes, are WIN-01's.

**Source.** Economy, What a Request Carries (One prefix per kind
version); Context, Rendering.

**Look for.** Where a kind's prompts and its tool definitions are built,
and every value formatted into them.

**Violation.** A kind prompt or a tool description filled with a
session's objective, a person's or a tenant's name, or a record; a value
of one session rendered above the pinned zone. (A time or a fresh id
rendered into a request is WIN-01.)

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/windows/types/kind.py`

**Check.** review

## ECO-02 A tool definition earns its weight

**Principle.** A tool definition rides every request of every session of
its kind, so it is paid for on every call. A kind registers the tools
its loops use. A description says what the model needs to choose the
tool and fill its input, and nothing its schema already says. A tool
that few loops need is reached through a sub-agent whose kind registers
it, rather than carried by every request.

**Source.** Economy, What a Request Carries (A tool definition earns its
weight).

**Look for.** Each kind's registry, and the description and the schema
of each tool it registers.

**Violation.** A kind that registers tools its work never needs, such as
a whole family of tools for one rare call; a description that holds a
manual, a worked example, or a restatement of its schema's fields. (A
tool with no contract at all is TOL-01.)

**Severity.** medium

**Check.** review

## ECO-03 A delegation moves a bounded objective and a bounded report

**Principle.** A spawn's objective has a size bound, and it names large
material by its handle, never pasting it. A child's report has a size
bound. Above it, the report is stored as an artifact, and the parent's
inbox holds its preview and its handle. Nothing else of the child's
crosses to its parent. That a child starts clean, without its parent's
history, is AGT-03's.

**Source.** Economy, What a Delegation Moves (A bounded objective, A
bounded report).

**Look for.** The size bound on a spawn's objective and on a child's
report; how a report reaches its parent's inbox, and what the parent's
next request renders of it.

**Violation.** An objective or a report with no size bound; a report
rendered whole into its parent's window, however large; a child's steps
or tool results copied into its parent's history.

**Severity.** medium

**Check.** review

## ECO-04 Each child runs under caps of its own

**Principle.** A child runs under its kind's step guard, sized for its
task, and under a budget scoped to its own session: a share of what the
tree has left. The tree's budget still bounds every child, and a
child's cap never adds to it; the cap only keeps one child from spending
its siblings' share. The tree's shared budget, height, count, and
deadline are AGT-05's and AGT-06's.

**Source.** Economy, What a Delegation Moves (Caps of its own).

**Look for.** What a spawn gives a child's session: the limits of the
child's kind, and any budget scoped to that session.

**Violation.** A kind that sub-agents run whose step guard is the
default every kind takes, never sized for its task; a spawn that leaves
the child no budget of its own, so one child can spend all the tree has
left. (A child's budget that adds to the tree's is AGT-06.)

**Severity.** medium

**Check.** review

## ECO-05 A mechanical task names a model role of its own

**Principle.** A title, a summary, a classification, or an extraction
needs no judgment. Each is a model role of its own, so a resolver can
fill it with a cheaper model, and none rides the main role, whose fill
is sized for the agent's hardest turn. The kind still names only model
roles, and the resolver still picks the model. That a call names a model
role, never a model, is MOD-01's.

**Source.** Economy, Fewer and Cheaper Calls (A mechanical task has a
model role of its own); Models, Roles and Fills.

**Look for.** Every model call site and the model role it names; the
model roles each kind names, and the task behind each.

**Violation.** A title, a summary, a classification, or an extraction
called under the main role; a kind whose side tasks all name the main
role.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/models/types/fill.py`

**Check.** review

## ECO-06 Fewer calls before concurrent calls

**Principle.** A loop's model calls and its sub-agents follow the
guideline's order for fixing calls: a fan-out is the last step, never
the first. A path that makes a call per item, or the same call twice,
is made cheaper by making fewer calls before it is made faster by making
them at once. How many children may run at once is AGT-05's.

**Source.** Economy, Fewer and Cheaper Calls (Fewer calls before
concurrent calls).

**Look for.** Loops and fan-outs over model calls or spawns; a model
call made once per item; a call whose result the history already holds.

**Violation.** A model call per item where one call over the items
serves; concurrent calls or sub-agents where one call or one child holds
the work; a model call made again for a result already in the history.

**Severity.** medium

**Check.** review

## ECO-07 A check costs what its risk is worth

**Principle.** A check that is itself a model call or a sub-agent, such
as a result gate that asks a model, a judge, or a reviewing kind, runs
where the work it gates calls for it: by that work's class and effect,
never on every result alike. It never runs again on an input it has
already judged. That a result gate exists and refuses a claim without
evidence is AGT-02's.

**Source.** Economy, Fewer and Cheaper Calls (A check costs what its risk
is worth).

**Look for.** Every model call or spawn made to verify, judge, or review
a result, and what decides that it runs.

**Violation.** A verifying model call or a reviewing sub-agent run on
every result alike, such as a read-only answer checked like an outward
write; the same check run twice on an unchanged input.

**Severity.** medium

**Check.** review

## ECO-08 Spend is a reading

**Principle.** What a loop spent is read from its history, never
guessed. Each model call's usage can be traced to its loop, its model
role, its kind version, and its tree, so the cost of a loop, a role, or
a kind is a query, and so is the share of its input the cache served.
The figures a call records, native usage and reference cost, are
BND-06's.

**Source.** Economy, Spend Is a Reading.

**Look for.** What a model call's recorded usage is stored with, and
whether its loop, model role, kind version, and tree can be joined to
it.

**Violation.** Usage recorded with no loop, model role, kind version, or
tree it can be traced to; usage kept only as a running total per session
or per budget, so the share of a loop or a role is lost.

**Severity.** medium

**Check.** review
