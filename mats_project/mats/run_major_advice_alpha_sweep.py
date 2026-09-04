#!/usr/bin/env python
"""Raw dump, no grading: a generic, open-ended advice question with no right/wrong
answer at all -- "what should I major in?" -- swept across the full alpha range along
BOTH the probe and diff-of-means (dom) directions. Tests whether the D0 knows/gap
direction has any noticeable effect on unrelated, subjective advice-giving (tone,
confidence, content), not just on correction-shaped math items. Same layer/vectors as
every steering experiment this session.

    python run_major_advice_alpha_sweep.py
"""
from __future__ import annotations
import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"
import json
import numpy as np
from run_ablation_probes import CACHE_PREFIX, _balanced_probe, fit_layer_probes_balanced, load_variant_rows, merged_split
from src import config as C

MAX_NEW_TOKENS = 600
ALPHA_FRACS = [-2.0, -1.0, -0.5, -0.25, -0.1, 0.0, 0.1, 0.25, 0.5, 1.0, 2.0]
RESULTS_PATH = C.RESULTS / "major_advice_alpha_sweep_results.json"
SAMPLES_PATH = C.RESULTS / "major_advice_alpha_sweep_samples.txt"

ITEM = {
    "id": "major_advice__na__1__expert",
    "turns": [{"role": "user", "content": "What should I major in?"}],
}

def main():
    import torch  # noqa: F401
    from src import model as M
    from src import steering as S

    C.banner("MAJOR ADVICE ALPHA SWEEP -- probe direction, full range, no grading")
    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)
    acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_natural.npy")
    d0_sweep = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
    layer = d0_sweep["best_layer"]
    mean_norm = float(np.linalg.norm(acts[:, layer, :], axis=1).mean())
    print(f"[major] layer={layer} mean_norm={mean_norm:.2f}")

    X = acts[:, layer, :]
    frozen_probe = _balanced_probe(C.SEED)
    frozen_probe.fit(X[tr], labels[tr])
    probe_vec = S.probe_direction(frozen_probe)
    dom_vec = S.diff_of_means_direction(X[tr], labels[tr])
    rand_vec = S.random_direction(X.shape[1], seed=C.SEED)
    vectors = {"probe": probe_vec, "dom": dom_vec, "random": rand_vec}

    mdl, tok = M.load()
    M.assert_template_sane(tok)
    text = M.render_chat(tok, ITEM["turns"])

    out = {"layer": layer, "mean_layer_norm": mean_norm, "max_new_tokens": MAX_NEW_TOKENS,
           "item": ITEM, "sweep": {}}
    lines = []
    for vname, vec in vectors.items():
        for frac in ALPHA_FRACS:
            alpha = frac * mean_norm
            h = S.register_steering(mdl, layer, vec, alpha=alpha) if frac != 0.0 else None
            try:
                gen = M.greedy_generate(mdl, tok, [text], max_new_tokens=MAX_NEW_TOKENS, batch_size=1)[0]
            finally:
                if h is not None:
                    h.remove()
            ntok = len(tok(gen, add_special_tokens=False)["input_ids"])
            trunc = ntok >= MAX_NEW_TOKENS
            key = f"{vname}/{frac:+.2f}"
            out["sweep"][key] = {"vector": vname, "alpha_frac": frac, "alpha": alpha, "generation": gen,
                                  "n_tok": ntok, "truncated": trunc}
            print(f"\n=== vector={vname} alpha_frac={frac:+.2f} (alpha={alpha:+.2f}) ({ntok} tok{' TRUNCATED' if trunc else ''}) ===")
            print(f"  {gen!r}")
            lines.append(f"=== vector={vname} alpha_frac={frac:+.2f} (alpha={alpha:+.2f}) ({ntok} tok{' TRUNCATED' if trunc else ''}) ===\n{gen}\n")
            C.log_run(act="2", experiment="major_advice_alpha_sweep",
                       config={"alpha_frac": frac, "alpha": alpha, "vector": vname, "layer": layer,
                               "item_id": ITEM["id"], "seed": C.SEED, "max_new_tokens": MAX_NEW_TOKENS},
                       metrics={"n_tok": ntok, "truncated": trunc})

    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[major] wrote {RESULTS_PATH}\n[major] wrote {SAMPLES_PATH}")

if __name__ == "__main__":
    main()
