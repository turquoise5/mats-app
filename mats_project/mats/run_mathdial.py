#!/usr/bin/env python
"""Generalization check (act2_causal.md Task 2.6): apply the D0 vector (fit entirely
on the synthetic EEDI algebra contrast set) to real teacher-student dialogue data --
MathDial (Macina et al. 2023, GSM8K-grounded tutoring dialogues; downloaded from
huggingface.co/datasets/eth-nlped/mathdial). 3 items, each a real word problem + a
real, natural, incorrect student solution (not synthetic, not written for this
project). Does steering fit on synthetic algebra content move behavior on real
out-of-distribution tutoring data?

    python run_mathdial.py

3 items x {0, +0.15, +0.25} x {probe, random, dom} = 27 generations, max_new_tokens=700.
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
RESULTS_PATH = C.RESULTS / "mathdial_results.json"
SAMPLES_PATH = C.RESULTS / "mathdial_samples.txt"
MATHDIAL_PATH = C.DATA / "mathdial" / "test.jsonl"

TARGET_QIDS = [6000060, 6000057, 6000054]  # interest/percent, candy/gumballs, juggling


def load_items():
    with open(MATHDIAL_PATH) as f:
        rows = [json.loads(l) for l in f]
    seen, items = set(), []
    for r in rows:
        if r["qid"] in TARGET_QIDS and r["qid"] not in seen:
            seen.add(r["qid"])
            prompt = (f"{r['question']}\n\nHere's my solution:\n{r['student_incorrect_solution'].strip()}"
                       "\n\nIs this correct?")
            items.append({
                "id": f"mathdial_{r['qid']}", "qid": r["qid"],
                "teacher_described_confusion": r["teacher_described_confusion"],
                "ground_truth": r["ground_truth"], "turns": [{"role": "user", "content": prompt}],
            })
    assert len(items) == len(TARGET_QIDS), f"expected {len(TARGET_QIDS)} items, found {len(items)}"
    return items


def main():
    import torch  # noqa: F401
    from src import model as M
    from src import steering as S

    C.banner("MATHDIAL GENERALIZATION -- real teacher-student data, D0 vectors, max_new_tokens=700")

    items = load_items()
    print(f"[mathdial] loaded {len(items)} items: {[it['qid'] for it in items]}")

    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)
    acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_natural.npy")
    d0_sweep = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
    layer = d0_sweep["best_layer"]
    mean_norm = float(np.linalg.norm(acts[:, layer, :], axis=1).mean())
    print(f"[mathdial] layer={layer} mean_norm={mean_norm:.2f} (must match every steering run this session)")

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
           "items": [{k: v for k, v in it.items() if k != "turns"} | {"prompt": it["turns"][-1]["content"]}
                     for it in items],
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
                act="2", experiment="mathdial",
                config={"alpha_frac": frac, "alpha": alpha, "vector": vname, "layer": layer,
                        "item_ids": [it["id"] for it in items], "seed": C.SEED,
                        "max_new_tokens": MAX_NEW_TOKENS},
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
    print(f"\n[mathdial] wrote {RESULTS_PATH}\n[mathdial] wrote {SAMPLES_PATH}")

if __name__ == "__main__":
    main()
