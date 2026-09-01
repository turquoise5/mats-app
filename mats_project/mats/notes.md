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
