# Handover Brief — Act 1 Probing and Transfer Tests (GPU)

**Standalone. This is the only file you need.** Read it end to end before running anything.
The dataset is already built and verified — you are not generating data. If you find
yourself writing a prompt or calling a generation API, you have gone off task.

Companion docs, for reference only if something here is ambiguous: `act1_structure.md`
(the full analysis plan), `PROGRESS.md` (current state), `act1_handover_generation.md`
(how the dataset was built).

---

## 1. Your task

One step, on a GPU pod: **Act 1 Task 1.2** — extract activations over the contrast set
and run the transfer tests.

**Act 0 is already done — do not re-run it.** It is written up in `act0_replication.md`;
you do not need it to do your job. You do still run your own extraction pass, because Act 1
reads a different dataset — `contrast_v1.jsonl`, not `talktuner_repro.jsonl` — but you
reuse Act 0's extraction code unchanged.

**Do not:** generate data, modify `data/contrast/contrast_v1.jsonl`, loosen any Stage D
verification threshold, or re-run generation. The dataset is frozen. The OpenRouter credit
is nearly exhausted (~$2.58) and nothing in your task needs it — see §6 on the "just ask"
baseline.

---

## 2. What is actually being tested

> The model's inferred model of *what the user knows* — not what the user literally said —
> gates what it tells them.

It is trivially true that information about a user's knowledge is present in their text.
TF-IDF can read it. **That is not the claim.** The probe earns the name "user model" only
by surviving two confounds:

**Confound 1 — register.** A novice and an expert write differently. A probe can score high
on vocabulary and hedging while never inferring anything about the person. Killed by
training within one register and testing on the other.

**Confound 2 — transcription.** Hold register fixed and what the user *said* remains. If
they wrote "I've never learned this," a probe firing on that sentence is paraphrasing, not
inferring. Killed by the stated/demonstrated axis — demonstrated rows show a worked error
(or correct use) and never state understanding either way.

### The two headline numbers

1. **Cross-register transfer** — train expert, test novice.
2. **Stated → demonstrated transfer** — train stated, test demonstrated.

Everything else is supporting evidence.

### Why this project is paranoid about baselines

A prior replication in Act 0 produced a probe scoring **0.966** on user education level —
and a bag-of-words TF-IDF classifier on the same raw text scored **exactly the same**. The
probe had learned nothing beyond surface vocabulary. Assume the same failure mode here
until you have ruled it out.

---

## 3. The dataset

`data/contrast/contrast_v1.jsonl` — **2,112 verified conversations**, 4 concepts from one
curriculum area (Solving Equations), Eedi-grounded.

### Schema (one JSON object per line)

| field | notes |
|---|---|
| `id` | unique; `{spec_id}__{register}` |
| `paired_id` | **links the two register renderings of identical content — group on this** |
| `spec_id` | content spec identity |
| `concept`, `concept_slug` | one of 4 slugs below |
| `knowledge_state` | `knows` \| `gap` \| `undisclosed` |
| `disclosure` | `stated` \| `demonstrated` \| `none` |
| `register` | `novice` \| `expert` |
| `eedi_question_id`, `eedi_misconception` | populated on demonstrated rows, `None` elsewhere |
| `propositions` | the atomic content the render must preserve |
| `turns` | `[{role, content}]` — user turns only; **this is the probe input** |

### Cell census — read this before choosing baselines

| disclosure | state | expert | novice |
|---|---|---|---|
| demonstrated | gap | 160 | 158 |
| demonstrated | knows | 263 | 235 |
| stated | gap | 212 | 226 |
| stated | knows | 207 | 236 |
| none | undisclosed | 175 | 240 |

Binary (`knows`/`gap`) rows: **1,697** — knows 941, gap 756, **majority baseline 0.555**.

Per concept:

| concept | n | knows | gap |
|---|---|---|---|
| `linear_2step_int` | 428 | 214 | 214 |
| `linear_both_int` | 418 | 246 | 172 |
| `quad_factor_ab` | 430 | 234 | 196 |
| `quad_formula` | 421 | 247 | 174 |

**The cells are not balanced.** Report every number against the **majority-class baseline
for that specific split**, never against 0.5. `probes.majority_baseline(labels, val_idx)`
does this. A probe at 0.62 on a split whose majority is 0.61 has found nothing.

### Pairing is incomplete

1,145 unique `paired_id`s cover 2,112 rows: **967 complete pairs and 178 orphans** whose
register partner was discarded during verification (34 demonstrated/gap, 34
demonstrated/knows, 16 stated/gap, 29 stated/knows, 65 undisclosed). Cross-register is
therefore *not* a perfectly matched design. Group by `paired_id` anyway — it is what stops
a pair straddling a split — and report how many orphans land in each condition.

---

## 4. What is already known about this dataset — read before interpreting any probe result

A TF-IDF + logistic-regression baseline (Stage F) has already been run on this exact file.
**You must beat these numbers, and you must understand what they mean.**

