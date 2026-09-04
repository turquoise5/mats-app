#!/usr/bin/env python
"""Causal demonstrated->stated transfer: the D0 vectors (fit entirely on `demonstrated`
rows) were never tested on `stated` content this session. Act 1's own headline finding
was that stated->demonstrated transfer FAILED passively (0.699, below the 0.75 bar)
while demonstrated->stated held up better. This tests the causal analogue: does
steering the demonstrated-trained direction change behavior on stated content?

Also fixes the failed omission test's design flaw -- stated rows are pure self-report +
fresh problem (no work to grade), so there's finally real contrast for an omission-
style readout: does steering toward "knows" make the model skip past the user's
*stated* confusion, and does steering toward "gap" make it over-explain despite the
user's stated confidence?

Matched pair, same concept, same register, quad_formula (a=/=1), novice:
  knows-stated: "i think maybe i understand how to use the quadratic formula..."
  gap-stated:   "i think maybe i don't really get how to use the quadratic formula..."

Same D0 layer/vectors as every steering experiment this session.

    python run_demonstrated_to_stated.py

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
RESULTS_PATH = C.RESULTS / "demonstrated_to_stated_results.json"
SAMPLES_PATH = C.RESULTS / "demonstrated_to_stated_samples.txt"

STATED_ITEM_IDS = [
    "quad_formula__stated__knows__0__novice",
    "quad_formula__stated__gap__0__novice",
]


def load_stated_items():
    with open(C.CONTRAST_FILE) as f:
        rows = [json.loads(l) for l in f]
    id_to_row = {r["id"]: r for r in rows}
    items = [id_to_row[i] for i in STATED_ITEM_IDS]
    assert len(items) == len(STATED_ITEM_IDS)
    return items


def main():
    import torch  # noqa: F401
    from src import model as M
    from src import steering as S

    C.banner("DEMONSTRATED -> STATED -- causal transfer + omission-style readout, max_new_tokens=700")

    items = load_stated_items()
    for it in items:
        print(f"  {it['id']} ({it['knowledge_state']}): {it['turns'][-1]['content'][:100]!r}")

    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)
    acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_natural.npy")
    d0_sweep = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
    layer = d0_sweep["best_layer"]
    mean_norm = float(np.linalg.norm(acts[:, layer, :], axis=1).mean())
    print(f"[dem2stated] layer={layer} mean_norm={mean_norm:.2f} (must match every steering run this session)")

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

    out = {"layer": layer, "mean_layer_norm": mean_norm, "max_new_tokens": MAX_NEW_TOKENS,
           "items": [{"id": it["id"], "knowledge_state": it["knowledge_state"],
                      "prompt": it["turns"][-1]["content"]} for it in items],
           "grid": {}}
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
                act="2", experiment="demonstrated_to_stated",
                config={"alpha_frac": frac, "alpha": alpha, "vector": vname, "layer": layer,
                        "item_ids": STATED_ITEM_IDS, "seed": C.SEED, "max_new_tokens": MAX_NEW_TOKENS},
                metrics={"n_items": len(items)},
            )

    C.banner("SANITY: alpha=0 identical across vector labels")
    for it in items:
        g0 = [out["grid"][f"alpha=+0.00/vector={v}"]["generations"][it["id"]] for v in CORRECTION_VECTORS]
        identical = len(set(g0)) == 1
        print(f"  {it['id']}: {'OK' if identical else '*** MISMATCH ***'}")
        out.setdefault("sanity_alpha0_identical", {})[it["id"]] = identical

    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[dem2stated] wrote {RESULTS_PATH}\n[dem2stated] wrote {SAMPLES_PATH}")

if __name__ == "__main__":
    main()
