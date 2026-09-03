# Act 1 Task 1.2 — Probing and Transfer Tests: Verdict

**Run date:** 2026-09-01 · **Model:** Qwen/Qwen3-8B (36 layers, d=4096; 37 read points) · **Seed:** 0
**Dataset:** `data/contrast/contrast_v1.jsonl`, 2,112 rows, unmodified.
**Elicit prefix used:** `"I think this user's understanding of {concept} is"`
**Just-ask template:** `"Based on this conversation, does the user understand {C}? Answer yes or no."` (local Qwen3-8B, $0, 0/2112 unparseable)

---

## VERDICT: branch 2 of the §7 decision rule — NARROW

> `cross-register` holds; `stated→demonstrated` collapses.
> The claim narrows to **"the model tracks what users *say* they know."** Act 2 still runs.

### The two headline numbers

| headline | natural | elicited | bar | TF-IDF | majority |
|---|---|---|---|---|---|
| cross-register (expert→novice) | 0.764 | **0.814** | ≥0.75 | 0.753 | 0.551 |
| stated→demonstrated | 0.615 | **0.699** | ≥0.75 | 0.462 | 0.610 |

`elicited` beats `natural` on both headlines and is the better read position.

**Do not round the second number up.** 0.699 is below the 0.75 bar. It is genuinely above
both TF-IDF (+23.7 pts) and its own majority baseline (+8.9 pts), so it is *not* nothing —
but it does not clear the threshold the design committed to in advance.

---

## Full condition table

Accuracy = best layer; `ctrl` = control-task max; all controls **clean** (below leakage threshold).

### natural
| condition | n_train | n_test | best_acc | @L | majority | TF-IDF | just-ask | ctrl_max |
|---|---|---|---|---|---|---|---|---|
| pooled | 1354 | 343 | 0.977 | 20 | 0.548 | 0.965 | 0.813 | 0.546 |
| within-expert | 673 | 169 | 1.000 | 19 | 0.556 | 0.905 | 0.840 | 0.562 |
| within-novice | 684 | 171 | 0.982 | 33 | 0.550 | 0.959 | 0.848 | 0.596 |
| within-stated | 707 | 174 | 1.000 | 10 | 0.500 | 0.994 | 0.943 | 0.601 |
| **cross-register** | 842 | 855 | **0.764** | 24 | 0.551 | 0.753 | 0.853 | — |
| cross-register-rev | 855 | 842 | 0.968 | 22 | 0.558 | 0.765 | 0.866 | — |
| **stated→demonstrated** | 881 | 816 | **0.615** | 9 | 0.610 | 0.462 | 0.784 | — |
| demonstrated→stated | 816 | 881 | 0.607 | 25 | 0.503 | 0.504 | 0.928 | — |
| cross-both | 419 | 393 | 0.631 | 25 | 0.598 | 0.397 | 0.748 | — |
| within-demonstrated *(diagnostic)* | 650 | 166 | 0.940 | 8 | 0.608 | 0.916 | 0.765 | 0.528 |
| within-demonstrated-by-eedi *(diagnostic)* | 637 | 179 | 0.899 | 19 | 0.587 | 0.743 | 0.749 | 0.582 |

### elicited
| condition | n_train | n_test | best_acc | @L | majority | TF-IDF | just-ask | ctrl_max |
|---|---|---|---|---|---|---|---|---|
| pooled | 1354 | 343 | 0.985 | 13 | 0.548 | 0.965 | 0.813 | 0.534 |
| within-expert | 673 | 169 | 0.988 | 21 | 0.556 | 0.905 | 0.840 | 0.556 |
| within-novice | 684 | 171 | 0.982 | 22 | 0.550 | 0.959 | 0.848 | 0.561 |
| within-stated | 707 | 174 | 1.000 | 11 | 0.500 | 0.994 | 0.943 | 0.612 |
| **cross-register** | 842 | 855 | **0.814** | 26 | 0.551 | 0.753 | 0.853 | — |
| cross-register-rev | 855 | 842 | 0.957 | 16 | 0.558 | 0.765 | 0.866 | — |
| **stated→demonstrated** | 881 | 816 | **0.699** | 36 | 0.610 | 0.462 | 0.784 | — |
| demonstrated→stated | 816 | 881 | 0.543 | 2 | 0.503 | 0.504 | 0.928 | — |
| cross-both | 419 | 393 | 0.728 | 36 | 0.598 | 0.397 | 0.748 | — |
| within-demonstrated *(diagnostic)* | 650 | 166 | 0.964 | 23 | 0.608 | 0.916 | 0.765 | 0.546 |
| within-demonstrated-by-eedi *(diagnostic)* | 637 | 179 | 0.888 | 22 | 0.587 | 0.743 | 0.749 | 0.577 |