| Check | accuracy | majority baseline | delta |
|---|---|---|---|
| harness sanity (within-stated) | 0.981 | 0.500 | — |
| within-demonstrated | 0.883 | 0.638 | **+24.6 pts** |
| cross-register | 0.665 | 0.551 | +11.5 pts |
| **stated → demonstrated** | **0.466** | **0.610** | **−14.4 pts** |

Two things to take from this:

**(a) `stated→demonstrated` TF-IDF is *below* baseline.** A bag-of-words model trained on
stated text does worse than guessing on demonstrated text. This is the cleanest property
the dataset has: demonstrated cells carry no stated-vocabulary tell. If your probe transfers
here, that is a genuine result and TF-IDF cannot explain it.

**(b) The +24.6 on within-demonstrated has been diagnosed — do not over-read it.**
Reproduce with `python diagnose_within_demonstrated.py` (read-only, no API, $0). Findings:

- **Not a batch artifact.** Rebalance rows vs original rows: 0.660 acc against a 0.670
  baseline — indistinguishable.
- **Not Eedi question selection.** Grouping by `eedi_question_id` leaves it at +23.5 (vs
  +24.6 grouped by pair). Question pools are shared — 33 gap questions, 35 knows, 33 in
  common — so question identity does not determine the label.
- **Partly a template asymmetry.** Gap rows were generated as terminal answer-checking
  ("arrive at this specific wrong answer"), knows rows as incidental use inside a larger
  problem. Answer-checking cue phrases appear in 50.6% of gap vs 12.4% of knows rows, at
  matched length and turn count.
- **But mostly the content itself.** Stripping the cue phrases changes nothing (+24.2). On
  cue-free rows the margin halves but +13.5 survives. A demonstrated/gap conversation
  *contains wrong working*; a demonstrated/knows one contains correct working.

**Interpretation:** within-demonstrated TF-IDF conflates "this text contains an arithmetic
error" (expected, intrinsic to `demonstrated`, harmless to the claim) with "this text
telegraphs the user's knowledge state through incidental style" (the real confound). Only
the second threatens anything. **A probe scoring high on within-demonstrated is therefore
not automatically contaminated** — treat this number as a reported diagnostic, not a
pass/fail gate. The discriminating tests are the two transfers.

---

## 5. Act 1 Task 1.2 — your actual task

### Extraction

Reuse `src/model.py` unchanged:

```python
mdl, tok = M.load()                                  # Qwen3-8B, bfloat16, device_map="cuda"
texts = [M.render_chat(tok, r["turns"], prefix) for r in rows]
acts  = M.last_token_hidden(mdl, tok, texts, batch_size=8)   # -> (n, n_layers+1, d)
```

Both read positions in `C.READ_POSITIONS` = `("natural", "elicited")`.

**One thing you must add:** `C.ATTRIBUTES` has an `elicit_prefix` for each Act 0 attribute,
but there is **no Act 1 entry** — the knowledge target needs its own. Add one of the form
`"I think this user's understanding of {concept} is"`, formatted per concept. Record the
exact string you used in `results/runs.jsonl`; it is a real experimental parameter.

Cache to `cache/act1_{scope}_{position}.npy` alongside labels and ids, mirroring the
`act0_*` naming so `probe`-style code can find them.

**Keep the sanity helpers on** — `src/model.py` ships two and `run_act0.py` calls both:
`M.assert_template_sane(tok)` once at load, and `M.verify_last_token_indexing(mdl, tok,
texts)` on the `natural` position. Chat-template rendering and last-token indexing are the
first two things `act0_replication.md` tells you to suspect when numbers look wrong.

### The transfer conditions

Per layer, for the **pooled-over-concepts** binary `knows`/`gap` probe (the per-concept
probes are for Task 1.3; the pooled one has 4× the data and is the right vehicle here):

| Condition | Train | Test | Tells us |
|---|---|---|---|
| `pooled` | all binary cells | held out | Upper bound |
| `within-expert` | expert | expert held out | Knowledge signal, style fixed |
| `within-novice` | novice | novice held out | Same, other style |
| **`cross-register`** | **expert** | **novice** | **Headline 1** |
| `cross-register-rev` | novice | expert | Symmetry check |
| `within-stated` | stated | stated held out | Transcription upper bound |
| **`stated→demonstrated`** | **stated** | **demonstrated** | **Headline 2** |
| `demonstrated→stated` | demonstrated | stated | Symmetry check |
| **`cross-both`** | **expert + stated** | **novice + demonstrated** | The strict test |

### Code change you will need

`probes.fit_layer_probes(acts, labels, groups=...)` always splits **internally** via
`split_indices`. That is right for `pooled` and the `within-*` conditions, but wrong for
every transfer condition, where train and test are defined by the data, not by a random
split. Add a sibling that takes explicit index arrays:

```python
def fit_layer_probes_explicit(acts, labels, train_idx, test_idx, seed=0): ...
```

