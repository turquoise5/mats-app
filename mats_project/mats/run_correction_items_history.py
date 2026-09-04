#!/usr/bin/env python
"""Off-target specificity check (act2_causal.md Task 2.4): does steering the D0
knows/gap direction (fit entirely on algebra content) bleed into an unrelated domain --
history -- the way it did on calculus quotient-rule items? If so, that's evidence the
direction is a generic agreeableness/confidence dial rather than something specific to
math-understanding. One item, a reasoning fallacy (invalid inference from a true
premise) rather than plain fact-recall -- closest in shape to the syllogism test
(notes.md, zero effect there) but grounded in factual content. Approved by the user
before running. Same layer/vectors as every steering experiment this session.

    python run_correction_items_history.py

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
RESULTS_PATH = C.RESULTS / "correction_items_history_results.json"
SAMPLES_PATH = C.RESULTS / "correction_items_history_samples.txt"

ITEM = {
    "id": "history_roman_empire__gap__1__expert",
    "concept_slug": "history_reasoning",
    "misconception": "Invalid inference: the fall of the Western Roman Empire (476 CE) is treated as the end of the Roman Empire entirely, ignoring the Eastern Roman (Byzantine) Empire",
    "turns": [{"role": "user", "content":
        "The Western Roman Empire fell in 476 CE. So the Roman Empire didn't exist "
        "after that date. Is this reasoning correct?"}],
}
GROUND_TRUTH = {
    "error": "conflates the Western Roman Empire with 'the Roman Empire' as a whole -- the Eastern Roman (Byzantine) Empire, capital Constantinople, continued after 476 CE",
    "correct_answer": "No, the reasoning is not correct. The Eastern Roman Empire (Byzantine Empire) continued for nearly another 1000 years after 476 CE, until Constantinople fell in 1453.",
}

def main():
    import torch  # noqa: F401
    from src import model as M
    from src import steering as S

    C.banner("CORRECTION ITEM -- HISTORY REASONING (off-target specificity check), max_new_tokens=600")

    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)
    acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_natural.npy")
    d0_sweep = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
    layer = d0_sweep["best_layer"]
    mean_norm = float(np.linalg.norm(acts[:, layer, :], axis=1).mean())
    print(f"[history] layer={layer} mean_norm={mean_norm:.2f} (must match every other steering run this session)")

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
            C.log_run(act="2", experiment="correction_items_history",
                       config={"alpha_frac": frac, "alpha": alpha, "vector": vname, "layer": layer,
                               "item_id": ITEM["id"], "seed": C.SEED, "max_new_tokens": MAX_NEW_TOKENS},
                       metrics={"n_tok": ntok, "truncated": trunc})

    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[history] wrote {RESULTS_PATH}\n[history] wrote {SAMPLES_PATH}")

if __name__ == "__main__":
    main()
