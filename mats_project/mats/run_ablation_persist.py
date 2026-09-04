#!/usr/bin/env python
"""Does the ablation-identified residual signal have causal teeth, not just passive
decodability? Every steering vector used this whole session was fit on `orig`
(unablated) activations. This fits fresh probe/dom vectors on `A` (answer-masked) and
`CTRL` (length-matched random deletion) activations instead -- the passive ablation
study's own two conditions -- and tests them causally on the same 3 correction items
used throughout, IN THEIR ORIGINAL UNABLATED FORM.

Why original text, not ablated text, as the generation target: ablated text was built
for single-forward-pass extraction, not generation, and is frequently incoherent as a
prompt -- e.g. ablation B drops the question itself on `quad_formula` ("...I set a=3,
b=2, c=-5." with no "which option matches?" left). Using ablated text as the actual
generation prompt would confound "steering effect" with "the model is reacting to a
broken prompt." So: fit the direction on the ablated corpus, test it on the same
coherent target every other correction-item experiment used.

LAYER, DOCUMENTED: fixed at L20 for both A and CTRL, NOT each variant's own
individually-optimal layer (per the earlier ablation write-up, A/B/AB/CTRL's own best
layers differ from orig's L20, and mixing "which text the vector was fit on" with
"which layer" would confound two variables into one comparison). L20 is also within
the "recovered" zone of B's own U-shaped accuracy curve reported earlier (trough is
mid-stack), so it is not a layer where these variants are known to be uninformative.

    python run_ablation_persist.py

2 variants (A, CTRL) x 2 vectors (probe, dom) x 3 items x {+0.15, +0.25}
= 24 generations, max_new_tokens=700. alpha=0 and `random` are not rerun -- both are
already on record from the earlier correction_items_v2 run (random vector is
text-independent; the alpha=0 no-op is definitionally identical for any vector).
"""
from __future__ import annotations
import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"
import json
import numpy as np
from run_ablation_probes import CACHE_PREFIX, _balanced_probe, load_variant_rows, merged_split
from run_steering import CORRECTION_ITEM_IDS
from src import config as C

LAYER = 20  # documented above: fixed, not each variant's own best layer
ALPHA_FRACS = [0.15, 0.25]
VARIANTS = ["A", "CTRL"]
MAX_NEW_TOKENS = 700
RESULTS_PATH = C.RESULTS / "ablation_persist_results.json"
SAMPLES_PATH = C.RESULTS / "ablation_persist_samples.txt"


def main():
    import torch  # noqa: F401
    from src import model as M
    from src import steering as S

    C.banner(f"ABLATION PERSIST -- A/CTRL-fit vectors on original correction items, layer=L{LAYER}")

    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)
    print(f"[abl_persist] merged split: n_train={len(tr)} n_test={len(te)}")

    id_to_row = {r["id"]: r for r in orig_rows}
    items = [id_to_row[i] for i in CORRECTION_ITEM_IDS]
    for it in items:
        assert it["knowledge_state"] == "gap", f"{it['id']} is not a gap row"

    mdl, tok = M.load()
    M.assert_template_sane(tok)
    texts = [M.render_chat(tok, it["turns"]) for it in items]

    out = {"layer": LAYER, "max_new_tokens": MAX_NEW_TOKENS, "variants": {}, "grid": {}}
    lines = []

    for variant in VARIANTS:
        acts = np.load(C.CACHE / f"{CACHE_PREFIX}_{variant}_natural.npy")
        X = acts[:, LAYER, :]
        mean_norm = float(np.linalg.norm(X, axis=1).mean())
        print(f"\n[abl_persist] variant={variant} layer={LAYER} mean_norm={mean_norm:.2f}")

        frozen_probe = _balanced_probe(C.SEED)
        frozen_probe.fit(X[tr], labels[tr])
        probe_vec = S.probe_direction(frozen_probe)
        dom_vec = S.diff_of_means_direction(X[tr], labels[tr])
        vectors = {"probe": probe_vec, "dom": dom_vec}
        out["variants"][variant] = {"layer": LAYER, "mean_norm": mean_norm}

        for frac in ALPHA_FRACS:
            alpha = frac * mean_norm
            for vname, vec in vectors.items():
                h = S.register_steering(mdl, LAYER, vec, alpha=alpha)
                try:
                    gens = M.greedy_generate(mdl, tok, texts, max_new_tokens=MAX_NEW_TOKENS, batch_size=len(texts))
                finally:
                    h.remove()
                key = f"{variant}/alpha={frac:+.2f}/vector={vname}"
                out["grid"][key] = {
                    "variant": variant, "alpha_frac": frac, "alpha": alpha, "vector": vname, "layer": LAYER,
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
                    act="2", experiment="ablation_persist",
                    config={"variant": variant, "alpha_frac": frac, "alpha": alpha, "vector": vname,
                            "layer": LAYER, "item_ids": CORRECTION_ITEM_IDS, "seed": C.SEED,
                            "max_new_tokens": MAX_NEW_TOKENS},
                    metrics={"n_items": len(items)},
                )

    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[abl_persist] wrote {RESULTS_PATH}\n[abl_persist] wrote {SAMPLES_PATH}")

if __name__ == "__main__":
    main()