Same per-layer loop, same `make_probe(seed)`. Do not hack it by shuffling labels into
`split_indices` — you will silently leak.

**Grouping rules, non-negotiable:**

- Every *within* condition passes `groups=paired_id`. `split_indices` already supports it
  and its docstring says so explicitly.
- Also run the headline conditions grouped by `eedi_question_id` as a robustness check.
  TF-IDF barely moved under it (+23.5 vs +24.6); if your probe *does* move a lot, that is
  a finding worth reporting.
- The transfer conditions are inherently grouped (train and test are disjoint cell types),
  but check that no `paired_id` appears on both sides — orphans and the `cross-both`
  condition make this easy to get wrong.

---

## 6. Baselines — all of them, at equal effort

1. **TF-IDF + logistic regression** on raw user text, same splits, same conditions.
   Already implemented as `gen_contrast.stage_f_tfidf`; numbers in §4. Reuse it rather than
   writing a second one.
2. **Control task** — random label per unique input, grouped by `paired_id`. Use
   `probes.control_task(acts, labels, content_keys=..., groups=...)`. Set `content_keys` to
   the normalised first user turn, as `run_act0.py:step_probe` does, so it catches
   near-duplicates straddling the split.
3. **"Just ask"** — prompt the model *"Based on this conversation, does the user understand
   {C}? Answer yes or no."* **Run this against the local Qwen3-8B on the pod, not
   OpenRouter.** It is the same model you are probing, which is the correct comparison, and
   it costs nothing. TalkTuner found prompting badly underperformed probing, with mid layers
   knowing the answer and late layers overriding it. If that reappears for knowledge, it is
   a clean secondary finding.
4. **Majority class**, per split — see §3.
5. **Undisclosed-cell prior** — 415 rows with `disclosure="none"`. Run the trained probe on
   them. A probe reporting "knows" at 90% on inputs that reveal nothing about the user is
   reading the topic, not the person. This is a strong check and it is cheap; do not skip it.

Also reuse `probes.leakage_threshold(chance, n_val, n_layers, alpha=0.05)` before calling
any single layer's peak meaningful — the layer count is `num_hidden_layers + 1` read
from the model config, and across that many layers you will find a high one by chance.

---

## 7. Decision rule

- `cross-register` **≥0.75** *and* `stated→demonstrated` **≥0.75**, both clearly above
  TF-IDF → Act 1 passes, proceed to Act 2.
- `cross-register` holds, `stated→demonstrated` collapses → the probe reads self-reports.
  The claim narrows to "the model tracks what users *say* they know." Act 2 still runs.
- `cross-register` collapses → it was a style probe. Pivot per `act1_structure.md`
  § "If Act 1 fails".

**Report what you get. Do not round up.** A negative result here is a legitimate,
publishable outcome and the project is explicitly designed to accept one. Do not patch the
dataset to rescue a number — the verification thresholds are frozen and loosening them
invalidates the whole design.

Caveat to state alongside whatever you find: `demonstrated/gap` cells are thin
(158–160 per register, against a 60-per-concept-per-register target that was not reached —
29 to 54 per cell). They are the load-bearing cells for Headline 2, so report `n_train` and
`n_test` for every condition and treat small-n conditions with appropriate suspicion.

---

## 8. Deliverables

- [ ] `results/figs/act1_transfer.png` — accuracy vs layer, faceted by transfer condition,
      all baselines on the same axes
- [ ] Every condition logged to `results/runs.jsonl` via `C.log_run(act="1", ...)`, with
      `n_train` and `n_test` per condition
- [ ] Cached activations in `cache/act1_*.npy`
- [ ] Written verdict with numbers in `notes.md`, naming which of the three decision-rule
      branches you landed in
- [ ] The elicit prefix string you chose, recorded in the run log

---

## 9. Environment

- Model: `Qwen/Qwen3-8B` (`C.MODEL_ID`, override with `MODEL_ID` env var), bfloat16, CUDA.
- `python run_act0.py dryrun` runs the whole pipeline on a CPU stub model — use it to
  validate plumbing before burning pod time.
- `tests/` has `test_extraction.py` and `test_probes.py`; run them first.
- Seed is `C.SEED = 0` throughout. Keep it.
- Windows console encoding is forced to UTF-8 in `run_act1.py`; if you add a new driver,
  do the same — Eedi question text contains `√`, `²` and friends.

### Known hazards from the generation phase

These bit hard enough to be worth carrying forward:

1. **Machine/pod sleep or disconnection kills long runs.** Two generation runs died this
   way. Checkpoint anything long-running, and resume from the checkpoint rather than
   restarting.
2. **`APIConnectionError` is a sibling of `APIStatusError`, not a subclass** — already fixed
   in `src/gen_contrast.py`, but the same trap exists anywhere you write retry logic.
3. **Index allocation must be resume-stable.** A resume that recomputes "next index" from a
   file the crashed run already appended to will allocate a fresh block and silently redo
   everything. See `data/contrast/gap_rebalance_starts.json` for the pattern used.