---

## Five things that qualify the verdict

### 1. Just-ask beats the probe on most transfer conditions — the reverse of TalkTuner

§6 predicted, from TalkTuner, that prompting would *badly underperform* probing. It does not.

| condition | probe (best pos.) | just-ask | winner |
|---|---|---|---|
| cross-register | 0.814 | **0.853** | just-ask |
| stated→demonstrated | 0.699 | **0.784** | just-ask |
| demonstrated→stated | 0.607 | **0.928** | just-ask |
| cross-both | 0.728 | **0.748** | just-ask |
| cross-register-rev | **0.968** | 0.866 | probe |

**This is the most consequential result in the run, and it cuts against the framing.**
Just-ask scores **0.784 on stated→demonstrated — clearing the 0.75 bar the probe failed.**
So the information about demonstrated knowledge state *is* accessible to the model; the
linear probe at these read positions simply does not capture it.

The correct conclusion is therefore **not** "the model does not track demonstrated
knowledge." It is: *the linear probe reads self-reports; prompting reaches more than the
probe does.* Any claim that probing reveals a user model the model cannot itself report is
not supported by this data.

### 2. The condition that passed shares content across the split; the one that failed does not

| condition | `paired_id_overlap` | `content_key_overlap` |
|---|---|---|
| cross-register | **792** | 0 |
| stated→demonstrated | 0 | 0 |
| demonstrated→stated | 0 | 0 |
| cross-both | 0 | 0 |

This is inherent to the cross-register design — holding content fixed and varying register
is the whole point — but it means 0.814 measures **register-invariance on content seen in
training under the other rendering**, not generalization to unseen content.
`stated→demonstrated` gets no such help. The two headline numbers are not like-for-like,
and the gap between them is partly explained by this rather than by disclosure mode alone.

### 3. cross-register is strongly asymmetric

expert→novice 0.814, but novice→expert **0.957–0.968**. Training on novice text generalizes
to expert far better than the reverse. The headline uses the harder (and pre-registered)
direction, which is correct, but the asymmetry is large and unexplained.

### 4. The natural-position cross-register pass is thin

0.764 clears 0.75, but TF-IDF gets 0.753 on the same split — a **+1.1 pt** margin. §7 requires
"clearly above TF-IDF." At `natural` that condition is **not** met; the pass rests on
`elicited` (0.814, +6.1 pts). Reported as an elicited-position result.

### 5. Clean checks

- **Control tasks: all 12 clean.** Closest margin is `elicited/within-stated`, 0.612 vs a
  0.614 threshold — passing by 0.002. Treat that single cell's headroom as nil.
- **Undisclosed-cell prior (415 rows):** P(predict "knows") = 0.407 natural, 0.335 elicited.
  The §6 failure mode (a probe calling "knows" at ~90% on inputs revealing nothing) is
  **absent**. Just-ask yes-rate on the same rows is 0.545.
- **within-demonstrated** (0.940/0.964 vs TF-IDF 0.916) is reported as a diagnostic, not a
  gate, per §4. Grouping by `eedi_question_id` instead of `paired_id` moves it to
  0.899/0.888 — consistent with the previously diagnosed +24.6 → +23.5 stability.

---

## Thin-cell caveat (§7)

`demonstrated/gap` remains the load-bearing cell for Headline 2 and is thin against its
60-per-concept-per-register target:

| cell | expert | novice |
|---|---|---|
| demonstrated/gap | 160 | 158 |
| demonstrated/knows | 263 | 235 |

Per-condition `n_train`/`n_test` are in the tables above and in `results/runs.jsonl`.
Pairing is incomplete overall: 1,145 unique `paired_id` over 2,112 rows — 967 complete
pairs, 178 orphans. Orphans landing in each transfer condition: cross-register 50 train /
63 test; stated→demonstrated 45 / 68; demonstrated→stated 68 / 45; cross-both 1 / 19.

