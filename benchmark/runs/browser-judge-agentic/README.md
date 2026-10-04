# browser-judge-agentic

The subject is the spec, `agentic_core_spec.md`, attached to four chat
products a reader would use: chatgpt.com, claude.ai,
gemini.google.com, and grok.com, each signed in, in a browser. Each is
asked to evaluate it as a senior engineer and architect, score it from
0 to 100, and set it beside its closest adjacents. The method, and what
a row means, are the guideline's, in
[browser-judge-swe](https://github.com/baristaze/swe_guidelines/blob/v0.50.0/benchmark/runs/browser-judge-swe/README.md).
This page keeps what differs.

There is no judge. Each score is the product's own, from the answer. The
prompt for run 2 onward is [prompt.md](../../browser/prompt.md), and it
asks for a report whose first line is `Score: NN/100`. Run 1 was sent by
hand with a shorter prompt and no report format, so its prompt is in its
`results.json`, each answer gives its score in its own words, and it
compares with no later run. Two rows compare when their runs share the
sizes, the prompt, and the contract, as each `results.json` records
them; the Set column marks them.

Each run's folder holds its `results.json` and one answer per session,
redacted. An answer is the product's own Markdown from the copy button
under it, and its header says so where it is page text instead.
`[redacted]` marks each place a conversation's address, a name the
product knows the person by, or the name of another repository stood. No
screenshot is checked in.

The columns are the guideline's: the run's folder, its start, the commit
the spec stood at (`repository_head`), the sizes, one cell for each
product (the score linking the answer, then the model and effort labels
the page shows), the set, and a note. "—" marks what a run does not
record.

| Run | Started (UTC) | Head | Sizes | chatgpt.com | claude.ai | gemini.google.com | grok.com | Set | Note |
|---|---|---|---|---|---|---|---|---|---|
| [20261002-063600](20261002-063600/results.json) | 2026-10-02 06:36 | `57f4a09` | —, — | [92](20261002-063600/chatgpt.com.md) Extra High | [80](20261002-063600/claude.ai.md) | [91](20261002-063600/gemini.google.com.md) 3.1 Pro | [84](20261002-063600/grok.com.md) | A | A manual run with the first prompt; gemini.google.com.md is page text, as its share page has no copy button |
