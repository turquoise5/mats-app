#!/usr/bin/env python
"""GPU Agent driver for `handover_gpu_ablation_probes.md`: probe refits on the five
Act 1 text-ablation variants (orig, A, B, AB, CTRL), demonstrated subset only.

    python run_ablation_probes.py extract   # GPU -- cache activations, 5 variants x 2 positions
    python run_ablation_probes.py probe     # CPU -- per-layer probes, control tasks, reference check
    python run_ablation_probes.py plot      # CPU -- results/figs/act1_ablations.png
    python run_ablation_probes.py all       # extract -> probe -> plot

Reuses src/model.py unchanged for extraction. Reads data/contrast/contrast_v1.jsonl and
data/contrast/contrast_v1_abl{A,B,AB,CTRL}.jsonl read-only; never writes to any of them.
"""

from __future__ import annotations

# Rule 3 of the handover: pin BLAS threads before numpy/sklearn are imported. This box
# has 128 cores; unpinned OpenBLAS made an earlier probe run ~11x slower (see notes.md).
import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"

import json
import re
import sys

import numpy as np

from src import config as C
from src import probes as P

CACHE_PREFIX = "abl"
VARIANTS = ["orig", "A", "B", "AB", "CTRL"]
RESULTS_PATH = C.RESULTS / "ablation_probe_results.json"
STATS_PATH = C.CONTRAST / "ablation_stats.json"
FIG_PATH = C.FIGS / "act1_ablations.png"

# Act 1's reference numbers for `within-demonstrated-by-eedi` (notes.md), which this run
# must reproduce under the *same* pipeline (probes.make_probe, plain accuracy) before any
# ablation comparison can be trusted.
REF_NATURAL = 0.899
REF_ELICITED = 0.888
REF_TOL = 0.03


def variant_path(v: str):
    return C.CONTRAST / "contrast_v1.jsonl" if v == "orig" else C.CONTRAST / f"contrast_v1_abl{v}.jsonl"


def load_variant_rows(v: str) -> list[dict]:
    rows = C.read_jsonl(variant_path(v))
    if v == "orig":
        rows = [r for r in rows if r["disclosure"] == "demonstrated"]
    return rows


def load_all_variant_rows():
    out = {v: load_variant_rows(v) for v in VARIANTS}
    ref_ids = [r["id"] for r in out["orig"]]
    for v in VARIANTS:
        ids_v = [r["id"] for r in out[v]]
        if ids_v != ref_ids:
            raise RuntimeError(f"{v} id order differs from orig -- alignment is void.")
    return out, ref_ids


def normalise_text(rows: list[dict]) -> list[str]:
    return [re.sub(r"\s+", " ", r["turns"][0]["content"].lower().strip()) for r in rows]


# --------------------------------------------------------------------------------------
# extract
# --------------------------------------------------------------------------------------

def step_extract(batch_size: int = 8):
    import torch  # noqa: F401  (import here so other steps need no GPU stack)

    from src import model as M

    C.banner("ACT 1 ABLATIONS — EXTRACT ACTIVATIONS (5 variants x 2 read positions)")
    variant_rows, ids = load_all_variant_rows()
    print(f"[extract] {len(ids)} demonstrated rows per variant, aligned across "
          f"{len(VARIANTS)} variants: {VARIANTS}")

    with open(C.CACHE / f"{CACHE_PREFIX}_ids.json", "w") as f:
        json.dump(ids, f)

    mdl, tok = M.load()
    env = M.print_env(mdl, tok)
    M.assert_template_sane(tok)

    # Verify batched == unbatched indexing once, on orig/natural text -- this tests the
    # extraction mechanism (padding side, last-token index), not the ablated data, so it
    # does not need repeating per variant.
    orig_natural_texts = [M.render_chat(tok, r["turns"]) for r in variant_rows["orig"]]
    M.verify_last_token_indexing(mdl, tok, orig_natural_texts)

    for variant in VARIANTS:
        rows = variant_rows[variant]
        for position in C.READ_POSITIONS:
            if position == "elicited":
                texts = [
                    M.render_chat(tok, r["turns"],
                                  C.ACT1_ELICIT_PREFIX.format(concept=r["concept"]))
                    for r in rows
                ]
            else:
                texts = [M.render_chat(tok, r["turns"]) for r in rows]

            print(f"\n[extract] variant={variant} position={position} n={len(texts)}")
            acts = M.last_token_hidden(mdl, tok, texts, batch_size=batch_size)
            if acts.shape[1] != 37:
                raise RuntimeError(
                    f"variant={variant} position={position}: expected 37 read points "
                    f"(embeddings + 36 layers), got {acts.shape[1]}."
                )

            out_path = C.CACHE / f"{CACHE_PREFIX}_{variant}_{position}.npy"
            np.save(out_path, acts.astype(np.float32))
            print(f"  [extract] wrote {out_path} shape={acts.shape} dtype=float32")

            C.log_run(
                act="1", experiment="ablation_extract",
                config={"variant": variant, "position": position, "batch_size": batch_size,
                        "elicit_prefix_template": C.ACT1_ELICIT_PREFIX, **env},
                metrics={"shape": list(acts.shape), "n_rows": len(rows)},
            )

    print("\n[extract] done. Cached: "
          f"{[f'{CACHE_PREFIX}_{v}_{p}.npy' for v in VARIANTS for p in C.READ_POSITIONS]}")