---

## Recommendation

Proceed to Act 2 under the **narrowed** claim ("the model tracks what users say they know"),
and treat finding #1 as a first-order problem for the project's framing: **the just-ask
baseline outperforms the probe on the transfer tests**, including on the very condition the
probe failed. Act 2's causal work should be designed to distinguish the probe direction from
what prompting already recovers, or the causal result will inherit this ambiguity.

Nothing in the dataset or any verification threshold was modified to produce these numbers.

---

## Artifacts

- `results/figs/act1_transfer.png` — accuracy vs layer, 9 conditions, all baselines on shared axes
- `results/act1_probe_results.json` — full per-layer curves and metrics
- `results/act1_acc_{condition}_{position}.npy`, `results/act1_ctrl_{condition}_{position}.npy` (22 conditions)
- `cache/act1_all_{natural,elicited}.npy` — (2112, 37, 4096) activations; `cache/act1_justask.json`
- `results/runs.jsonl` — every condition logged with `n_train`/`n_test`
- Logs: `logs/act1_{extract,justask,probe,plot}.log`

### Run note — probe performance

The first `probe` launch was aborted after 33 min without completing a single condition:
BLAS thread oversubscription (128 cores, 64-thread OpenBLAS pools on 1354×4096 fits) made
each fit ~11x slower (25.0s vs 2.1s at identical `n_iter`). Re-run with
`OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1`; full run then took ~19 min.
**No code, hyperparameters, solver, or seed were changed** — `C=1.0`, `max_iter=3000` as in
Act 0, so results stay comparable. Aborted log kept at `logs/act1_probe_ABORTED_threadthrash.log`.

---

## Ablation probe refits — GPU Agent (`handover_gpu_ablation_probes.md`)

**Run date:** 2026-09-03 · same model/seed as above. **Scope:** demonstrated subset only
(816 rows: 318 gap / 498 knows), `within-demonstrated` condition, grouped by
`eedi_question_id`, `test_size=0.2` (`n_train=637`, `n_test=179`), split built once from
`orig` and reused unchanged for A/B/AB/CTRL (row order aligned, asserted before every run).
Pipeline: `StandardScaler` + `LogisticRegression(C=1.0, max_iter=3000,
class_weight="balanced", random_state=0)`, scored by balanced accuracy, all 37 read
points, both `natural` and `elicited`. This deliberately differs from Act 1's own
`within-demonstrated-by-eedi` pipeline (no `class_weight`, plain accuracy) — that pipeline
is reserved for the reference check below, where matching Act 1 exactly is the point.

**Reference check (handover §4.3): PASSED exactly.** Reusing Act 1's own pipeline
(`probes.fit_layer_probes_explicit`, unbalanced, plain accuracy) on `orig` against this
split reproduces **0.899 @L19 natural / 0.888 @L22 elicited** — the identical numbers
reported for `within-demonstrated-by-eedi` in the verdict above, to three decimals. Split,
prefix, and preprocessing all match Act 1.

**Control tasks: all 10 clean** (5 variants × 2 positions), well under the leakage
threshold (0.697 at this `n_test`/layer count) in every case — max observed control was
0.620 (CTRL, elicited).

### Results (balanced accuracy, best layer; TF-IDF is the handover-specified pipeline —
`TfidfVectorizer(1,2)` + `LogReg(balanced)`, `GroupShuffleSplit` by `eedi_question_id` —
from `data/contrast/ablation_stats.json`, computed by the CPU agent on a different split
mechanism than the probe's, so read it as an independent baseline, not a like-for-like
delta)

| variant | natural | elicited | TF-IDF (qid) | Δ nat vs orig | Δ eli vs orig | Δ nat vs CTRL | Δ eli vs CTRL |
|---|---|---|---|---|---|---|---|
| orig | 0.910 | 0.897 | 0.885 | — | — | +0.062 | +0.035 |
| A | 0.904 | 0.871 | 0.823 | −0.007 | −0.026 | +0.055 | +0.010 |
| B | 0.852 | 0.847 | 0.833 | −0.058 | −0.050 | +0.004 | −0.015 |
| AB | 0.866 | 0.835 | 0.784 | −0.045 | −0.062 | +0.018 | −0.026 |
| CTRL | 0.848 | 0.861 | 0.834 | −0.062 | −0.035 | +0.000 | +0.000 |

