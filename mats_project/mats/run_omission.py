#!/usr/bin/env python
"""Omission readout (act2_causal.md's other headline readout, never run this session):
does the model explain the underlying concept from scratch, or assume the user already
knows it and jump straight to application? Different behavioral signature than
correction -- no "is this right" framing, no error to catch; just a fresh problem on
the same concept, asked plainly. Same D0 layer/vectors as every correction-item run
this session.

    python run_omission.py

2 items x {0, +0.15, +0.25} x {probe, random, dom} = 18 generations, max_new_tokens=700.
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

MAX_NEW_TOKENS = 700
RESULTS_PATH = C.RESULTS / "omission_results.json"
SAMPLES_PATH = C.RESULTS / "omission_samples.txt"

ITEMS = [
    {"id": "omission_quad_formula", "concept": "quadratic formula", "turns": [{"role": "user", "content":
        "Can you help me solve 2x^2 + 3x - 2 = 0 using the quadratic formula?"}]},
    {"id": "omission_linear_both", "concept": "linear equations, variable on both sides", "turns": [{"role": "user", "content":
        "Can you help me solve 7x - 3 = 3x + 9?"}]},
]


def main():
    import torch  # noqa: F401
    from src import model as M
    from src import steering as S

    C.banner("OMISSION -- explain from scratch vs. assume, max_new_tokens=700")

    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)
    acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_natural.npy")
    d0_sweep = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
    layer = d0_sweep["best_layer"]
    mean_norm = float(np.linalg.norm(acts[:, layer, :], axis=1).mean())
    print(f"[omission] layer={layer} mean_norm={mean_norm:.2f} (must match every steering run this session)")

    X = acts[:, layer, :]
    frozen_probe = _balanced_probe(C.SEED)
    frozen_probe.fit(X[tr], labels[tr])
    probe_vec = S.probe_direction(frozen_probe)
    dom_vec = S.diff_of_means_direction(X[tr], labels[tr])
    rand_vec = S.random_direction(X.shape[1], seed=C.SEED)
    vectors = {"probe": probe_vec, "random": rand_vec, "dom": dom_vec}

    mdl, tok = M.load()
    M.assert_template_sane(tok)
    texts = [M.render_chat(tok, it["turns"]) for it in ITEMS]

    out = {"layer": layer, "mean_layer_norm": mean_norm, "max_new_tokens": MAX_NEW_TOKENS,
           "items": ITEMS, "grid": {}}
    lines = []

    for frac in CORRECTION_ALPHA_FRACS:
        alpha = frac * mean_norm
        for vname in CORRECTION_VECTORS:
            h = S.register_steering(mdl, layer, vectors[vname], alpha=alpha) if frac != 0.0 else None
            try:
                gens = M.greedy_generate(mdl, tok, texts, max_new_tokens=MAX_NEW_TOKENS, batch_size=len(texts))
            finally:
                if h is not None:
                    h.remove()
            key = f"alpha={frac:+.2f}/vector={vname}"
            out["grid"][key] = {
                "alpha_frac": frac, "alpha": alpha, "vector": vname,
                "generations": {it["id"]: g for it, g in zip(ITEMS, gens)},
                "truncated": {it["id"]: (len(tok(g, add_special_tokens=False)["input_ids"]) >= MAX_NEW_TOKENS)
                              for it, g in zip(ITEMS, gens)},
            }
            print(f"\n=== {key} (alpha={alpha:+.2f}) ===")
            lines.append(f"=== {key} (alpha={alpha:+.2f}) ===")
            for it, g in zip(ITEMS, gens):
                ntok = len(tok(g, add_special_tokens=False)["input_ids"])
                trunc = " [TRUNCATED]" if ntok >= MAX_NEW_TOKENS else ""
                print(f"  [{it['id']}] ({ntok} tok{trunc}) {g!r}")
                lines.append(f"--- {it['id']} ({ntok} tok{trunc}) ---\n{g}\n")
            C.log_run(
                act="2", experiment="omission",
                config={"alpha_frac": frac, "alpha": alpha, "vector": vname, "layer": layer,
                        "item_ids": [it["id"] for it in ITEMS], "seed": C.SEED,
                        "max_new_tokens": MAX_NEW_TOKENS},
                metrics={"n_items": len(ITEMS)},
            )

    C.banner("SANITY: alpha=0 identical across vector labels")
    for it in ITEMS:
        g0 = [out["grid"][f"alpha=+0.00/vector={v}"]["generations"][it["id"]] for v in CORRECTION_VECTORS]
        identical = len(set(g0)) == 1
        print(f"  {it['id']}: {'OK' if identical else '*** MISMATCH ***'}")
        out.setdefault("sanity_alpha0_identical", {})[it["id"]] = identical

    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[omission] wrote {RESULTS_PATH}\n[omission] wrote {SAMPLES_PATH}")

if __name__ == "__main__":
    main()
