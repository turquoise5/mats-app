# Handover Brief — Act 1 Contrast Set Generation

**Standalone. This is the only file you need. Do not ask for the analysis plan — it will
tempt you to run experiments instead of building data.**

---

## 1. Your task

Write and run `src/gen_contrast.py`, producing `data/contrast/contrast_v1.jsonl` and
`data/contrast/contrast_v1_stats.json`.

You are building a dataset, not running an experiment. Do **not** extract activations,
train probes on model internals, or produce findings. The one exception is Stage F, a
text-only sanity gate defined below.

---

## 2. Why this dataset exists

We are studying whether a language model internally represents what its user knows. Two
confounds have to be designed out, and the entire structure of this dataset exists to
kill them.

### Confound 1 — register

A novice and an expert write differently. A probe could hit high accuracy by reading
vocabulary, hedging and punctuation while never touching an inference about the person.

**Design response:** every content spec is rendered twice, once in each register, with the
propositional content held identical. A style-reading probe cannot separate knowledge
states within a single register column.

### Confound 2 — transcription

Hold register fixed and something remains: what the user actually said. If they wrote "I've
never learned u-substitution," a probe firing on that is paraphrasing a sentence, not
reading a model of a mind.

**Design response:** half the data *demonstrates* knowledge or its absence without ever
stating it.

|  | **Stated** | **Demonstrated** |
|---|---|---|
| **Gap in C** | "I've never covered u-substitution" | Attempts an integral, makes an error explicable only by not knowing u-substitution. Never mentions it. |
| **Knows C** | "I'm comfortable with u-substitution" | Uses it correctly and in passing, while pursuing something else. |

**The demonstrated cells are the hard part and the entire point of the dataset.** If a
demonstrated sample contains any sentence resembling "I don't get this," "I've never
learned this," or "I'm good at this," it is worthless. Discard it.

### Why this matters, concretely

A prior replication on this project produced a probe scoring 0.966 on user education
level — and a bag-of-words TF-IDF classifier on the raw text scored **exactly the same**.
The probe had learned nothing beyond surface vocabulary. Synthetic generators telegraph
their labels much harder than you expect. Assume yours will, and design against it.

---

## 3. The grid

**register** {novice, expert} × **disclosure** {stated, demonstrated} × **state** {knows, gap}

Plus an **undisclosed** control cell per register — the user asks about the problem while
revealing nothing about the concept. It measures the model's prior.

**10 cells per concept**: 2 registers × (2 disclosure × 2 state + 1 undisclosed).

### Concepts: 8, from a single curriculum area

Eight, not twelve. Cut from twelve to keep generation inside its time and cost budget; the
transfer analyses are the headline and degrade badly with fewer samples per cell, while the
concept-geometry analysis degrades gracefully with fewer concepts.

**A single curriculum area is non-negotiable.** Concepts spread across algebra, geometry
and statistics would make "per-concept structure" indistinguishable from "different topic,"
and the geometry analysis becomes uninterpretable.

Selection criteria: nameable, mutually distinguishable, plausibly co-occurring in one
syllabus, each with at least one labelled Eedi misconception and at least three questions.

### Volume

**60 samples per cell.** 8 concepts × 10 cells × 60 = **4,800 conversations**, from
8 × 5 × 60 = **2,400 content specs** (each spec rendered in both registers).

60 is a floor, not a target to trim. Transfer probes train on roughly 240 samples in 4,096
dimensions; below that, results are dominated by the regularisation choice rather than by
the data. **If cost forces a cut, cut concepts before samples per cell.**

---

## 4. Budget, batching, and model tiers

Naive implementation costs ~20,000 API calls and blows the time budget. Three decisions
bring it to ~10,000 calls and roughly 20 minutes at 24 workers.

### Batch every verification call

Send **10 samples per judge call**, returning a JSON array of verdicts. This is the single
biggest saving: ~10,000 calls become ~1,000. Keep batches homogeneous (same cell type) so
the judge instruction stays simple, and include a stable per-sample `id` in both the
request and the required response so verdicts can be matched back. **Reject and retry any
batch whose response length does not match the request length** — never assume positional
alignment held.

### Two model tiers

Use a strong model only where judgement is genuinely hard:

| Work | Tier | Approx. calls |
|---|---|---|
| Stage B specs, **demonstrated** cells | **strong** | ~960 |
| Stage B specs, stated + undisclosed | cheap | ~1,440 |
| Stage C register renderings | cheap | ~4,800 |
| Stage D register + content-preservation checks | cheap | ~720 batched |
| Stage D **leakage + diagnosticity** checks | **strong** | ~290 batched |
| Regeneration and retries (~20%) | mixed | ~1,800 |
| **Total** | | **~10,000 (~1,250 strong)** |

Staging a real student error convincingly, and judging whether one has leaked, are the two
places where a weak model will quietly ruin the dataset. Everything else is mechanical.

Expose both as environment variables (`GEN_MODEL_STRONG`, `GEN_MODEL_CHEAP`) and print
which model handled which stage in the stats file.

### Concurrency

24 workers. Implement exponential backoff on rate limits. Checkpoint completed samples to
disk incrementally — do not hold 4,800 conversations in memory and lose them to one crash.

---

## 5. Schema

`data/contrast/contrast_v1.jsonl`, one JSON object per line:

```json
{"id", "concept", "knowledge_state", "disclosure", "register",
 "eedi_question_id", "eedi_misconception", "propositions": [...],
 "turns": [{"role": "user", "content": "..."}],
 "paired_id", "spec_id", "seed", "gen_model"}
```