### Reading the table against §2

Against raw `orig`, every variant drops (5–6 pts). But **CTRL — random text deletion,
length-matched to B, containing no targeted content — drops almost as much as B does**
(−0.062/−0.035 vs B's −0.058/−0.050). That makes the `Δ vs CTRL` columns the ones that
answer the actual question, and against CTRL:

- **A**: **no drop** — +0.055 natural, +0.010 elicited (A tracks orig almost exactly; see
  `results/figs/act1_ablations.png`, blue curve sits on the black curve at nearly every
  layer).
- **B**: **no drop at natural** (+0.004, a wash), a **small drop at elicited** (−0.015).
- **AB**: small positive at natural (+0.018), small negative at elicited (−0.026) —
  consistent with "B accounts for what little AB shows; A adds nothing on top."

By the §2 table this lands closest to the **no/no row**: *"something distributed that
neither ablation removes — the interesting case."* Neither answer-masking nor
metacognitive-cue removal costs the probe anything beyond what an equal-sized random
deletion already costs it. Whatever the probe reads from demonstrated text is not
concentrated in the final numeric answer, nor in the small set of verification/hedging
sentences TF-IDF flagged — it is spread across the rest of the text (working steps,
phrasing throughout, structure), or the probe is substantially reading generic
text-presence/length rather than either targeted signal.

**This does not fully resolve correctness vs. style** — it rules out the two most
legible single-cause stories (a literal answer token, an explicit metacognitive register
marker) without saying what the surviving signal actually is.

### Caveats that qualify this reading — report plainly, do not round off

1. **Ablation A's own gate failed.** `ablation_stats.json` reports
   `answer_match_rate_A = 0.567` against an 80% pass bar the CPU handover set in advance —
   under 57% of demonstrated rows actually had an answer string found and replaced by
   `[ANS]`. A's near-zero probe drop is therefore **not strong evidence the probe ignores
   the answer** — it is equally consistent with the ablation mostly failing to remove it.
   TF-IDF's ~6pt drop under A (0.885→0.823, similar in size to B's and CTRL's) is a
   soft second data point that some real content did leave the text under A, but the
   match-rate failure means this ablation cannot carry a "no" verdict on its own.
2. **CTRL's per-row length target (±15% of B) was hit for only 64.5%** of rows
   (`ctrl_rows_within_15pct` in `ablation_stats.json`) — CTRL is length-matched on
   aggregate, not row-by-row exactly, so the `Δ vs CTRL` comparison is a reasonable but
   imperfect length control.
3. B removes a larger share of `gap` text than `knows` text (38.4% vs 35.3%, per
   `ablation_stats.json`'s `b_pct_removed_by_class`) — a ~3pp gap-vs-knows imbalance, a
   small residual length confound in B's own right, on top of what CTRL already controls
   for.
4. The TF-IDF column in the table above comes from a **different split mechanism**
   (`GroupShuffleSplit`, `test_size=0.3`) than the probe's (`sklearn`'s grouped
   stratified split at `test_size=0.2`) — both grouped by `eedi_question_id`, but not the
   same train/test partition. Read TF-IDF as an independent sanity baseline, not a
   row-for-row comparison point.

### Verdict

**The data lands on the §2 "no / no" row.** Against the length-matched CTRL control,
neither removing the user's final answer (A) nor removing metacognitive/verification
sentences (B) costs the `within-demonstrated` probe anything beyond what an equal-sized
random deletion already costs (Δ vs CTRL: A +0.055/+0.010, B +0.004/−0.015, both ≈0 or
positive). This weighs against either single-cause story from §1 — the probe's signal is
not sitting in the literal answer token or in the explicit verification-language TF-IDF
flagged — but Ablation A's 56.7% match-rate failure means the answer-removal result is
weak evidence, not a clean negative, and should be re-run with a better answer-matcher
before this is treated as settled. Nothing here overturns Act 1's narrowed headline
(cross-register holds, stated→demonstrated collapses); it only says that whatever the
`within-demonstrated` probe is reading is not the two most obvious surface routes.

### Artifacts

- `cache/abl_{orig,A,B,AB,CTRL}_{natural,elicited}.npy` — (816, 37, 4096) float32, 10 files
- `results/ablation_probe_results.json` — full per-layer curves, reference check, split,
  summary table
- `results/abl_acc_{variant}_{position}.npy`, `results/abl_ctrl_{variant}_{position}.npy`
  — 10 + 10 files
- `results/figs/act1_ablations.png`
- 5 entries in `results/runs.jsonl` (`ablation_probe/{orig,A,B,AB,CTRL}`), plus 10
  `ablation_extract` entries
- Logs: `logs/ablation_extract.log`, `logs/ablation_probe.log`, `logs/ablation_plot.log`

### orig only, merged-group split — probe vs. TF-IDF, like-for-like

The `Δ vs CTRL` table above borrowed its TF-IDF numbers from a *different* split
(`GroupShuffleSplit`, `eedi_question_id`-grouped) than the probe's own (`sklearn`'s
grouped stratified split). This run fixes that: `orig` only, both positions, split on
the CPU agent's **merged** (content-corrected) groups from `ablation_groups.json`, using
the exact `GroupShuffleSplit(n_splits=1, test_size=0.3, seed=0)` that produced the CPU
agent's own merged-grouping number — reproduces `n_train=521`/`n_test=295` exactly. TF-IDF
(`TfidfVectorizer(max_features=5000, ngram_range=(1,2))` + `LogisticRegression(
class_weight="balanced")`) is **refit on the identical 295 test rows** rather than reused
from a different split — it lands at `balanced_acc=0.799916`, matching the CPU agent's
merged-grouping figure (0.799916148761961) to the last digit, which cross-checks that the
groups file, row order, and split mechanics are all identical to the CPU run.

