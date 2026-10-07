# browser-judge-agentic

The subject is this repository, read whole at its default branch from
its public URL by four chat products a reader would use: chatgpt.com,
claude.ai, gemini.google.com, and grok.com, each signed in, in a
browser. Each is asked to evaluate it as a senior engineer and
architect, score it from 0 to 100, and set it beside its closest
adjacents. The prompt is [prompt.md](../../browser/prompt.md). Two
t-shirt sizes pick each site's model and effort, as
[sizes.yaml](../../browser/sizes.yaml) maps them.
[agentic-benchmark-browser](../../../skills/agentic-benchmark-browser/SKILL.md)
runs it: the guideline's `arch-benchmark-browser`, bound to this
scenario. What a row means is the guideline's, in
[browser-judge-swe](https://github.com/baristaze/swe_guidelines/blob/v0.52.0/benchmark/runs/browser-judge-swe/README.md).
This page keeps what differs.

There is no judge. Each score is the product's own, from the answer's
`Score: NN/100` line, which the prompt's report format asks for first.
The report format is part of the prompt, so nothing is sent after it,
and a run's `contract` is null. Two rows compare when their runs share
the sizes, the prompt, and the contract, as each `results.json` records
them; the Set column marks them. This page is in the repository the
products evaluate, so a product can read it. A run where an answer
names this folder or an earlier run, or says what an earlier run
scored, read the earlier scores: it is a set of its own, and its note
says which site read them.

Run 1 was sent by hand, with a shorter prompt, no report format, no
sizes, and the spec, `agentic_core_spec.md`, attached as a file. Its
prompt is in its `results.json`, each answer gives its score in its own
words, and it compares with no later run. It predates the skill and its
schema.

Each run's folder holds its `results.json` and one answer per session,
redacted. An answer is the product's own Markdown from the copy button
under it, and its header says so where it is page text instead.
`[redacted]` marks each place a conversation's address, a device's or a
person's name, or the name of another repository of the person's stood.
No screenshot is checked in. When `agentic-benchmark-browser` runs in a
checkout of this repository, it writes the run's folder here and adds
its row at the top.

The columns are the guideline's: the run's folder, its start, the commit
the repository's default branch stood at (`repository_head`), the sizes,
one cell for each product (the score linking the answer, then the model
and effort labels the page shows), the set, and a note. "—" marks what a
run does not record.

| Run | Started (UTC) | Head | Sizes | chatgpt.com | claude.ai | gemini.google.com | grok.com | Set | Note |
|---|---|---|---|---|---|---|---|---|---|
| [20261007-145103](20261007-145103/results.json) | 2026-10-07 14:51 | `a5ad9cc` | m, m | — | [77](20261007-145103/claude.ai.md) Opus 5.5, High | — | — | B | — |
| [20261007-074959](20261007-074959/results.json) | 2026-10-07 07:49 | `3929a0c` | m, m | [89](20261007-074959/chatgpt.com.md) Latest, High | [not-run](20261007-074959/claude.ai.md) | [refused](20261007-074959/gemini.google.com.md) 3.1 Pro | [86](20261007-074959/grok.com.md) Expert | B | the sizes and the prompt differ from every other run's; claude.ai: the conversation page said "Computer actions available"; gemini.google.com: it declined, saying it has no live internet access to read the GitHub URL |
| [20261002-063600](20261002-063600/results.json) | 2026-10-02 06:36 | `57f4a09` | —, — | [92](20261002-063600/chatgpt.com.md) Extra High | [80](20261002-063600/claude.ai.md) | [91](20261002-063600/gemini.google.com.md) 3.1 Pro | [84](20261002-063600/grok.com.md) | A | A manual run with the first prompt; gemini.google.com.md is page text, as its share page has no copy button |
