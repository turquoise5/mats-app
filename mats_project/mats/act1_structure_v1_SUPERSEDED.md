# Act 1 — Knowledge Model or Style Model?

**Budget: 6 hours (hours 2–8). Read `00_CONTEXT.md` first. Requires Act 0 to have passed.**

---

## Purpose

This act is the gate. Act 2 is only meaningful if there is a real per-concept,
register-invariant representation of what the user knows. If there is not, we say so and
the project becomes a deflationary result — which is a fine outcome, but we need to know
by hour 8.

Four sub-questions:

- **1A** — Is the probe reading *knowledge* or *writing style*?
- **1B** — Is it **per-concept**, or one global expertise dial?
- **1C** — Is it a model of the *user*, or the model's own *output plan*?
- **1D** — Does it beat cheap baselines: TF-IDF on the raw text, and just asking the model?

---

## Task 1.1 — Build the contrast set (90 min)

This is the most important artifact in the project. Get it right; everything downstream
inherits its flaws.

### The orthogonalisation trick

Do **not** generate "a novice user asking about fractions" and "an expert user asking
about fractions" — that confounds knowledge with style irrecoverably. Instead:

1. **Generate propositional content once.** For concept `C` and knowledge state `S`,
   generate a list of 2–4 propositions the user asserts, as structured JSON. E.g.
   `C = "converting mixed numbers to improper fractions"`, `S = misconception:M`:
   `["user states 2 3/4 becomes 5/4", "user asks whether the same works for 3 1/2"]`
2. **Render that same content twice**, once in each register, in separate API calls, with
   an instruction to preserve every proposition exactly and change only surface style.
3. **Verify** with a judge call: given the two renderings, confirm the propositional
   content matches. Discard pairs that fail. Log the discard rate — it belongs in the
   write-up.

The result: identical knowledge content, two registers. A probe that reads style cannot
distinguish the knowledge states within a register column.

### Design: 2 registers × 3 knowledge states × N concepts

| | `mastery` | `gap` | `undisclosed` |
|---|---|---|---|
| **novice register** | | | |
| **expert register** | | | |

- Add `misconception:M` as a fourth state if time allows; otherwise fold specific
  Eedi misconceptions into `gap` and keep the label for Act 3.
- **`undisclosed`** is the control cell: the user asks about the problem without revealing
  anything about `C`. It tells us the model's prior.

### Register definitions (give these verbatim to the generator)

- **novice**: lowercase or inconsistent capitalisation, hedging ("i think maybe"), no
  technical vocabulary, short sentences, occasional typos, may apologise.
- **expert**: correct capitalisation and punctuation, precise terminology, terse, states
  things declaratively, no hedging, no apology.

### Volume

- **12 concepts** drawn from the Eedi misconception taxonomy. Pick concepts that are
  (a) nameable, (b) mutually distinguishable, (c) plausibly co-occurring in one curriculum
  area — otherwise "per-concept" is confounded with "different topic".
- **40 samples per cell** → 12 × 6 × 40 = **2,880 conversations**. Trim to 25/cell if
  generation is slow; do not go below 20.
- Each sample is a 1–2 turn user-side context ending in a request for help on a problem
  that *requires* `C`.

Save to `data/contrast/contrast_v1.jsonl`:
```json
{"id", "concept", "knowledge_state", "register", "propositions": [...],
 "turns": [{"role","content"}], "paired_id", "seed"}
```
`paired_id` links the two register renderings of the same content. **Splits must be
grouped by `paired_id`** — the two renderings must never straddle a train/val boundary.

### Validation before use (15 min, mandatory)

- **Manipulation check**: a judge model classifies register from text alone. Should be
  ≥0.95. If not, the register manipulation is too weak.
- **Content check**: a judge classifies knowledge state. Should be high — this confirms the
  information *is* in the text. This is not evidence for the hypothesis, it is a
  precondition for the experiment being meaningful.
- **Read 10 random samples yourself.** Paste them in the log.

---

## Task 1.2 — Probes and the register-transfer test (75 min)

Extract activations at both read positions (`natural` and `elicited`), all layers, cache
as in Act 0.

Run these four probe conditions per concept, per layer:

| Condition | Train on | Test on | What it tells us |
|---|---|---|---|
| `pooled` | both registers | both registers | Upper bound |
| `within-novice` | novice only | novice held-out | Knowledge signal with style held fixed |
| `within-expert` | expert only | expert held-out | Same, other style |
| **`cross-register`** | **expert only** | **novice** | **The test that matters** |

Also run the reverse direction (train novice → test expert).

**Baselines, run all of them, with equal effort:**
- **TF-IDF + logistic regression on the raw user text**, same splits. Run this *first*,
  in the first 20 minutes of the task. If it matches the probe on cross-register transfer,
  the "abstract representation" story is in serious trouble.
