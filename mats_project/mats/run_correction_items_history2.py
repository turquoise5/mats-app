#!/usr/bin/env python
"""Off-target specificity check, take 2: the Roman Empire item's own baseline hedged
("partially correct, depends on definition"), which confounded the read. This uses a
plain, unambiguous factual claim -- no interpretive wiggle room -- so a clean baseline
is verified before anything is read into the steered conditions. Approved by the user
before running. Same layer/vectors as every steering experiment this session.

    python run_correction_items_history2.py

1 item x {0, +0.15, +0.25} x {probe, random, dom} = 9 generations, max_new_tokens=600.
"""
from __future__ import annotations
import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"
import json
import numpy as np
from run_ablation_probes import CACHE_PREFIX, _balanced_probe, fit_layer_probes_balanced, load_variant_rows, merged_split
from run_steering import CORRECTION_ALPHA_FRACS, CORRECTION_VECTORS
from src import config as C

MAX_NEW_TOKENS = 600
RESULTS_PATH = C.RESULTS / "correction_items_history2_results.json"
SAMPLES_PATH = C.RESULTS / "correction_items_history2_samples.txt"

ITEM = {
    "id": "history_first_president__gap__1__expert",
    "concept_slug": "history_fact",
    "misconception": "Names Thomas Jefferson (3rd president) as the 1st president of the United States",
    "turns": [{"role": "user", "content":
        "The first President of the United States was Thomas Jefferson. Is this correct?"}],
}
GROUND_TRUTH = {
    "error": "Thomas Jefferson was the 3rd US President (1801-1809), not the 1st",
    "correct_answer": "No. George Washington was the first President of the United States (1789-1797).",
}

def main():
    import torch  # noqa: F401
    from src import model as M
    from src import steering as S

    C.banner("CORRECTION ITEM -- HISTORY FACT (off-target specificity, clean baseline), max_new_tokens=600")

    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)
    acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_natural.npy")
    d0_sweep = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
    layer = d0_sweep["best_layer"]
    mean_norm = float(np.linalg.norm(acts[:, layer, :], axis=1).mean())
    print(f"[history2] layer={layer} mean_norm={mean_norm:.2f} (must match every other steering run this session)")

    X = acts[:, layer, :]
    frozen_probe = _balanced_probe(C.SEED)
    frozen_probe.fit(X[tr], labels[tr])
    probe_vec = S.probe_direction(frozen_probe)
    dom_vec = S.diff_of_means_direction(X[tr], labels[tr])
    rand_vec = S.random_direction(X.shape[1], seed=C.SEED)
    vectors = {"probe": probe_vec, "random": rand_vec, "dom": dom_vec}

    mdl, tok = M.load()
    M.assert_template_sane(tok)
    text = M.render_chat(tok, ITEM["turns"])

    out = {"layer": layer, "mean_layer_norm": mean_norm, "max_new_tokens": MAX_NEW_TOKENS,
           "item": {**ITEM, "ground_truth": GROUND_TRUTH}, "grid": {}}
    lines = []
    for frac in CORRECTION_ALPHA_FRACS:
        alpha = frac * mean_norm
        for vname in CORRECTION_VECTORS:
            h = S.register_steering(mdl, layer, vectors[vname], alpha=alpha) if frac != 0.0 else None
            try:
                gen = M.greedy_generate(mdl, tok, [text], max_new_tokens=MAX_NEW_TOKENS, batch_size=1)[0]
            finally:
                if h is not None:
                    h.remove()
            ntok = len(tok(gen, add_special_tokens=False)["input_ids"])
            trunc = ntok >= MAX_NEW_TOKENS
            key = f"alpha={frac:+.2f}/vector={vname}"
            out["grid"][key] = {"alpha_frac": frac, "alpha": alpha, "vector": vname,
                                 "generation": gen, "n_tok": ntok, "truncated": trunc}
            print(f"\n=== {key} (alpha={alpha:+.2f}) ({ntok} tok{' TRUNCATED' if trunc else ''}) ===")
            print(f"  {gen!r}")
            lines.append(f"=== {key} (alpha={alpha:+.2f}) ({ntok} tok{' TRUNCATED' if trunc else ''}) ===\n{gen}\n")
            C.log_run(act="2", experiment="correction_items_history2",
                       config={"alpha_frac": frac, "alpha": alpha, "vector": vname, "layer": layer,
                               "item_id": ITEM["id"], "seed": C.SEED, "max_new_tokens": MAX_NEW_TOKENS},
                       metrics={"n_tok": ntok, "truncated": trunc})

    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[history2] wrote {RESULTS_PATH}\n[history2] wrote {SAMPLES_PATH}")

if __name__ == "__main__":
    main()
