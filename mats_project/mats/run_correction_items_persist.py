#!/usr/bin/env python
"""Does the causal steering story survive turn-distance the way passive probing did?
notes.md's persistence experiments showed a fresh probe refit on D1/D3 (1 or 3 neutral
filler turns pushing the user's real work back from the read position) recovers the
knows/gap distinction almost as well as D0 (0.930->0.930->0.927 balanced acc, natural).
That was pure classification. This asks the causal question: fit fresh probe/dom
vectors AT D1's and D3's own best layer, on D1/D3 activations, and steer the SAME 3
EEDI correction items -- with the matching neutral filler turns actually appended to
the conversation -- to see if the correction-item sycophancy/false-affirmation pattern
still shows up when read out from a buried-context representation, not just D0's
freshest-turn one.

    python run_correction_items_persist.py

2 levels (D1, D3) x 3 items x {0, +0.15, +0.25} x {probe, random, dom}
= 54 generations, max_new_tokens=600.
"""
from __future__ import annotations
import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"
import json
import numpy as np
from run_ablation_probes import CACHE_PREFIX, NEUTRAL_PAIRS, _balanced_probe, extend_turns, load_variant_rows, merged_split
from run_steering import CORRECTION_ALPHA_FRACS, CORRECTION_ITEM_IDS, CORRECTION_VECTORS
from src import config as C

MAX_NEW_TOKENS = 600
RESULTS_PATH = C.RESULTS / "correction_items_persist_results.json"
SAMPLES_PATH = C.RESULTS / "correction_items_persist_samples.txt"

# label -> (n_pairs appended, own best layer, natural refit -- from persist_results.json)
LEVELS = {"D1": {"n_pairs": 1, "layer": 21}, "D3": {"n_pairs": 3, "layer": 22}}


def main():
    import torch  # noqa: F401
    from src import model as M
    from src import steering as S

    C.banner("CORRECTION ITEMS -- PERSISTENCE (D1/D3 own-probe steering), max_new_tokens=600")

    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)
    id_to_row = {r["id"]: r for r in orig_rows}
    items = [id_to_row[i] for i in CORRECTION_ITEM_IDS]
    for it in items:
        assert it["knowledge_state"] == "gap", f"{it['id']} is not a gap row"

    mdl, tok = M.load()
    M.assert_template_sane(tok)

    out = {"max_new_tokens": MAX_NEW_TOKENS, "levels": {}, "grid": {}}
    lines = []

    for label, cfg in LEVELS.items():
        layer, n_pairs = cfg["layer"], cfg["n_pairs"]
        acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_{label}_natural.npy")
        X = acts[:, layer, :]
        mean_norm = float(np.linalg.norm(X, axis=1).mean())
        print(f"\n[persist] level={label} layer={layer} mean_norm={mean_norm:.2f} n_pairs={n_pairs}")

        frozen_probe = _balanced_probe(C.SEED)
        frozen_probe.fit(X[tr], labels[tr])
        val_acc = frozen_probe.score(X[te], labels[te])
        print(f"[persist] level={label} probe val balanced-ish acc (plain, sanity only)={val_acc:.4f}")

        probe_vec = S.probe_direction(frozen_probe)
        dom_vec = S.diff_of_means_direction(X[tr], labels[tr])
        rand_vec = S.random_direction(X.shape[1], seed=C.SEED)
        vectors = {"probe": probe_vec, "random": rand_vec, "dom": dom_vec}
        out["levels"][label] = {"layer": layer, "n_pairs": n_pairs, "mean_norm": mean_norm,
                                 "sanity_val_acc": val_acc}

        item_turns = [extend_turns(it["turns"], NEUTRAL_PAIRS, n_pairs) for it in items]
        texts = [M.render_chat(tok, t) for t in item_turns]

        for frac in CORRECTION_ALPHA_FRACS:
            alpha = frac * mean_norm
            for vname in CORRECTION_VECTORS:
                h = S.register_steering(mdl, layer, vectors[vname], alpha=alpha) if frac != 0.0 else None
                try:
                    gens = M.greedy_generate(mdl, tok, texts, max_new_tokens=MAX_NEW_TOKENS, batch_size=len(texts))
                finally:
                    if h is not None:
                        h.remove()
                key = f"{label}/alpha={frac:+.2f}/vector={vname}"
                out["grid"][key] = {
                    "level": label, "alpha_frac": frac, "alpha": alpha, "vector": vname, "layer": layer,
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
                    act="2", experiment="correction_items_persist",
                    config={"level": label, "alpha_frac": frac, "alpha": alpha, "vector": vname,
                            "layer": layer, "item_ids": CORRECTION_ITEM_IDS, "seed": C.SEED,
                            "max_new_tokens": MAX_NEW_TOKENS},
                    metrics={"n_items": len(items)},
                )

    C.banner("SANITY: alpha=0 identical across vector labels, per level")
    for label in LEVELS:
        for it in items:
            g0 = [out["grid"][f"{label}/alpha=+0.00/vector={v}"]["generations"][it["id"]]
                  for v in CORRECTION_VECTORS]
            identical = len(set(g0)) == 1
            print(f"  {label} {it['id']}: {'OK' if identical else '*** MISMATCH ***'}")
            out.setdefault("sanity_alpha0_identical", {}).setdefault(label, {})[it["id"]] = identical

    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[persist] wrote {RESULTS_PATH}\n[persist] wrote {SAMPLES_PATH}")

if __name__ == "__main__":
    main()