Probe: `StandardScaler` + `LogisticRegression(class_weight="balanced")`, all 37 layers,
best layer reported. Paired bootstrap (10,000 resamples of the fixed 295 test rows, seed
0) on `balanced_acc(probe) − balanced_acc(TF-IDF)`:

| position | probe (best layer) | TF-IDF | diff | 95% CI | p(diff ≤ 0) |
|---|---|---|---|---|---|
| natural | 0.930 @L20 | 0.800 | **+0.130** | [+0.076, +0.185] | 0.0000 |
| elicited | 0.911 @L23 | 0.800 | **+0.112** | [+0.054, +0.169] | 0.0000 |

On this harder, content-corrected split — with TF-IDF given a strong bigram config and
evaluated on the exact same held-out rows as the probe — **the probe beats TF-IDF by
11–13 points, and the bootstrap CI excludes zero by a wide margin** (0/10,000 resamples
favoured TF-IDF or tied). This is a different question from the ablation-drop analysis
above (which asked *what the probe's signal is made of*): this one asks *whether the
probe carries more than bag-of-words does at all*, and on this split the answer is
unambiguously yes.

Artifacts: `results/orig_merged_probe_vs_tfidf.json` (full per-layer curves, split,
TF-IDF config, bootstrap detail); 2 entries in `runs.jsonl`
(`orig_merged_probe_vs_tfidf/{natural,elicited}`); `logs/orig_merged.log`.

### B and CTRL refit on merged groups — McNemar, not point estimates (correction)

**The "Δ vs CTRL" table further up this section was measured on a leaky split.** It used
`within-demonstrated` grouped by raw `eedi_question_id`, which `src/grouping.py` documents
as letting content-colliding questions (e.g. 1158/552, byte-identical stems) straddle
train/test. This section refits B and CTRL (plus `orig`, for reference) on the same
merged-group split as `orig_merged` above (`n_train=521`, `n_test=295`, identical items),
and replaces the earlier Δ-of-point-estimates comparison with **McNemar's exact test on
the shared, paired test items** — the right tool for "do these two classifiers disagree
systematically on the same rows," which a bare accuracy difference cannot answer (two
models can have identical accuracy while disagreeing on every single item, or identical
accuracy while agreeing on all but a handful — a Δ of zero is compatible with either).

**Balanced accuracy, best layer, merged split** (majority baseline on this test set: 0.631):

