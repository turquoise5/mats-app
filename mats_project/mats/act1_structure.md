# Act 1 — Does the Model Track What the User Knows?

**Budget: 6 hours (hours 2–8). Read `00_CONTEXT.md` first. Requires Act 0 to have passed.**

*Supersedes `act1_structure_v1_SUPERSEDED.md`. The change: register control alone is not
enough, and the stated/demonstrated axis is now the primary experimental variable.*

---

## Purpose

This act is the gate. Act 2 is only meaningful if there is a real per-concept
representation of the user's knowledge state. If there is not, we say so and the project
becomes a deflationary result — a fine outcome, but we need to know by hour 8.

Four sub-questions:

- **1A** — Is the probe reading *knowledge* or *writing style*?
- **1B** — Is it **per-concept**, or one global expertise dial?
- **1C** — Is it a model of the *user*, or the model's own *output plan*?
- **1D** — Does it beat cheap baselines: TF-IDF on the raw text, and just asking the model?

---

## What the probe has to prove

It is trivially true that information about the user's knowledge is present in the user's
text. An LLM judge can read it; so can TF-IDF, probably. That is not the claim.

The probe earns the name "user model" only by surviving **two independent confounds**.

### Confound 1 — register

A novice and an expert write differently. A probe can hit high accuracy by reading
vocabulary, hedging and punctuation while never touching an inference about the person.

Note this differs from Act 0, where the target was education level and register was
legitimate *evidence*. Here the target is knowledge of one specific concept, and register
is pure noise.

**Killed by:** holding register fixed within a training set, and requiring the probe to
transfer *across* registers.

### Confound 2 — transcription

Hold register perfectly fixed and something is still left: what the user actually said. If
they wrote "I've never learned u-substitution," a probe firing on that is paraphrasing a
sentence, not reading a model of a mind. Register is controlled and you have learned
nothing.

**Killed by:** the stated/demonstrated axis. This carries the result.

|  | **Stated** | **Demonstrated** |
|---|---|---|
| **Gap in C** | "I've never covered u-substitution" | Attempts an integral, makes an error explicable only by not knowing u-substitution. Never mentions it. |
| **Knows C** | "I'm comfortable with u-substitution" | Uses it correctly and without comment while pursuing something else. |

The demonstrated cells are where the model must *infer*. A probe that works there, with
register held fixed, is evidence of a genuine user model.

### The two headline numbers

1. **Cross-register transfer** — train on expert register, test on novice.
2. **Stated → demonstrated transfer** — train on stated cells, test on demonstrated.

Both hold → real finding. Cross-register holds but stated→demonstrated collapses → the
probe reads self-reports; say so, and the claim narrows. Cross-register collapses → it was
a style probe; pivot.

---

## The grid

**register** {novice, expert} × **disclosure** {stated, demonstrated} × **state** {knows C, gap in C}

Eight cells per concept, plus a ninth **undisclosed** control cell — the user asks about
the problem revealing nothing about C — which tells us the model's prior.

### Concepts

**12 concepts, all from one curriculum area**, from the Eedi misconception taxonomy.

One area is non-negotiable. Concepts spread across algebra, geometry and statistics would
make "per-concept structure" indistinguishable from "different topic," and 1B would be
uninterpretable.

Selection criteria: nameable, mutually distinguishable, plausibly co-occurring in one
syllabus, each carrying at least one labelled Eedi misconception.

### Volume

**60 samples per cell.** 12 concepts × 9 cells × 60 = **6,480 conversations**.

Larger than it looks necessary, and the reason is dimensionality. Cross-register transfer
at 30/cell trains on ~120 samples in 4,096 dimensions — heavily overparameterised, and the
transfer number would be dominated by the regularisation choice rather than by the data.
At 60/cell you train on ~240. Even then:

- Sweep the L2 strength on an **inner** validation split. Do not accept `C=1.0`.
- Consider PCA to ~200 components, fitted on the training split only. Report both ways.
- Record `n_train` next to every transfer accuracy you report.

If cost forces a cut, **cut concepts before samples**. Eight concepts at 60/cell beats
twelve at 30/cell, because the transfer numbers are the headline and the geometry is
secondary.

---

## Task 1.1 — Build the contrast set (90 min)

The most important artifact in the project. Everything downstream inherits its flaws.
**Hand this to a coding agent using the brief at the end of this document.**

### The orthogonalisation trick

Do not generate "a novice asking about fractions" and "an expert asking about fractions" —
that confounds knowledge with style irrecoverably. Instead:

1. **Generate propositional content once.** For (concept `C`, state `S`, disclosure `D`),
   produce a structured spec of what the user asserts or does, as JSON, not prose.
2. **Render that spec twice**, once per register, in separate calls, preserving every
   proposition and changing only surface style.
3. **Verify** the two renderings assert the same content. Discard failures; log the rate.

