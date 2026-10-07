# Lenses

A lens holds the checkable detail under one rule of
[`agentic_core_spec.md`](../agentic_core_spec.md), cited here as *the
spec*: the exact field, call, or breach a reviewer looks for in code. It
is stricter than the spec on purpose, and never contrary to it. It never
adds a rule the spec does not state. A review loads one group of lenses
at a time, which keeps it narrow enough to be thorough.

The lenses hold the engine's rules only. Where the spec leans on a rule
of the guideline, the guideline's own [lenses][g-lenses] hold it, and a
lens here names that rule without restating it.

## Groups

| Group id  | Prefix | File         | Covers |
|-----------|--------|--------------|--------|
| `steps`   | `STP`  | `steps.md`   | The Engine and the Brain; Steps; Loops, Runs, and Sessions; History |
| `windows` | `WIN`  | `windows.md` | Context |
| `models`  | `MOD`  | `models.md`  | Models |
| `tools`   | `TOL`  | `tools.md`   | Tools; The Runtime |
| `live`    | `LIV`  | `live.md`    | Streams; Steering |
| `trust`   | `TRU`  | `trust.md`   | Identity, Trust, and Attribution |
| `agents`  | `AGT`  | `agents.md`  | Agent Kinds and Sub-Agents |
| `bounds`  | `BND`  | `bounds.md`  | Bounds and Budgets; Parking |
| `economy` | `ECO`  | `economy.md` | Economy |
| `privacy` | `PRV`  | `privacy.md` | Privacy; Null Objects |

The groups are the ones the spec plans in The Repository. `steps` also
homes The Engine and the Brain, the loop every group's rules run in.

A rule belongs to one group, and to one lens in it. The `Covers` column
names each group's home sections. Where two groups touch one section,
the paragraph at the top of each file draws the line. A lens may cite a
section outside its group's home when its rule rests there: Testing and
Conformance is cited by the lenses whose interfaces it tests, and The
Object Model and Deviations from the Guideline by the lens of the noun or
the rule they name. Where two lenses meet one breach, the one a reviewer
reaches first names the other in parentheses, and the lens it names
carries the severity.

## Lens format

The format is the guideline's, with the spec in place of
`architecture.md`:

```markdown
## STP-01 Title of the lens

**Principle.** The rule, in a few short sentences, in the spec's voice.

**Source.** Loops, Runs, and Sessions, Durable by Default.

**Look for.** What to inspect: files, signatures, declarations, call sites.

**Violation.** What evidence of a breach looks like, concretely.

**Severity.** high | medium | low

**Shape.** `scaffold/acme_root/<path>`

**Check.** review
```

Ids are the group prefix and two digits: `STP`, `WIN`, `MOD`, `TOL`,
`LIV`, `TRU`, `AGT`, `BND`, `ECO`, `PRV`.

- **Principle** is at most 120 words. A rule that needs more is two
  lenses.
- **Source** names the section and, after a comma, the subsection, by
  title as the spec spells them, never by number. Several citations are
  separated by `;`, and a bare subsection after a `;` belongs to the
  section cited before it. A title may hold commas of its own, as
  `Loops, Runs, and Sessions` does. A subsection's citation may end in
  the bold labels of the paragraphs it means, in parentheses:
  `Context, Compaction (Overflow)`.
- **Look for** and **Violation** are one to three sentences each,
  concrete enough that two reviewers flag the same line.
- **Severity** is `high` for a breach that spends outside the gate, lets
  a secret into a step, weakens isolation, switches a model silently,
  lets text grant power, writes past a lost claim, or repeats an unsafe
  call on recovery: the spec's list in The Repository. `medium` bends a
  shape the spec relies on, and `low` is a convention. A lens that cites
  only `style` sections is `low`.
- **Shape** is optional. It names one or two files or folders of the
  scaffold, `scaffold/acme_root/<path>` in backticks, that show the rule
  as code. A review reads the file and compares the code with it. A lens
  has one only where a scaffold file shows its rule plainly.
- **Check** reads `review` when the review alone judges the lens. The
  checker the scaffold carries, `scaffold/acme_root/checkers/`, decides
  what a program can decide without guessing, by a rule with the lens's
  id, and says so here in the guideline's two sentences:
  "`agentic-check` decides it." when it decides the whole lens, and
  "`agentic-check` decides `<the part>`; the rest is judged." when the
  review judges what it leaves.

`make lenses` holds the format, a width of 80 columns, the citations,
each Shape path, the Check line against the checker's rules, and every
identifier a lens quotes to the section it cites. That a lens stays
inside its rule, stricter and never contrary, is held by review, not by
a program.

[g-lenses]: https://github.com/baristaze/swe_guidelines/blob/v0.52.1/lenses/README.md