| variant | natural | elicited |
|---|---|---|
| orig | 0.930 @L20 | 0.911 @L23 |
| B | 0.809 @L3 | 0.804 @L4 |
| CTRL | 0.875 @L21 | 0.841 @L29 |

B's best layer is early (L3/L4) both positions, not a single-point noise spike -- its
full 37-layer curve is genuinely U-shaped (checked directly: 0.80 at L2-4, dips to ~0.70
mid-stack, climbs back to ~0.80 by L20-27, natural position). Worth remembering when
reading "best layer" here as anything other than an argmax over 37 correlated,
noisy-at-n=295 estimates — that caveat applies to `orig` and `CTRL` too, not just `B`.

**McNemar (exact, paired, 295 shared items) — contingency `both_right / both_wrong /
a_only_right / b_only_right`:**

| pair | position | both right | both wrong | a-only | b-only | exact p | chi2 p |
|---|---|---|---|---|---|---|---|
| B vs CTRL | natural | 216 | 13 | 25 (B) | 41 (CTRL) | **0.064** | 0.065 |
| B vs CTRL | elicited | 206 | 17 | 35 (B) | 37 (CTRL) | **0.906** | 0.906 |
| B vs orig | natural | 228 | 11 | 13 (B) | 43 (orig) | 0.0001 | 0.0001 |
| B vs orig | elicited | 225 | 12 | 16 (B) | 42 (orig) | 0.0009 | 0.0010 |
| CTRL vs orig | natural | 253 | 20 | 4 (CTRL) | 18 (orig) | 0.0043 | 0.0056 |
| CTRL vs orig | elicited | 238 | 23 | 5 (CTRL) | 29 (orig) | 0.0000 | 0.0001 |

### What changes vs. the leaky-split verdict

**Both B and CTRL lose to `orig` at both positions, decisively** (all four `*_vs_orig`
rows p ≤ 0.005) — any text removal, targeted or random, costs the probe real,
McNemar-significant information. That part of the earlier picture holds up.

**The `B vs CTRL` comparison — the one that actually tests whether the targeted
metacognitive-cue removal costs more than an equivalent random deletion — is genuinely
split by position, and differs from the earlier point-estimate read:**

- **elicited: still no difference** (p = 0.906, 35 vs 37 discordant pairs — as
  symmetric as a paired comparison gets). This matches the earlier `Δ vs CTRL ≈ 0`
  finding and survives the corrected split cleanly.
- **natural: trends toward B being worse than CTRL, but does not reach significance**
  (p = 0.064, 25 vs 41 discordant pairs — CTRL wins the disagreement roughly 1.6:1). The
  earlier leaky-split table reported `Δ vs CTRL = +0.004` at natural — effectively zero.
  The corrected split does **not** reproduce that null; it shows a real-looking trend in
  the opposite direction (B costing *more* than CTRL) that a properly-powered version of
  this test might well confirm. **Do not report natural-position "no drop beyond CTRL"
  as established — it is not, on the non-leaky split.** At n=295 with 66 discordant
  pairs, this test is underpowered to fully resolve a moderate effect; call it
  unresolved, not null.

**Net effect on the earlier verdict:** the elicited-position "no/no" reading (§2 table)
stands, refit and correctly tested. The natural-position "no" leg of that reading was an
artifact of the leaky split and should be retracted — natural-position B vs CTRL is an
open question, trending toward "B does cost something beyond length," not resolved either
way at this sample size.

Artifacts: `results/ablation_merged_mcnemar.json` (balanced accuracy, best layer, full
contingency tables and both McNemar p-values, per pair per position); 2 entries in
`runs.jsonl` (`ablation_merged_mcnemar/{natural,elicited}`); `logs/ablation_merged.log`.

### Persistence: does the direction survive neutral filler turns, or evaporate?

