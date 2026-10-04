# The prompt

This is the prompt for run 2 onward. It is sent as written, with the
specification, `agentic_core_spec.md`, attached to the message. Run 1
was sent with a shorter prompt, which is in its
[results.json](../runs/browser-judge-agentic/20261002-063600/results.json).
A run records the prompt as it sent it, and two runs compare only when
both texts are the same.

```text
You are a highly senior software engineer and software architect specializing in agentic systems and AI application infrastructure.

Evaluate the attached `agentic_core` specification strictly on its software design and architecture merits.

Your goal is not merely to review `agentic_core` in isolation. Your goal is to determine:

1. What architectural problem it is actually solving.
2. How good its design is for that problem.
3. Which existing systems are its closest architectural adjacents.
4. Where `agentic_core` is concretely stronger, weaker, broader, narrower, or simply different.
5. How all compared systems score under one consistent architectural rubric.

Evaluate the material itself. Do not be biased by who wrote it, an agent or a human, by how fast it was written, or by how new it is.

## Evaluation process

Follow this order deliberately. Do not skip ahead.

### 1. Understand and classify `agentic_core`

First, read the specification in full.

Determine:

- What problem `agentic_core` is trying to solve.
- Its intended abstraction level.
- Its architectural role inside an application.
- Its core execution model.
- Its primary abstractions.
- Its intended extension points.
- What it owns.
- What it deliberately delegates elsewhere.
- What it is.
- Equally importantly, what it is not.

Do not force it into the category of "agent framework" if a more precise category describes it better. Name the category you would file it under, in a few words, and say why that name fits better than the obvious ones.

Then identify the closest existing architectural adjacents.

Potential examples include:

- Pydantic AI
- Claude Agent SDK
- LangGraph / LangChain
- OpenAI Agents SDK
- Temporal or another durable-execution engine, where the overlap is durability rather than the agent loop
- other systems that are more directly comparable to what `agentic_core` actually provides

These examples are suggestions, not mandatory choices.

Select the **2 to 3 most relevant adjacents based on architectural overlap**, not popularity.

For each selected adjacent, briefly explain why it is a relevant comparison and what portion of `agentic_core` it overlaps with.

### 2. Define and freeze the scoring rubric

Before scoring anything, define a common architectural evaluation rubric.

The rubric should fit the category you identified in step 1.

Potential areas include:

- conceptual model and abstraction quality
- separation of concerns
- composability and extensibility
- agent/model/provider independence
- state and context management
- orchestration and control-flow model
- tool/action abstraction
- streaming and event model
- observability and debuggability
- error handling and failure semantics
- determinism, replayability, and testability
- concurrency and async model
- type safety and contract clarity
- security and isolation boundaries
- cost and resource accounting
- framework interoperability and escape hatches
- operational complexity
- developer ergonomics
- architectural coherence and simplicity

Modify, combine, remove, or add criteria where appropriate after understanding the actual design.

Avoid excessive fragmentation. Prefer approximately **8 to 12 meaningful architectural criteria** rather than a long checklist of overlapping concerns.

For each criterion:

1. Give it a concise name.
2. Define exactly what it measures.
3. Describe what a `10/10` architecture would look like.
4. Assign a weight.
5. Ensure all weights sum to exactly **100**.

Once defined, **freeze the rubric**.

Do not change criteria, definitions, weights, or scoring interpretation after you start scoring `agentic_core` or its adjacents.

### 3. Evaluate `agentic_core`

Score `agentic_core` against every frozen criterion on a **0 to 10 scale**.

Use the scale consistently:

- `0-2`: fundamentally absent, broken, or architecturally unsuitable
- `3-4`: significant weaknesses or major missing pieces
- `5-6`: reasonable but materially incomplete or compromised
- `7-8`: strong architecture with identifiable limitations
- `9`: excellent, unusually strong design
- `10`: exceptional and difficult to improve materially for the intended scope

Do not treat `10` as merely "supports the feature."

For every score:

- Ground the assessment in concrete design decisions from the specification, and name the section they come from.
- Distinguish specified architecture from aspirational language.
- Identify important ambiguities and underspecified behavior.
- Do not award points for functionality that is merely implied.
- Do not penalize intentional narrowness when it improves coherence.
- Do not penalize the project simply because it is new.

Calculate the weighted result: the sum of each score times its weight, divided by 10, rounded to a whole number.

### 4. Evaluate the selected adjacents

Apply the **exact same frozen rubric** to every selected adjacent.

Use their current architecture and primary documentation or source repositories where possible. Name the version or the date of what you read.

Evaluate architectural properties, not ecosystem momentum.

Do **not** use any of the following as scoring criteria:

- age of the project
- GitHub stars
- follower count
- community size
- market share
- production adoption
- company backing
- version number
- number of integrations, where that number comes from ecosystem size rather than from the design

Where an adjacent's architecture is not documented well enough to score a criterion, say so, score only what is documented, and mark that score as low confidence. Never fill a gap with what the system probably does.

Compare like with like. Where an adjacent covers less than `agentic_core` (or more), score each criterion on what each system specifies for it, and say in a line that the scopes differ. Do not reward or penalize scope that the criterion does not measure.

Calculate each adjacent's weighted result the same way.

### 5. Compare

Put every system side by side under the frozen rubric.

Then say, concretely, where `agentic_core` is:

- stronger: a design decision an adjacent lacks, and what it buys;
- weaker: a decision an adjacent makes better, and what it costs `agentic_core`;
- broader or narrower: what one owns that the other leaves to the application;
- simply different: a trade-off where neither side is better, and when each choice wins.

Each point names the design decision behind it, not a feature list.

### 6. Recommend

Name the changes to the specification that would raise its score most, most valuable first. Each names the criterion it moves and roughly by how much. Prefer a change that removes or clarifies over one that adds.

## Report format

Answer in this shape, so evaluations compare:

- First line: `Score: NN/100`, the weighted score of `agentic_core`, and nothing else on that line.
- `## Classification`: what it is, its abstraction level, what it owns, what it delegates, and what it is not, one line each; then the category you would file it under, in a few words.
- `## Adjacents`: each selected system, why it was chosen, and the part of `agentic_core` it overlaps, one line each.
- `## Rubric`: a table of each criterion, what it measures, what a `10/10` looks like, and its weight; the weights sum to 100.
- `## Scores`: a table with one row per criterion (its weight, then each system's score) and a last row of each system's weighted result out of 100.
- `## agentic_core, criterion by criterion`: for each criterion, its score and its grounding in the specification, with the ambiguities that held it back, a short paragraph each.
- `## Where it differs`: stronger, weaker, broader or narrower, and different, one line each.
- `## What I would change`: concrete edits to the specification, most valuable first, one line each.
- `## Method`: what you read (the specification's sections, each adjacent's documentation or source with its version or date), how deep, and which scores rest on thin evidence, one line each.
```