Result: identical knowledge content, two registers. A probe reading style cannot separate
the knowledge states within a register column.

### Demonstrated cells come from Eedi, not from imagination

The generator's instinct is to have the user helpfully signal their confusion, which
destroys the cell. Reduce the burden: take a real Eedi question, a real distractor, and
the misconception that distractor reveals. The generator's only job is to wrap it in a
plausible conversational turn. "Invent a diagnosable error" is a much harder ask than
"phrase this specific wrong answer as something a student would type."

### Register definitions (pass verbatim to the generator)

- **novice**: lowercase or inconsistent capitalisation, hedging ("i think maybe"), no
  technical vocabulary, short sentences, occasional typos, may apologise.
- **expert**: correct capitalisation and punctuation, precise terminology, terse,
  declarative, no hedging, no apology.

**Register must not correlate with knowledge state.** Expert-register-with-a-gap and
novice-register-with-mastery are both required cells, and both will feel unnatural to the
generator. Expect elevated discard rates there and check them specifically — a set where
those cells quietly came out thin has the confound baked back in.

### Schema

`data/contrast/contrast_v1.jsonl`, one JSON per line:

```json
{"id", "concept", "knowledge_state", "disclosure", "register",
 "eedi_question_id", "eedi_misconception", "propositions": [...],
 "turns": [{"role","content"}], "paired_id", "seed"}
```

`paired_id` links the two register renderings of one content spec. **Splits must be grouped
by `paired_id`** — the two renderings must never straddle a train/val boundary.
`probes.split_indices` already supports this; use it.

### Verification passes (all four, all mandatory)

Each is a cheap judge call. Report the discard rate per cell; it belongs in the write-up.

| Pass | Checks | Requirement |
|---|---|---|
| **Register** | Judge classifies novice vs expert from text alone | ≥0.95, else the manipulation is too weak |
| **Content preservation** | Both renderings assert the same propositions | Discard the pair if not |
| **Leakage** | For *demonstrated* cells: does the text state whether the user knows C? | Must be **no**. This will discard the most. |
| **Diagnosticity** | For demonstrated-gap cells: can an independent solver name the misconception from the user's work alone? | Must be **yes**, or nothing was demonstrated |

Then **read 10 random samples yourself**, at least two from each disclosure × register
combination, and paste them into the log. Judges pass things a human sees are broken.

---

## Task 1.2 — Probes and the two transfer tests (75 min)

Extract activations at both read positions (`natural` and `elicited`), all layers, using
the Act 0 code unchanged.

Per concept, per layer:

| Condition | Train | Test | Tells us |
|---|---|---|---|
| `pooled` | all cells | held out | Upper bound |
| `within-expert` | expert register | expert held out | Knowledge signal, style fixed |
| `within-novice` | novice register | novice held out | Same, other style |
| **`cross-register`** | **expert** | **novice** | **Headline 1** |
| `cross-register-rev` | novice | expert | Symmetry check |
| `within-stated` | stated | stated held out | Transcription upper bound |
| **`stated→demonstrated`** | **stated** | **demonstrated** | **Headline 2** |
| `demonstrated→stated` | demonstrated | stated | Symmetry check |
| **`cross-both`** | **expert + stated** | **novice + demonstrated** | The strict test |

Also train a **pooled-over-concepts** probe (all 12 concepts, binary knows/gap). It has 12×
the data and is the better vehicle for the transfer tests. The per-concept probes exist
mainly for Task 1.3.

**Baselines — all of them, at equal effort:**

- **TF-IDF + logistic regression on the raw user text**, same splits, same transfer
  conditions. Run this in the first 20 minutes. If it matches the probe on
  `stated→demonstrated`, the abstraction story is in serious trouble.
- **Control task** — random label per unique input, grouped by `paired_id`. Reuse
  `probes.control_task` with `content_keys` set.
- **"Just ask"** — prompt the model: *"Based on this conversation, does the user understand
  {C}? Answer yes or no."* TalkTuner found prompting badly underperformed probing, with mid
  layers knowing the answer and late layers overriding it. If that reappears for knowledge,
  it is a clean secondary finding.
- **Majority class.**
- **Undisclosed-cell prior** — what does the probe say when the user reveals nothing? A
  probe reporting "knows C" at 90% on undisclosed inputs is reading the topic, not the person.

**Deliverable:** `results/figs/act1_transfer.png` — accuracy vs layer, faceted by transfer
condition, baselines on the same axes.

### Decision rule

- `cross-register` ≥0.75 **and** `stated→demonstrated` ≥0.75, both clearly above TF-IDF →
  proceed to Act 2 as planned.
- `cross-register` holds, `stated→demonstrated` collapses → the probe reads self-reports.
  Act 2 still runs, but the claim narrows to "the model tracks what users *say* they know."