# --------------------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------------------

def _balanced_probe(seed: int):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return make_pipeline(
        StandardScaler(),
        LogisticRegression(C=1.0, max_iter=3000, class_weight="balanced", random_state=seed),
    )


def _fit_one_layer(acts, labels, tr, te, layer, seed):
    from threadpoolctl import threadpool_limits

    with threadpool_limits(limits=1):
        from sklearn.metrics import balanced_accuracy_score

        X = acts[:, layer, :]
        p = _balanced_probe(seed)
        p.fit(X[tr], labels[tr])
        pred = p.predict(X[te])
        bal_acc = balanced_accuracy_score(labels[te], pred)
        return float(bal_acc)


def fit_layer_probes_balanced(acts, labels, tr, te, seed=0, n_jobs=None):
    """Handover §4.2 pipeline: StandardScaler + LogisticRegression(C=1.0, max_iter=3000,
    class_weight='balanced', seed 0), scored by balanced accuracy, all 37 read points.
    This is deliberately NOT `probes.make_probe` (which has no class_weight and is scored
    by plain accuracy) -- that pipeline is reserved for the §4.3 reference check, where
    reproducing Act 1's exact numbers is the point. Parallelised across layers, one BLAS
    thread per worker process (handover rule 3)."""
    from joblib import Parallel, delayed

    n_layers_plus1 = acts.shape[1]
    n_jobs = n_jobs or min(n_layers_plus1, os.cpu_count() or 1)
    accs = Parallel(n_jobs=n_jobs)(
        delayed(_fit_one_layer)(acts, labels, tr, te, layer, seed)
        for layer in range(n_layers_plus1)
    )
    accs = np.array(accs)
    return {"val_acc": accs, "best_layer": int(np.argmax(accs)), "best_acc": float(np.max(accs))}


