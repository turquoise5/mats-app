#!/usr/bin/env python
"""Raw dump, no grading: a plain arithmetic item, swept across the full alpha range
(same range as the original steer_d0 dose-response) along the probe direction only.
Same layer/vector as every steering experiment this session.

    python run_arithmetic_alpha_sweep.py
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
RESULTS_PATH = C.RESULTS / "arithmetic_alpha_sweep_results.json"
SAMPLES_PATH = C.RESULTS / "arithmetic_alpha_sweep_samples.txt"

ITEM = {
    "id": "arithmetic_subtraction__gap__1__expert",
    "turns": [{"role": "user", "content":
        "I calculated 347 - 189 = 168. Is this correct?"}],
}

def main():
    import torch  # noqa: F401
    from src import model as M
    from src import steering as S

    C.banner("ARITHMETIC ALPHA SWEEP -- probe direction, full range, no grading")
    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)
    acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_natural.npy")
    d0_sweep = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
    layer = d0_sweep["best_layer"]
    mean_norm = float(np.linalg.norm(acts[:, layer, :], axis=1).mean())
    print(f"[arith] layer={layer} mean_norm={mean_norm:.2f}")

    X = acts[:, layer, :]
    frozen_probe = _balanced_probe(C.SEED)
    frozen_probe.fit(X[tr], labels[tr])
    probe_vec = S.probe_direction(frozen_probe)

    mdl, tok = M.load()
    M.assert_template_sane(tok)
    text = M.render_chat(tok, ITEM["turns"])

    out = {"layer": layer, "mean_layer_norm": mean_norm, "max_new_tokens": MAX_NEW_TOKENS,
           "item": ITEM, "sweep": {}}
    lines = []
    for frac in ALPHA_FRACS:
        alpha = frac * mean_norm
        h = S.register_steering(mdl, layer, probe_vec, alpha=alpha) if frac != 0.0 else None
        try:
            gen = M.greedy_generate(mdl, tok, [text], max_new_tokens=MAX_NEW_TOKENS, batch_size=1)[0]
        finally:
            if h is not None:
                h.remove()
        ntok = len(tok(gen, add_special_tokens=False)["input_ids"])
        trunc = ntok >= MAX_NEW_TOKENS
        key = f"{frac:+.2f}"
        out["sweep"][key] = {"alpha_frac": frac, "alpha": alpha, "generation": gen,
                              "n_tok": ntok, "truncated": trunc}
        print(f"\n=== alpha_frac={frac:+.2f} (alpha={alpha:+.2f}) ({ntok} tok{' TRUNCATED' if trunc else ''}) ===")
        print(f"  {gen!r}")
        lines.append(f"=== alpha_frac={frac:+.2f} (alpha={alpha:+.2f}) ({ntok} tok{' TRUNCATED' if trunc else ''}) ===\n{gen}\n")
        C.log_run(act="2", experiment="arithmetic_alpha_sweep",
                   config={"alpha_frac": frac, "alpha": alpha, "vector": "probe", "layer": layer,
                           "item_id": ITEM["id"], "seed": C.SEED, "max_new_tokens": MAX_NEW_TOKENS},
                   metrics={"n_tok": ntok, "truncated": trunc})

    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[arith] wrote {RESULTS_PATH}\n[arith] wrote {SAMPLES_PATH}")

if __name__ == "__main__":
    main()