A different axis from everything above: not *what the probe's signal is made of*, but
*whether it is a maintained representation of the user or a transient read of the last
thing said*. Three neutral, content-free assistant/user pairs (no mention of correctness,
the concept, or the user's work — e.g. `"Got it — give me a moment to look at that." /
"Sure, no rush."`) are appended after each demonstrated row's real turns, pushing the
user's actual work (right or wrong) 1 turn back (`D1`) or 3 turns back (`D3`) from the
read position. `D0` = `orig`, unmodified. Same 816 rows, same ids, same merged split
(`n_test=295`) throughout — only `turns` grows.

**Transfer (strict):** freeze the probe fit on `D0` at `D0`'s own merged-split best layer
(derived, not hard-coded: L20 natural, L23 elicited — matches the `orig_merged` numbers
above), apply it *unchanged* to `D1`/`D3` at that same layer. Tests whether it's the same
direction, still readable later.

**Refit (permissive):** fit a fresh probe on `D1`/`D3` (same split, full 37-layer sweep).
Tests whether the information is present *at all*, in *any* linear form, even at a
different layer.

| | D0 (frozen L) | D1 | D3 | McNemar D0 vs D1 | McNemar D0 vs D3 |
|---|---|---|---|---|---|
| **natural, transfer** | 0.930 | 0.893 | 0.862 | 18/8 disc., p=0.076 | 29/8 disc., **p=0.0008** |
| **elicited, transfer** | 0.911 | 0.864 | 0.867 | 15/8 disc., p=0.210 | 15/9 disc., p=0.308 |
| **natural, refit** | 0.930 @L20 | 0.930 @L21 | 0.927 @L22 | — | — |
| **elicited, refit** | 0.911 @L23 | 0.908 @L31 | 0.899 @L18 | — | — |

(majority baseline on this test set: 0.631; McNemar counts are `D0-only-right /
{D1,D3}-only-right` discordant pairs, exact test, out of 295 paired items)

**Neither of the two named outcomes is what happened. It's a third thing.** Refit is
essentially flat — 0.3 to 3 points off `D0`, nowhere near the 0.631 floor, at both
positions through `D3`. The information about the user's demonstrated competence is not
lost by three neutral turns later; a fresh linear probe recovers almost all of it. That
rules out "evaporates" in the strong sense the §2-style table implies.

But the frozen direction is not fully stable either. At **natural**, transfer degrades
monotonically and the `D0` vs `D3` drop is McNemar-significant (p=0.0008, 29 vs 8
discordant pairs) — by three neutral turns, the *specific direction* found at the base
conversation is measurably worse at reading out the label, even though a same-layer-
adjacent direction (refit drifts L20→L21→L22, one layer per added pair) recovers the
signal almost completely. At **elicited**, the same drop is present in magnitude
(0.911→0.867) but is *not* significant (p=0.21, p=0.31 at n=295) — could be noise at
this sample size, could be a smaller true effect; not resolved either way.

**Reading:** the balance of evidence favors "maintained user model" over "local
correctness judgment," but not in the clean, single-frozen-direction form the question
posed. What appears to persist is the *information*, recoverable by a fresh probe at
nearly full strength three turns later; what does *not* fully persist, at least at the
natural read position, is the *exact geometric encoding* the base-conversation probe
found — it drifts by about one layer per added neutral pair, and that drift costs the
frozen probe real, statistically detectable accuracy. A purely transient, re-derived-
each-token judgment would predict refit collapsing toward chance by `D3`; it does not.
A perfectly static, maintained-forever direction would predict transfer holding flat;
at natural position it does not, significantly.

Artifacts: `cache/abl_orig_{D1,D3}_{natural,elicited}.npy` (4 files, 816×37×4096 each);
`results/persist_results.json` (full per-layer refit curves, transfer accuracies, McNemar
contingency + both p-values, per position); `results/persist_samples.txt` (rendered
before/after examples for eyeballing where the neutral turns land); 2 entries in
`runs.jsonl` (`persist/{natural,elicited}`), plus 4 `extract_persist` entries; logs:
`logs/extract_persist.log`, `logs/persist.log`.

### Persistence, take two: intervening turns with real content, not empty filler

The neutral filler above ("Got it, give me a moment" / "Sure, no rush") gives the model
nothing to actually process — it tests persistence across *turns*, not across
*competing content*. This repeats the identical design (same rows, same merged split,
same frozen-D0-probe / fresh-refit pair of tests) but swaps in three self-contained,
unrelated table/plot Q&A exchanges as the intervening pairs instead — each one a small
made-up fact requiring a real answer (`"Site A: 14, Site B: 31, Site C: 22 — which site
has the highest reading?" / "Site B, at 31."`), so the model has something concrete to
track in between, not just tokens. `C1`/`C3` = 1/3 such pairs appended; `D1`/`D3` (from
the run above) are the matched-turn-count neutral-filler baseline for direct comparison.

| | D0 (frozen L) | +1 pair | +3 pairs | McNemar D0 vs +3 |
|---|---|---|---|---|
| **natural, transfer, content (C)** | 0.930 | 0.518 | 0.571 | 90/20 disc., **p<0.0001** |
| **natural, transfer, neutral (D)** | 0.930 | 0.893 | 0.862 | 29/8 disc., p=0.0008 |
| **elicited, transfer, content (C)** | 0.911 | 0.614 | 0.690 | 57/15 disc., **p<0.0001** |
| **elicited, transfer, neutral (D)** | 0.911 | 0.864 | 0.867 | 15/9 disc., p=0.308 |
| **natural, refit, content (C)** | 0.930 @L20 | 0.917 @L23 | 0.928 @L23 | — |
| **elicited, refit, content (C)** | 0.911 @L23 | 0.888 @L29 | 0.911 @L24 | — |

**Content vs. neutral, matched turn-count, identical frozen probe, McNemar on the same
295 items:**

| pair | content bal. acc | neutral bal. acc | discordant (content-only / neutral-only) | exact p |
|---|---|---|---|---|
| natural, +1 pair (C1 vs D1) | 0.518 | 0.893 | 26 / 97 | **<0.0001** |
| natural, +3 pairs (C3 vs D3) | 0.571 | 0.862 | 35 / 84 | **<0.0001** |
| elicited, +1 pair (C1 vs D1) | 0.614 | 0.864 | 11 / 61 | **<0.0001** |
| elicited, +3 pairs (C3 vs D3) | 0.690 | 0.867 | 6 / 42 | **<0.0001** |

(majority baseline: 0.631 — natural/C1's 0.518 is *below* majority; every other content
transfer number sits at or just above it)

**This is a much sharper result than the neutral-filler version, and it changes the
picture.** Where neutral filler produced a modest (natural: significant only by +3
turns; elicited: not significant) decline, real intervening content **collapses the
frozen probe almost immediately** — one content-bearing pair costs as much as or more
than three neutral ones, at both read positions, decisively (all four `content vs
neutral` comparisons p < 0.0001, huge and lopsided discordant-pair counts). At natural
position with one content pair, the frozen probe is not just degraded, it is **worse
than the majority baseline** (0.518 < 0.631) — actively anti-informative, not merely
uninformative.

**Refit tells a completely different story: it barely moves.** 0.930→0.917→0.928
(natural), 0.911→0.888→0.911 (elicited) — a fresh probe recovers the label almost
exactly as well after three unrelated table Q&As as it does at `D0`, same as in the
neutral-filler run. **The information about the user's demonstrated competence is not
erased by processing something else — it is not where the frozen direction is looking
for it anymore.**

**Reading:** taken together with the neutral-filler run, this rules out both of the
originally posed outcomes more decisively than before. It is not "evaporates" — refit
recovery stays near-total under real cognitive load, which a genuinely transient,
overwritten-by-the-next-thing signal would not do. But it is *emphatically* not a single
"maintained direction" either — that story predicts transfer holding roughly flat
regardless of what happens in between, and instead one turn of real unrelated content
does more damage than three turns of silence. The honest description: the model
continues to carry *recoverable* information about who it's talking to, but the specific
linear feature exposing it at the read position is **actively reallocated by whatever
the model is currently doing** — it is far more disrupted by being asked to track
something else than by the mere passage of turns. A "maintained user model" in the sense
of a stable, dedicated, persistently-read-out direction is not supported by this data;
a "maintained but currently-not-foregrounded" representation, recoverable on demand but
crowded out of its usual read-out slot by competing processing, is.

Artifacts: `cache/abl_orig_{C1,C3}_{natural,elicited}.npy` (4 files); `results/
persist_content_results.json` (transfer/refit curves, D0-vs-C McNemar, and the C-vs-D
content-vs-neutral McNemar comparison, all per position); `results/
persist_content_samples.txt`; `runs.jsonl` entries `persist_content/{natural,elicited}`
and `persist_content_vs_persist/{natural,elicited}`, plus 4 `extract_persist_content`
entries; logs: `logs/extract_persist_content.log`, `logs/persist_content.log`.