def step_probe():
    C.banner("ACT 1 ABLATIONS — PER-LAYER PROBES, CONTROL TASKS, REFERENCE CHECK")

    variant_rows, ids = load_all_variant_rows()
    with open(C.CACHE / f"{CACHE_PREFIX}_ids.json") as f:
        cached_ids = json.load(f)
    if cached_ids != ids:
        raise RuntimeError(
            f"{CACHE_PREFIX}_ids.json does not match the current row order -- activations "
            "and labels would silently misalign. Re-run `extract`."
        )

    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in variant_rows["orig"]])
    groups = np.array([r["eedi_question_id"] for r in variant_rows["orig"]])
    n_gap, n_knows = int((labels == 0).sum()), int((labels == 1).sum())
    print(f"[probe] labels: gap={n_gap} knows={n_knows}  "
          f"groups: {len(set(groups.tolist()))} unique eedi_question_id")

    # One split, built once from orig, reused for all five variants (row order aligned).
    tr, te = P.split_indices(labels, groups=groups, test_size=0.2, seed=C.SEED)
    print(f"[probe] split: n_train={len(tr)} n_test={len(te)} "
          f"(groups grouped by eedi_question_id, test_size=0.2, seed={C.SEED})")
    majority = P.majority_baseline(labels, val_idx=te)

    if STATS_PATH.exists():
        ablation_stats = json.load(open(STATS_PATH))
    else:
        ablation_stats = None
        print("[probe] WARNING: ablation_stats.json not found -- TF-IDF baselines will be null.")

    all_results = {}
    reference_check = {}

    for position in C.READ_POSITIONS:
        print(f"\n[probe] === position={position} ===")
        pos_results = {}

        for variant in VARIANTS:
            acts = np.load(C.CACHE / f"{CACHE_PREFIX}_{variant}_{position}.npy")
            print(f"  [probe] variant={variant} acts={acts.shape} "
                  f"labels gap={n_gap} knows={n_knows} n_train={len(tr)} n_test={len(te)}")

            if variant == "orig":
                ref_res = P.fit_layer_probes_explicit(acts, labels, tr, te, seed=C.SEED, verbose=False)
                reference_check[position] = {
                    "acc": ref_res["best_acc"], "best_layer": ref_res["best_layer"],
                    "expected": REF_NATURAL if position == "natural" else REF_ELICITED,
                }
                exp = reference_check[position]["expected"]
                diff = ref_res["best_acc"] - exp
                passed = abs(diff) <= REF_TOL
                reference_check[position]["pass"] = passed
                print(f"    [reference check] Act-1 pipeline best_acc={ref_res['best_acc']:.3f} "
                      f"@L{ref_res['best_layer']} vs expected ~{exp:.3f} "
                      f"(diff {diff:+.3f}) -> {'PASS' if passed else 'FAIL'}")
                if not passed:
                    print("    *** STOP-AND-REPORT CONDITION (handover §4.3): orig does not "
                          "reproduce the Act 1 reference number within tolerance. Reported, "
                          "not silently smoothed over -- see notes.md. ***")

            res = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
            ctrl = P.control_task(
                acts, labels, content_keys=normalise_text(variant_rows[variant]),
                groups=groups, seed=C.SEED,
            )
            thresh = P.leakage_threshold(majority, len(te), acts.shape[1])
            control_max = float(ctrl.max())
            control_clean = control_max <= thresh

            tfidf_acc = None
            if ablation_stats is not None:
                tfidf_acc = (ablation_stats["variants"][variant]["tfidf_specified_pipeline"]
                             ["qid"]["balanced_accuracy"])

            metrics = {
                "val_acc": res["val_acc"].tolist(),
                "best_acc": res["best_acc"],
                "best_layer": res["best_layer"],
                "n_train": len(tr),
                "n_test": len(te),
                "majority_baseline": majority,
                "control_acc": ctrl.tolist(),
                "control_max": control_max,
                "leakage_threshold": thresh,
                "control_clean": control_clean,
                "tfidf_acc_qid": tfidf_acc,
            }
            pos_results[variant] = metrics

            print(f"    balanced_acc={metrics['best_acc']:.3f}@L{metrics['best_layer']:<2d} "
                  f"majority={majority:.3f} tfidf={tfidf_acc if tfidf_acc is None else f'{tfidf_acc:.3f}'} "
                  f"ctrl_max={control_max:.3f} thresh={thresh:.3f} "
                  f"{'CLEAN' if control_clean else '*** LEAKY ***'}")

            np.save(C.RESULTS / f"abl_acc_{variant}_{position}.npy", res["val_acc"])
            np.save(C.RESULTS / f"abl_ctrl_{variant}_{position}.npy", ctrl)

        all_results[position] = pos_results

    # -- summary table -----------------------------------------------------------------
    C.banner("SUMMARY: variant | natural | elicited | TF-IDF | delta vs orig | delta vs CTRL")
    header = f"{'variant':6s} {'natural':>8s} {'elicited':>9s} {'tfidf(qid)':>11s} " \
             f"{'d_nat_orig':>11s} {'d_eli_orig':>11s} {'d_nat_ctrl':>11s} {'d_eli_ctrl':>11s}"
    print(header)
    summary_rows = []
    orig_nat = all_results["natural"]["orig"]["best_acc"]
    orig_eli = all_results["elicited"]["orig"]["best_acc"]
    ctrl_nat = all_results["natural"]["CTRL"]["best_acc"]
    ctrl_eli = all_results["elicited"]["CTRL"]["best_acc"]
    for v in VARIANTS:
        nat = all_results["natural"][v]["best_acc"]
        eli = all_results["elicited"][v]["best_acc"]
        tfidf = all_results["natural"][v]["tfidf_acc_qid"]
        row = {
            "variant": v, "natural": nat, "elicited": eli, "tfidf_qid": tfidf,
            "delta_natural_vs_orig": nat - orig_nat, "delta_elicited_vs_orig": eli - orig_eli,
            "delta_natural_vs_ctrl": nat - ctrl_nat, "delta_elicited_vs_ctrl": eli - ctrl_eli,
        }
        summary_rows.append(row)
        print(f"{v:6s} {nat:8.3f} {eli:9.3f} {(tfidf if tfidf is not None else float('nan')):11.3f} "
              f"{row['delta_natural_vs_orig']:+11.3f} {row['delta_elicited_vs_orig']:+11.3f} "
              f"{row['delta_natural_vs_ctrl']:+11.3f} {row['delta_elicited_vs_ctrl']:+11.3f}")

    out = {
        "reference_check": reference_check,
        "split": {"n_train": len(tr), "n_test": len(te), "grouped_by": "eedi_question_id",
                  "test_size": 0.2, "seed": C.SEED, "majority_baseline": majority},
        "pipeline": "StandardScaler + LogisticRegression(C=1.0, max_iter=3000, "
                    "class_weight='balanced', seed=0), scored by balanced_accuracy_score, "
                    "37 read points, condition=within-demonstrated (binary gap/knows), "
                    "grouped by eedi_question_id",
        "results": all_results,
        "summary": summary_rows,
    }
    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n[probe] wrote {RESULTS_PATH}")

    for v in VARIANTS:
        C.log_run(
            act="1", experiment=f"ablation_probe/{v}",
            config={"variant": v, "pipeline": out["pipeline"], "seed": C.SEED,
                    "n_train": len(tr), "n_test": len(te)},
            metrics={
                "natural": {k: all_results["natural"][v][k]
                            for k in ("best_acc", "best_layer", "control_max",
                                      "leakage_threshold", "control_clean", "tfidf_acc_qid")},
                "elicited": {k: all_results["elicited"][v][k]
                             for k in ("best_acc", "best_layer", "control_max",
                                       "leakage_threshold", "control_clean", "tfidf_acc_qid")},
                "majority_baseline": majority,
            },
        )

    return out