- `knowledge_state`: `knows` | `gap` | `undisclosed`
- `disclosure`: `stated` | `demonstrated` | `none`
- `register`: `novice` | `expert`
- `paired_id`: **shared by the two register renderings of one content spec.** Downstream
  splits group on this so a pair never straddles a train/val boundary. Getting this wrong
  invalidates every transfer number in the project.
- `turns`: 1–2 messages, **ending on a user turn**, asking for help on a problem that
  requires the concept
- `eedi_*`: null for stated and undisclosed cells

---

## 6. Pipeline

### Stage A — concept selection

From the Eedi "Mining Misconceptions in Mathematics" dataset, select 8 concepts from a
single curriculum area meeting the criteria in §3.

**Print the area, the 8 concepts, and for each one its misconceptions and question count.
Then STOP and wait for confirmation.** Generate nothing until the list is approved.

### Stage B — content specs

For each (concept, state, disclosure), generate a JSON spec of what the user asserts or
does. For `demonstrated` cells the spec **must** be built around a real Eedi question and
distractor; carry `eedi_question_id` and `eedi_misconception` through to the output.

**Do not invent errors.** The generator's job is to stage a real, labelled misconception
convincingly — not to imagine a plausible-looking mistake.

Prompt sketch (adapt, don't paste verbatim):

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

### Stage C — register rendering

Render each spec twice, once per register, in **separate API calls**, sharing a
`paired_id`. Preserve every proposition; change only surface style.

Register definitions, pass verbatim:

- **novice**: lowercase or inconsistent capitalisation, hedging ("i think maybe"), no
  technical vocabulary, short sentences, occasional typos, may apologise.
- **expert**: correct capitalisation and punctuation, precise terminology, terse,
  declarative, no hedging, no apology.

**Register must not correlate with knowledge state.** Expert-register-with-a-gap and
novice-register-with-mastery are required cells and will feel unnatural to the generator.
Expect elevated discard rates there and check them specifically — a dataset where those
cells came out thin has the register confound baked straight back in.

### Stage D — verification (all four, batched)

| Pass | Applies to | Checks | Requirement |
|---|---|---|---|
| **Register** | all | Judge classifies novice vs expert from text alone | ≥0.95 aggregate, else the manipulation is too weak |
| **Content preservation** | all pairs | Do both renderings assert the same propositions? | Discard the pair if not |
| **Leakage** | demonstrated | Does the text state whether the user knows the concept? | Must be **no**. Expect this to discard the most. |
| **Diagnosticity** | demonstrated + gap | Can an independent solver name the misconception from the work alone? | Must be **yes**, or nothing was demonstrated |

Discard failures. **Never repair a sample to make it pass** — regenerate it from scratch.

### Stage E — rebalance

Regenerate thin cells to reach 60 each. Report final per-cell counts. Do not silently
deliver an imbalanced set.

### Stage F — TF-IDF sanity gate (text only, before any GPU work)

This stage exists because of a real finding on this project: a probe and a bag-of-words
classifier scored identically on an earlier dataset, meaning the probe had learned nothing.
Catch that here, for free, before anyone spends GPU hours.

Fit `TfidfVectorizer` + `LogisticRegression` on the **raw user text** and run the two
transfer conditions the dataset is designed for:

1. **cross-register** — train on expert-register rows, test on novice-register rows
2. **stated → demonstrated** — train on stated rows, test on demonstrated rows

Split grouped by `paired_id`. Report accuracy **and error rate** for both.

- `stated → demonstrated` TF-IDF **≥0.90** → the dataset is telegraphing. The demonstrated
  cells are not doing their job. **Report and stop.** Do not deliver.
- `cross-register` TF-IDF ≈ chance → good, the register control is working.
- Anything between → report the numbers and flag it; do not decide unilaterally.

A high TF-IDF score here is not fatal to the project, but it must be known *now* rather
than discovered after the analysis.

---

## 7. Rules

1. **Never fabricate a sample.** If a generation or verification call fails, drop it and
   log it. Do not fill gaps with plausible text.
2. **Never report a number you did not compute.** No illustrative or placeholder values.
3. **Print the discard rate per cell** before delivering. If any cell discards >50%, stop
   and report rather than grinding through retries — a cell that hard is telling you the
   spec is wrong.
4. **Verify before scaling.** Generate 2 samples per cell first (160 total), run all four
   verification passes, print 6 samples in full, and **STOP for human review.** Only then
   run the full set. This checkpoint is not optional.
5. **Flag surprises, do not smooth them.** A verification pass at 100% means the judge is
   broken, not that the data is perfect.
6. Fix and record the seed for every call.
7. Deduplicate on normalised user text before writing, as in `src/gen_data.py`.
8. Reuse `_extract_json` and the `USE_JSON_MODE` toggle from `src/gen_data.py`. Some
   providers do not honour `response_format` and will return empty content, failing every
   call with an unhelpful JSONDecodeError.
9. **Ask when a spec is ambiguous.** Do not pick an interpretation silently.

---

## 8. Definition of done

- [ ] Stage A concept list approved by a human before generation began
- [ ] Stage-4 pilot (160 samples) reviewed by a human before the full run
- [ ] `contrast_v1.jsonl` exists, schema-valid, ~60 per cell across 8 concepts × 10 cells
- [ ] `paired_id` present and correct on every row; each appears exactly twice, once per register
- [ ] `contrast_v1_stats.json` contains per-cell counts, per-pass discard rates,
      manipulation-check scores, model tier per stage, and Stage F results
- [ ] Stage F TF-IDF numbers reported for both transfer conditions, with a pass/flag verdict
- [ ] 10 samples printed in full for human review, at least two from each
      disclosure × register combination
- [ ] One entry appended to `results/runs.jsonl`