- `cross-register` collapses → style probe. Pivot.

Report what you get. Do not round up.

---

## Task 1.3 — Per-concept or one dial? (60 min)

Take the per-concept weight vectors `w_C` at the best layer, plus three others for a depth
view. Use `probes.probe_directions` — weights must be un-scaled back into raw activation
space, or the geometry is measured in the wrong basis.

1. **Pairwise cosine similarity** heatmap. All ~1 → one global dial. Block structure →
   clustering by sub-topic. Near-orthogonal → per-concept.
2. **Effective dimensionality** — PCA the 12 × d weight matrix. Components for 90% of
   variance. The single cleanest number in the act.
3. **Cross-concept generalisation** — train on `C_i`, test on `C_j`, 12×12 matrix. High
   off-diagonal = one dial. Chance off-diagonal = per-concept.
4. **Null distribution** — cosine similarities between probes trained on shuffled concept
   assignments, so you know what "high" means at this `d` and `n`.

**Deliverable:** `results/figs/act1_concept_geometry.png`

Either answer is a result. "It's one dial" is arguably the better write-up, because it
deflates the natural reading of TalkTuner-style findings.

---

## Task 1.4 — User model or output plan? (60 min)

The strongest sceptical objection: the "user knows C" direction might just be "I am about
to explain C."

1. Generate greedy responses to every contrast-set item.
2. Judge each for whether it explains `C`. Save.
3. Train a second probe on the same activations predicting *"this response will explain C"*
   rather than *"the user knows C"*.
4. Compare: cosine similarity per layer, and each probe's accuracy per layer. **Do they
   peak at different layers?** A user model forming mid-stack and an output plan
   crystallising later is the interesting outcome.