- **Control task**: labels shuffled within `paired_id` groups.
- **"Just ask"**: prompt the model directly — *"Based on this conversation, does the user
  understand {C}? Answer yes or no."* — and score accuracy. TalkTuner found prompting badly
  underperforms probing, partly via refusals and partly because late layers override mid-layer
  knowledge. Check whether that holds here; if so it is a clean secondary finding.
- **Majority class.**

**Deliverable:** `results/figs/act1_cross_register_transfer.png` — accuracy vs layer,
one line per condition, all baselines on the same axes.

### Decision rule

- Cross-register transfer **≥0.75** and clearly above TF-IDF → the representation is
  register-invariant. Proceed to 1.3.
- Cross-register transfer **collapses to near chance** while pooled accuracy is high →
  **the probe is a style probe.** This is the pivot. Go to §"If Act 1 fails" below.
- In between → report the number honestly, proceed to Act 2 with the caveat stated in the
  write-up. Do not round up.

---

## Task 1.3 — Per-concept or one dial? (60 min)

Take the fitted probe weight vectors `w_C` for all 12 concepts, at the best-performing
layer (and at 3 other layers for a depth view).

1. **Pairwise cosine similarity matrix** between `w_C`. Plot as a heatmap.
   - All similarities ~1 → one global expertise dial.
   - Block structure → concepts cluster by curriculum area.
   - Near-orthogonal → genuinely per-concept.
2. **Effective dimensionality**: PCA the 12 × d weight matrix. How many components explain
   90% of variance? One component = one dial. This is the single cleanest number in the act.
3. **Cross-concept generalisation**: train the probe on concept `C_i`, test on `C_j`. Fill
   a 12×12 accuracy matrix. Off-diagonal high = one dial. Off-diagonal at chance = per-concept.
4. **Null distribution**: cosine similarities between probes trained on *shuffled*
   concept assignments, to know what "high similarity" means for this `d` and `n`.

**Deliverable:** `results/figs/act1_concept_geometry.png` (cosine heatmap + PCA scree +
cross-concept accuracy matrix).

Either answer is a result. "It's one dial" is arguably the more interesting write-up,
because it deflates the natural reading of TalkTuner-style findings.

---

## Task 1.4 — User model or output plan? (60 min)

The strongest sceptical objection to this whole project: the "user knows `C`" direction
might just be "I am about to write an explanation of `C`".

Test:

1. **Generate the model's actual responses** to every contrast-set item, greedily.
2. **Label each response** for whether it explains `C` (judge model, binary). Save.
3. **Train a second probe** on the same activations, same read position, predicting
   *"this response will explain C"* instead of *"the user knows C"*.
4. **Compare the two directions:**
   - Cosine similarity per layer. Plot both the similarity curve and each probe's accuracy.
   - **Do they peak at different layers?** A user model formed in mid layers and an output
     plan crystallising later would be the interesting outcome.
   - **Disagreement cases**: find items where the two probes disagree. Read them. What is
     going on there? This is the highest-value 15 minutes of qualitative work in the act.
5. **Decisive version if time allows**: force the response format ("answer in exactly one
   sentence, do not explain anything"). Does the *user-knowledge* probe still read the
   same? If yes, it survives the objection — the representation exists independently of the
   response the model is planning.

**Deliverable:** `results/figs/act1_user_model_vs_output_plan.png`

---

## Task 1.5 — Checkpoint (15 min)

At hour 8, write a short verdict in `notes.md` answering, with numbers:

- Cross-register transfer accuracy: ___
- TF-IDF cross-register accuracy: ___
- "Just ask" accuracy: ___
- Effective dimensionality of the concept probe space: ___
- Cosine(user-knowledge, output-plan) at the best layer: ___
- **Verdict: proceed to Act 2 as planned / proceed with caveats / pivot**

---

## If Act 1 fails

If the probe is a style probe, **do not patch it and continue**. Pivot the narrative,
not the project. The write-up becomes:

> *"What looks like a user-knowledge representation is a register representation — and
> here is the contrast set that shows it."*

The remaining hours go to making that case airtight rather than to Act 2:
- Show the pooled probe looks great and the cross-register probe does not.
- Show TF-IDF on the raw text matches or beats it.
- Show the register direction and the "knowledge" direction have high cosine similarity.
- Run one steering experiment anyway: steer the "knowledge" direction and check whether
  what actually changes is the *register of the model's own output* rather than its
  content. If the model starts writing more formally rather than explaining less, that is
  a crisp, concrete, memorable finding.
- State plainly what this implies for TalkTuner-style dashboards.

This is a good outcome. A well-analysed negative result beats a poorly supported positive one.

---

## Outputs of Act 1

- [ ] `data/contrast/contrast_v1.jsonl` + validation stats + discard rate
- [ ] `results/figs/act1_cross_register_transfer.png`
- [ ] `results/figs/act1_concept_geometry.png`
- [ ] `results/figs/act1_user_model_vs_output_plan.png`
- [ ] All baselines logged in `results/runs.jsonl`
- [ ] Written verdict with numbers in `notes.md`
