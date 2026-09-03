# Handover — GPU Agent: Probe Refits on Ablated Variants

**Standalone. You need this file, the five variant JSONL files from the CPU agent,
`src/model.py`, and `src/probes.py`. Do not start until `ablation_stats.json` exists.**

---

## 1. Why this exists

A probe reads whether a user understands an algebra concept from *demonstrated* work —
**0.899** (natural read position) / **0.888** (elicited), splits grouped by
`eedi_question_id`, against a TF-IDF baseline of **0.743**.

That is compatible with two stories, and we do not know which is true:

- **Correctness** — the model evaluates the arithmetic and represents the user's competence.
- **Style** — the model reads manner of writing. Mastery samples narrate verification steps
  (`discriminant`, `factoring`, `substitute back`); gap samples pick an option and report it
  (`circled`, `option`, `my answer`).

The CPU agent has built text variants that remove one route or the other. You measure what
survives. **You are running a measurement, not trying to preserve a result.** A large drop
is as informative as no drop.

---

## 2. What you are running

Five variants, each already prepared as a JSONL with **identical, aligned `id` lists**:

| Variant | What was removed |
|---|---|
| `orig` | nothing (reference) |
| `A` | the user's final numeric answer → `[ANS]` |
| `B` | sentences containing metacognitive / verification cues |
| `AB` | both |
| `CTRL` | random sentences, length-matched to B |

`CTRL` is the control for "did accuracy drop only because text got shorter." Any drop in A
or B must be read against CTRL's drop, not against zero.

### Interpretation table — fill this in, do not prejudge it

| A drops | B drops | Reading (relative to CTRL) |
|---|---|---|
| yes | no | the probe keys on the answer — closer to correctness than to style |
| no | yes | the probe keys on metacognitive register — the deflationary result |
| yes | yes | both surface routes carry it; little left |
| **no** | **no** | something distributed that neither ablation removes — the interesting case |

---

## 3. Scope

Demonstrated rows only, ~816 (≈318 gap, ≈498 knows). This is small — extraction should take
minutes, not the ~19 min the full Act 1 probe run took.

Both read positions:
- `natural` — last token of the rendered chat, `add_generation_prompt=True`
- `elicited` — plus `"I think this user's understanding of {concept} is"`

Elicit prefix must be **byte-identical** to Act 1's or the numbers are not comparable.

---

## 4. Procedure

### 4.1 Extraction

Reuse `src/model.py` unchanged. `Qwen/Qwen3-8B`, bfloat16, 36 layers, d=4096, 37 read
points.

Before extracting anything:

- `M.assert_template_sane(tok)` — fails loudly if `<think>` appears or the elicitation
  prefix is not final
- `M.verify_last_token_indexing(...)` — batched vs unbatched must agree; catches padding-side
  bugs that silently corrupt every value
- confirm `len(hidden_states) == 37`

Cache as `cache/abl_{variant}_{position}.npy`, shape `(n, 37, 4096)`, float32.

**Assert alignment before probing:**

```python
for v in VARIANTS:
    assert ids[v] == ids["orig"], f"{v} id order differs from orig"
```

### 4.2 Probes

One condition only: **within-demonstrated**, binary `gap` vs `knows`.

- Splits grouped by **`eedi_question_id`**, not `paired_id`. Question-grouped is the honest
  number; ungrouped inflates it to ~0.94–0.96 through item memorisation.
- **The same split object across all five variants.** Build it once from `orig` and reuse.
  Row order is aligned, so indices transfer directly.
- `StandardScaler` + `LogisticRegression`, `C=1.0`, `max_iter=3000`, `class_weight="balanced"`,
  seed 0. Same as Act 1 so numbers stay comparable.
- All 37 layers, both read positions.
- Report **balanced accuracy** and majority baseline, plus `n_train` / `n_test`.
- Run `probes.control_task` with `content_keys` set to normalised user text, per variant.
  A control above the leakage threshold invalidates that variant.

### 4.3 Reference check

`orig` must reproduce **≈0.899 natural / ≈0.888 elicited**. If it does not, your split,
prefix, or preprocessing differs from Act 1. **Stop and report** — every comparison depends
on this anchor.

---

## 5. Deliverables

- `cache/abl_{variant}_{natural,elicited}.npy` (10 files)
- `results/ablation_probe_results.json` — full per-layer curves, best layer, balanced
  accuracy, control-task max, n_train/n_test, per variant per position
- `results/figs/act1_ablations.png` — accuracy vs layer, five variants on shared axes, one
  panel per read position, with each variant's TF-IDF baseline from `ablation_stats.json`
  drawn as a horizontal line
- A summary table:

| variant | natural | elicited | TF-IDF | Δ vs orig | Δ vs CTRL |
|---|---|---|---|---|---|

- One entry per variant appended to `results/runs.jsonl`

---

## 6. Rules

1. **Never report a number you did not compute.** If a run fails, say it failed.
2. **Do not tune.** Same `C`, `max_iter`, solver, seed, and layer range as Act 1. If you
   sweep anything, sweep it identically for all five variants and say so.
3. **Set thread limits.** `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1`
   before importing numpy/sklearn, or use `threadpoolctl.threadpool_limits(limits=1)`.
   This box has 128 cores; OpenBLAS spawned 64-thread pools on 1354×4096 fits and made each
   one ~11× slower (25.0 s vs 2.1 s at identical iteration count). Parallelise across layers
   with `joblib`, one BLAS thread per process.
4. **Print shapes and label counts** before every probe fit.
5. **Flag surprises, do not smooth them.** If a variant scores *higher* than `orig`, report
   it; do not assume a bug.
6. Do not modify any JSONL. Read-only.
7. Ask when a spec is ambiguous.

---

## 7. Definition of done

- [ ] `orig` reproduces ≈0.899 / ≈0.888, question-grouped
- [ ] Identical split reused across all five variants, asserted
- [ ] Control tasks clean for all five, reported
- [ ] Summary table with Δ vs `orig` **and** Δ vs `CTRL`
- [ ] Figure written
- [ ] A one-paragraph verdict in `notes.md` naming which row of the §2 interpretation table
      the data lands on — with the numbers, and without softening the result