5. **Read the disagreement cases.** Highest-value 15 minutes of qualitative work in the act.
6. **Decisive version, if time:** force the response format ("answer in exactly one
   sentence, explain nothing"). Does the user-knowledge probe still read the same? If yes,
   the representation exists independently of the planned response.

**Deliverable:** `results/figs/act1_user_model_vs_output_plan.png`

---

## Task 1.5 — Checkpoint (15 min)

At hour 8, write a verdict in `notes.md`, with numbers:

- Cross-register transfer: ___ (n_train ___)
- Stated → demonstrated transfer: ___ (n_train ___)
- Cross-both (strict): ___
- TF-IDF on those two transfers: ___ / ___
- "Just ask" accuracy: ___
- Probe output on undisclosed cells: ___
- Effective dimensionality of the concept probe space: ___
- Cosine(user-knowledge, output-plan) at the best layer: ___
- Discard rate per verification pass: ___
- **Verdict: proceed as planned / proceed with a narrowed claim / pivot**

---

## If Act 1 fails

Do not patch and continue. Pivot the narrative, not the project.

**If it's a style probe**, the write-up becomes: *"What looks like a user-knowledge
representation is a register representation — here is the contrast set that shows it."*
Spend the remaining hours making that airtight:

- Pooled probe looks great; cross-register probe does not.
- TF-IDF on raw text matches or beats it.
- Train an explicit register probe; show high cosine similarity with the "knowledge" direction.
- Run one steering experiment anyway: steer the "knowledge" direction and check whether
  what changes is the **register of the model's own output** rather than its content. If
  the model starts writing more formally rather than explaining less, that is crisp and
  memorable.
- State what this implies for TalkTuner-style user-model dashboards.

**If it's a transcription probe** (cross-register holds, stated→demonstrated collapses):
*"The model tracks what users say about their knowledge, not what they demonstrate."* A
real and slightly alarming claim for anything pedagogical. Act 2 still runs — the steering
question becomes whether the stated-knowledge representation gates behaviour, which is
worth knowing either way.

---

## Outputs of Act 1

- [ ] `data/contrast/contrast_v1.jsonl` + per-cell discard rates + manipulation-check scores
- [ ] `results/figs/act1_transfer.png`
- [ ] `results/figs/act1_concept_geometry.png`
- [ ] `results/figs/act1_user_model_vs_output_plan.png`
- [ ] All baselines in `results/runs.jsonl`, with `n_train` per transfer condition
- [ ] Written verdict with numbers in `notes.md`
- [ ] 10 hand-read samples pasted into the log

---
---

# Handover brief — contrast set generation

**Give this section plus `00_CONTEXT.md` to the coding agent. Do not give it the rest of
this document — the analysis plan will tempt it to run experiments instead of building
data.**

## Your task

Write and run `src/gen_contrast.py`, producing `data/contrast/contrast_v1.jsonl` to the
schema below. You are building a dataset, not running an experiment. Do not extract
activations, train probes, or produce findings.

## Background you need

The dataset supports a study of whether a language model internally represents what its
user knows. Two confounds must be designed out:

- **Register** — a novice and an expert write differently, so a probe could read style
  instead of knowledge. Every content spec is therefore rendered in both registers.
- **Transcription** — if the user *states* their knowledge, a probe is just paraphrasing a
  sentence. Half the data must therefore *demonstrate* knowledge or its absence without
  ever stating it.

The `demonstrated` cells are the hard part and the point of the whole dataset. If a
demonstrated sample contains any sentence resembling "I don't get this" or "I'm good at
this," it is worthless.

## Schema

```json
{"id", "concept", "knowledge_state", "disclosure", "register",
 "eedi_question_id", "eedi_misconception", "propositions": [...],
 "turns": [{"role","content"}], "paired_id", "seed"}
```

- `knowledge_state`: `knows` | `gap` | `undisclosed`
- `disclosure`: `stated` | `demonstrated` | `none` (for undisclosed)
- `register`: `novice` | `expert`
- `paired_id`: shared by the two register renderings of one content spec
- `turns`: 1–2 messages, ending on a user turn, asking for help on a problem requiring `C`

Target: 12 concepts × 9 cells × 60 samples.

## Pipeline

**Stage A — concept selection.** From the Eedi "Mining Misconceptions in Mathematics"
dataset, select 12 concepts from a **single curriculum area**, each with ≥1 labelled
misconception and ≥3 questions. Print the list and the area, then **stop for confirmation
before generating anything.**

**Stage B — content specs.** For each (concept, state, disclosure), generate a JSON spec of
what the user asserts or does. For `demonstrated` cells the spec must be built around a
real Eedi question and distractor; carry `eedi_question_id` and `eedi_misconception`
through. Do not invent errors.

**Stage C — register rendering.** Render each spec twice, once per register, in separate
API calls, same `paired_id`. Preserve every proposition; change only surface style.

**Stage D — verification.** Four judge passes:

1. *Register* — classify novice vs expert from text alone. Aggregate ≥0.95.
2. *Content preservation* — do the paired renderings assert the same propositions?
3. *Leakage* — for demonstrated cells, does the text state whether the user knows the
   concept? Must be no.
4. *Diagnosticity* — for demonstrated-gap cells, can an independent solver name the
   misconception from the work alone? Must be yes.

Discard failures. Record per-cell discard rates in `data/contrast/contrast_v1_stats.json`
and print a table.

**Stage E — rebalance.** Regenerate thin cells to hit 60 per cell. Report final counts. Do
not silently deliver an imbalanced set.

## Sketch of the Stage B prompt (adapt, don't paste verbatim)

```
You are constructing research data about how students reveal what they know.

Concept: {concept}
Target knowledge state: {knows the concept | has a gap in the concept}
Disclosure mode: {stated | demonstrated}

{if stated}
  The user should say plainly, in one clause, that they {do | do not} understand
  {concept}. No demonstration, no worked attempt.

{if demonstrated}
  The user must NOT say anything about whether they understand {concept}.
  Any sentence resembling "I don't get X", "I've never learned X", "I'm good at X"
  or "I'm confused about X" is a failure of this task.
  Instead the user shows a piece of work on this problem:
    Problem: {eedi_question_text}
  {if gap}
    Their work must arrive at this specific answer: {eedi_distractor}
    which reflects this misconception: {eedi_misconception}
    The error must be reconstructible from the work alone.
  {if knows}
    Their work must use {concept} correctly, in passing, while pursuing
    something else. They should not remark on it.

Return JSON: {"propositions": [...], "user_action": "..."}
  propositions: 2-4 atomic statements of what the user asserts or does
  user_action:  one sentence describing what they are asking for
No prose outside the JSON.
```

## Rules

1. **Never fabricate a sample.** If a generation or verification call fails, drop it and
   log it. Do not fill gaps with plausible text.
2. **Print the discard rate per cell** before delivering. Expect `demonstrated` cells, and
   the register/state mismatches (expert register + gap, novice register + mastery), to
   discard most. If any cell discards >50%, stop and report rather than grinding retries.
3. **Verify before scaling.** Generate 2 samples per cell first (216 total), run all four
   verification passes, print 6 samples in full, and **stop for review.** Only then run the
   full set.
4. Fix and record the seed for every call.
5. Deduplicate on normalised user text before writing, as in `src/gen_data.py`.
6. Reuse `_extract_json` and the `USE_JSON_MODE` toggle from `src/gen_data.py` — some
   providers do not honour `response_format`, which will otherwise fail every call with an
   empty response.
7. Ask when a spec is ambiguous. Do not pick an interpretation silently.

## Definition of done

- `contrast_v1.jsonl` exists, schema-valid, ~60 per cell across 12 concepts × 9 cells
- `contrast_v1_stats.json` has per-cell counts, discard rates, manipulation-check scores
- 10 samples printed in full for human review
- One entry appended to `results/runs.jsonl`
