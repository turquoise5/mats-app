# Handover — CPU Agent: Ablations A and B (text only, no GPU)

**Standalone. You need this file, `data/contrast/contrast_v1.jsonl`, and the Eedi source
data. Nothing else.**

---

## 1. Why this exists

A probe reads whether a user understands an algebra concept from *demonstrated* work —
0.899 accuracy against a 0.743 bag-of-words baseline, splits grouped by
`eedi_question_id`.

That result is compatible with two very different stories, and we do not currently know
which is true:

- **Correctness story** — the model evaluates the arithmetic and forms a representation of
  the user's competence from it.
- **Style story** — the model reads *manner of writing*. TF-IDF coefficients on this data
  key "gap" on `circled`, `option`, `my`, `wrote`, `answer`, and key "knows" on
  `verification`, `discriminant`, `factoring`, `yields`, `back`, `original`. Mastery
  samples narrate a verification step; gap samples pick an option and report it.

Your job is to build the text variants that let a later GPU run distinguish these. You are
producing **datasets**, not conclusions.

**Do not attempt to fix or improve the data.** A previous attempt stripped a list of cue
words and refit TF-IDF: 0.883 → 0.879, essentially no change, because bag-of-words reroutes
weight onto correlated features. Ablations are for *measuring* a contribution, not removing
it. If a variant shows no drop, that is a result.

---

## 2. Scope

Only rows with `disclosure == "demonstrated"` (approx. 816 rows: ~318 gap, ~498 knows).
Stated and undisclosed rows are out of scope and must be carried through unmodified or
omitted — your choice, but be consistent and say which.

---

## 3. What to build

Four variants, plus the unmodified original as the reference.

| Variant | File | What changes |
|---|---|---|
| `orig` | `contrast_v1.jsonl` (existing, **do not modify**) | nothing |
| **A** | `contrast_v1_ablA.jsonl` | the user's final numeric answer replaced with `[ANS]` |
| **B** | `contrast_v1_ablB.jsonl` | sentences containing metacognitive/verification cues removed |
| **AB** | `contrast_v1_ablAB.jsonl` | both |
| **CTRL** | `contrast_v1_ablCTRL.jsonl` | random spans deleted, length-matched to B |

### CTRL is not optional

If accuracy drops under A or B, we need to know whether that is because the *removed
content mattered* or merely because text was removed. CTRL deletes a matched number of
characters at random sentence boundaries, per row, matched to what B removed from that same
row. Without it, every drop is uninterpretable.

### Hard requirement: row alignment

Every variant must contain **the same `id` values, in the same order**, as the demonstrated
subset of the original. The GPU agent will build one split and reuse it across all five
files. If ids drift, every comparison is void.

Assert this before writing:

```python
assert [r["id"] for r in variant] == [r["id"] for r in orig_demonstrated]
```

Preserve `paired_id`, `eedi_question_id`, `eedi_misconception`, `knowledge_state`,
`register`, `concept` untouched. Only `turns` content changes.

---

## 4. Ablation A — remove the answer

**Target:** the numeric answer the user arrived at. In `gap` rows this is the Eedi
distractor; in `knows` rows it is the correct answer. Replace **both** the correct-answer
string and the distractor string wherever either appears, so answer identity is neutralised
in both classes.

Look them up via `eedi_question_id` against the Eedi source.

Matching must handle surface variation: `2/7`, `2 / 7`, `2⁄7`, `x = 5`, `x=5`, and written
forms where they occur. Build candidate patterns per answer, apply case-insensitively,
replace with `[ANS]`.

**Report the match rate.** If under 80% of demonstrated rows had at least one answer string
found and replaced, **stop and report** rather than delivering a variant that mostly did
nothing.

Known and acceptable limitation: intermediate working may coincidentally contain the same
number and will also be replaced. Document it; do not attempt to disambiguate.

---

## 5. Ablation B — remove the metacognitive style