# --------------------------------------------------------------------------------------
# plot
# --------------------------------------------------------------------------------------

VARIANT_COLORS = {"orig": "black", "A": "tab:blue", "B": "tab:orange",
                   "AB": "tab:green", "CTRL": "tab:red"}


def step_plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    C.banner("ACT 1 ABLATIONS — PLOT")
    with open(RESULTS_PATH) as f:
        out = json.load(f)
    all_results = out["results"]

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), squeeze=False)
    for ax, position in zip(axes.flat, C.READ_POSITIONS):
        for v in VARIANTS:
            m = all_results[position][v]
            acc = np.array(m["val_acc"])
            x = np.arange(len(acc))
            ax.plot(x, acc, marker="o", ms=2.5, color=VARIANT_COLORS[v], label=f"{v} probe")
            if m["tfidf_acc_qid"] is not None:
                ax.axhline(m["tfidf_acc_qid"], ls="--", lw=1, alpha=0.6,
                           color=VARIANT_COLORS[v], label=f"{v} TF-IDF")
        ax.axhline(out["split"]["majority_baseline"], color="k", ls=":", lw=1, alpha=0.5,
                   label="majority")
        ax.set_title(f"{position}\n(n_train={out['split']['n_train']}, "
                      f"n_test={out['split']['n_test']}, grouped by eedi_question_id)",
                      fontsize=9)
        ax.set_xlabel("layer")
        ax.set_ylabel("balanced accuracy")
        ax.set_ylim(0, 1)
        ax.legend(fontsize=6.5, loc="lower right", ncol=2)

    fig.suptitle(f"Act 1 text ablations: within-demonstrated (gap/knows), {C.MODEL_ID}",
                 fontsize=13)
    fig.tight_layout()
    FIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_PATH, dpi=150)
    print(f"[plot] wrote {FIG_PATH}")


# --------------------------------------------------------------------------------------

STEPS = {"extract": step_extract, "probe": step_probe, "plot": step_plot}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which == "all":
        step_extract(); step_probe(); step_plot()
    elif which in STEPS:
        STEPS[which]()
    else:
        print(f"unknown step {which!r}; choose from {list(STEPS)} or 'all'")
        sys.exit(1)
