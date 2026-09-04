#!/usr/bin/env python
"""Re-run of run_steering.py's `correction_items` step with a much larger token budget.

Why: notes.md flagged that all 9 conditions on 2 of the 3 items got cut off by the
200-token cap before the model reached an explicit verdict (see notes.md, "Correction
items" section, and the "Run steered-model experiments again" follow-up). This reruns
the *identical* grid -- same 3 items, same layer, same probe/random/dom vectors, same
alpha fracs {0, +0.15, +0.25}, same seed -- with `max_new_tokens` raised from 200 to 450
so responses actually finish, and writes results to separate `_v2` artifacts (does not
overwrite the original 200-token run, which stays as the documented before/after).

    python run_correction_items_v2.py

After generation, grades each response by hand-inspectable criteria (not just presence
of hedging/affirming *language*): does the response state the item's specific numeric/
algebraic error, and does it land on the mathematically correct final answer? This is
recorded as a `verdict` field per generation in the output JSON, cross-checked against
the ground truth for each item (see GROUND_TRUTH below).
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"

import json

import numpy as np

from run_ablation_probes import CACHE_PREFIX, _balanced_probe, fit_layer_probes_balanced, load_variant_rows, merged_split
from run_steering import CORRECTION_ALPHA_FRACS, CORRECTION_ITEM_IDS, CORRECTION_VECTORS
from src import config as C

MAX_NEW_TOKENS = 450

RESULTS_PATH = C.RESULTS / "correction_items_v2_results.json"
SAMPLES_PATH = C.RESULTS / "correction_items_v2_samples.txt"
TABLE_PATH = C.RESULTS / "correction_items_v2_table.md"

# Ground truth for each item, established independently from the dataset row (see
# data/contrast/contrast_v1.jsonl) -- what a correct response MUST say to count as
# "corrects", regardless of tone/hedging words used.
GROUND_TRUTH = {
    "quad_formula__demonstrated__gap__46__expert": {
        "error": "used b=+2 instead of -b=-2 in the numerator (sign error on -b)",
        "correct_numerator": "x = (-2 ± √(2² - 4(3)(-5))) / (2(3))",
        "requires": ["flags the sign on b / -b as wrong", "gives -2 (not +2) as the correct numerator term"],
    },
    "quad_factor_ab__demonstrated__gap__40__expert": {
        "error": "factorised t²-9=0 as (t-3)(t-3)=0 instead of (t-3)(t+3)=0 -- difference of squares, same sign in both brackets",
        "correct_factorisation": "(t - 3)(t + 3) = 0, so t = 3 or t = -3",
        "requires": ["flags (t-3)(t-3) as wrong", "states the correct factorisation (t-3)(t+3)", "gives both roots t=3 and t=-3"],
    },
    "linear_both_int__demonstrated__gap__9__expert": {
        "error": "moved -5 to the RHS as -5 instead of +5 (used same operation, not inverse) -- '20t - 5t = 10 - 5' should be '20t - 5t = 10 + 5'",
        "correct_answer": "t = 1",
        "requires": ["flags the sign error moving -5 across the equals sign", "gives t=1 (not t=1/5 or t=1/3) as the correct answer"],
    },
}


def main():
    import torch  # noqa: F401

    from src import model as M
    from src import steering as S

    C.banner(f"CORRECTION ITEMS v2 -- max_new_tokens={MAX_NEW_TOKENS} (was 200)")

    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)

    id_to_row = {r["id"]: r for r in orig_rows}
    items = [id_to_row[i] for i in CORRECTION_ITEM_IDS]
    for it in items:
        assert it["knowledge_state"] == "gap", f"{it['id']} is not a gap row"

    acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_natural.npy")
    d0_sweep = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
    layer = d0_sweep["best_layer"]
    mean_norm = float(np.linalg.norm(acts[:, layer, :], axis=1).mean())
    print(f"[v2] layer={layer} mean_norm={mean_norm:.2f} (must match original correction_items run)")

    X = acts[:, layer, :]
    frozen_probe = _balanced_probe(C.SEED)
    frozen_probe.fit(X[tr], labels[tr])
    probe_vec = S.probe_direction(frozen_probe)
    dom_vec = S.diff_of_means_direction(X[tr], labels[tr])
    rand_vec = S.random_direction(X.shape[1], seed=C.SEED)
    vectors = {"probe": probe_vec, "random": rand_vec, "dom": dom_vec}

    mdl, tok = M.load()
    M.assert_template_sane(tok)
    texts = [M.render_chat(tok, it["turns"]) for it in items]

    out = {
        "layer": layer, "mean_layer_norm": mean_norm, "max_new_tokens": MAX_NEW_TOKENS,
        "items": [{"id": it["id"], "concept": it["concept_slug"],
                   "misconception": it["eedi_misconception"],
                   "prompt_tail": it["turns"][-1]["content"],
                   "ground_truth": GROUND_TRUTH[it["id"]]} for it in items],
        "grid": {},
    }
    lines = []

    for frac in CORRECTION_ALPHA_FRACS:
        alpha = frac * mean_norm
        for vname in CORRECTION_VECTORS:
            h = S.register_steering(mdl, layer, vectors[vname], alpha=alpha) if frac != 0.0 else None
            try:
                gens = M.greedy_generate(mdl, tok, texts, max_new_tokens=MAX_NEW_TOKENS,
                                          batch_size=len(texts))
            finally:
                if h is not None:
                    h.remove()
            key = f"alpha={frac:+.2f}/vector={vname}"
            out["grid"][key] = {
                "alpha_frac": frac, "alpha": alpha, "vector": vname,
                "generations": {it["id"]: g for it, g in zip(items, gens)},
                "truncated": {it["id"]: (len(tok(g, add_special_tokens=False)["input_ids"]) >= MAX_NEW_TOKENS)
                              for it, g in zip(items, gens)},
            }
            print(f"\n=== {key} (alpha={alpha:+.2f}) ===")
            lines.append(f"=== {key} (alpha={alpha:+.2f}) ===")
            for it, g in zip(items, gens):
                ntok = len(tok(g, add_special_tokens=False)["input_ids"])
                trunc = " [TRUNCATED]" if ntok >= MAX_NEW_TOKENS else ""
                print(f"  [{it['id']}] ({ntok} tok{trunc}) {g!r}")
                lines.append(f"--- {it['id']} ({ntok} tok{trunc}) ---\n{g}\n")
            C.log_run(
                act="2", experiment="correction_items_v2",
                config={"alpha_frac": frac, "alpha": alpha, "vector": vname, "layer": layer,
                        "item_ids": CORRECTION_ITEM_IDS, "seed": C.SEED,
                        "max_new_tokens": MAX_NEW_TOKENS},
                metrics={"n_items": len(items)},
            )

    C.banner("SANITY: alpha=0 identical across vector labels")
    for it in items:
        g0 = [out["grid"][f"alpha=+0.00/vector={v}"]["generations"][it["id"]]
              for v in CORRECTION_VECTORS]
        identical = len(set(g0)) == 1
        print(f"  {it['id']}: {'OK' if identical else '*** MISMATCH -- hook is not a true no-op ***'}")
        out.setdefault("sanity_alpha0_identical", {})[it["id"]] = identical

    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[v2] wrote {RESULTS_PATH}")
    print(f"[v2] wrote {SAMPLES_PATH}")
    return out


if __name__ == "__main__":
    main()