**Granularity: whole sentences.** Split each user turn into sentences; drop any sentence
containing a cue term; rejoin.

Seed lexicon, derived from the fitted TF-IDF coefficients on this dataset. Extend it if you
find near-synonyms in the data, and **list every term you added** in the stats file:

```
knows-side : verification, verify, verified, verifying, discriminant, factoring,
             factorise, factorize, factored, yields, substitute, substituting,
             substituted, plug back, plugging back, check, checked, checking,
             confirm, confirms, original equation, sanity
gap-side   : circled, circle, option, options, picked, chose, "my answer",
             "I wrote", "the answer is", "so the answer"
```

**Report characters removed, broken down by class.** If B removes substantially more from
`knows` rows than from `gap` rows, the ablation has introduced a length confound of its own.
Say so explicitly with the numbers; the GPU agent needs this to interpret the result.

If a row loses **all** of its content, keep it with the surviving non-cue text; if that is
empty, keep the row with a single placeholder token and flag it. Do not drop rows — the ids
must align.

---

## 6. Ablation CTRL

For each row, count characters removed by B on that row. Remove approximately the same
number from the **original** text by deleting whole sentences chosen at random with a fixed
seed, until the removed-character count is within ±15% of B's. Never delete a sentence
selected by B's cue rule — CTRL must remove *different* text of *similar* length.

---

## 7. TF-IDF baselines — one per variant

For each of the five variants, fit `TfidfVectorizer(max_features=5000, ngram_range=(1,2))`
plus `LogisticRegression(max_iter=3000, class_weight="balanced")` on the raw user text.

- Predict `knowledge_state` (`gap` vs `knows`), binary.
- **Splits grouped by `eedi_question_id`.** Not `paired_id`. This matters: ungrouped gives
  0.883, question-grouped gives 0.743, and the question-grouped number is the honest one.
- Fixed seed 0, same split across all five variants.
- Report **balanced accuracy**, plus the majority baseline and `n_train` / `n_test`.
- For each variant, print the top 15 coefficients per class.

Expected reference point: `orig` should reproduce ≈0.743. **If it does not, stop** — your
split or preprocessing differs from the earlier run and nothing downstream will be
comparable.

---

## 8. Deliverables

- `data/contrast/contrast_v1_abl{A,B,AB,CTRL}.jsonl`
- `data/contrast/ablation_stats.json` containing, per variant: row count, answer-match rate
  (A), characters removed by class (B, CTRL), any lexicon terms you added, TF-IDF balanced
  accuracy, majority baseline, n_train, n_test, top coefficients
- A printed table of the five TF-IDF numbers side by side
- **6 rows printed in full, before and after**, per variant — at minimum 2 gap and 2 knows
- One entry appended to `results/runs.jsonl`

---

## 9. Rules

1. **Never modify `contrast_v1.jsonl`.** Read-only.
2. **Never report a number you did not compute.** No placeholders.
3. Assert id alignment across all variants before writing anything.
4. Fix and record the seed everywhere.
5. If the `orig` TF-IDF number does not reproduce ≈0.743, stop and report.
6. If the answer-match rate for A is under 80%, stop and report.
7. Set `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1` before importing
   numpy/sklearn — this box has 128 cores and OpenBLAS oversubscription made an earlier
   probe run ~11× slower.
8. Ask when a spec is ambiguous. Do not pick an interpretation silently.

---

## 10. Definition of done

- [ ] Five variants exist with identical, aligned `id` lists
- [ ] `orig` TF-IDF reproduces ≈0.743 balanced accuracy, question-grouped
- [ ] A's answer-match rate ≥80%, reported
- [ ] B's per-class character removal reported, with any imbalance stated plainly
- [ ] CTRL's removal is length-matched to B within ±15% and touches different sentences
- [ ] Five-row TF-IDF comparison table printed
- [ ] 6 before/after samples per variant printed for human review
