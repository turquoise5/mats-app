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
GROUPS_PATH = C.CONTRAST / "ablation_groups.json"
ORIG_MERGED_RESULTS_PATH = C.RESULTS / "orig_merged_probe_vs_tfidf.json"

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
# orig_merged: probe vs. TF-IDF on the *identical* held-out rows, paired bootstrap CI
# --------------------------------------------------------------------------------------

def row_text(row: dict) -> str:
    """All turns joined -- matches run_ablations.py's `row_text` exactly (every turn in
    this dataset is role='user', so this equals act1_data.full_user_text)."""
    return " ".join(t["content"] for t in row["turns"])


def _paired_bootstrap_balanced_acc_diff(y_true, pred_a, pred_b, n_boot=10000, seed=0,
                                         alpha=0.05):
    """Bootstrap CI on balanced_accuracy(a) - balanced_accuracy(b), resampling the fixed
    test rows (paired: each bootstrap draw applies to both models' predictions and the
    same true labels). Vectorised over (n_boot, n) rather than looping sklearn calls.
    Bootstrap draws that lose one class entirely (undefined balanced accuracy) are
    dropped."""
    y_true = np.asarray(y_true)
    pred_a = np.asarray(pred_a)
    pred_b = np.asarray(pred_b)
    n = len(y_true)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))

    yb = y_true[idx]
    mask1, mask0 = (yb == 1), (yb == 0)
    n1, n0 = mask1.sum(1), mask0.sum(1)
    valid = (n1 > 0) & (n0 > 0)

    def bal_acc(pred):
        pb = pred[idx]
        correct = (pb == yb)
        recall1 = np.divide((correct & mask1).sum(1), n1, out=np.full(n_boot, np.nan), where=n1 > 0)
        recall0 = np.divide((correct & mask0).sum(1), n0, out=np.full(n_boot, np.nan), where=n0 > 0)
        return 0.5 * (recall1 + recall0)

    diffs = (bal_acc(pred_a) - bal_acc(pred_b))[valid]
    lo, hi = np.percentile(diffs, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {
        "n_boot_valid": int(valid.sum()), "n_boot_requested": n_boot, "seed": seed,
        "mean_diff": float(diffs.mean()), "ci_lo": float(lo), "ci_hi": float(hi),
        "ci_level": 1 - alpha,
        "p_diff_le_0": float(np.mean(diffs <= 0)),
    }


def step_orig_merged():
    """`orig` only, both positions. Split: merged (content-corrected) groups from
    `data/contrast/ablation_groups.json`, via the identical GroupShuffleSplit(test_size=
    0.3, seed=0) the CPU agent used -- reproduces its n_train=521/n_test=295 exactly.
    Probe: StandardScaler + LogisticRegression(class_weight='balanced'), balanced
    accuracy, all 37 layers. TF-IDF: refit on the *same* 295 test rows (bigrams,
    max_features=5000, class_weight='balanced') -- not the CPU agent's own split, so this
    is a like-for-like comparison, unlike the qid-grouped table above. Paired bootstrap CI
    on the probe-minus-TF-IDF balanced-accuracy difference, resampling the fixed test
    rows."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import balanced_accuracy_score
    from sklearn.model_selection import GroupShuffleSplit

    C.banner("ORIG ONLY, MERGED-GROUP SPLIT -- PROBE vs TF-IDF, PAIRED BOOTSTRAP")

    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    texts = np.array([row_text(r) for r in orig_rows])

    with open(GROUPS_PATH) as f:
        groups_json = json.load(f)
    group_of_row_id = groups_json["group_of_row_id"]
    merged_groups = np.array([group_of_row_id[r["id"]] for r in orig_rows])

    gss = GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=C.SEED)
    tr, te = next(gss.split(np.zeros(len(orig_rows)), labels, merged_groups))
    print(f"[orig_merged] split: n_train={len(tr)} n_test={len(te)} "
          f"(merged groups, GroupShuffleSplit test_size=0.3, seed={C.SEED})")
    if len(te) != 295:
        print(f"  *** WARNING: n_test={len(te)} != 295 -- split does not match the CPU "
              f"agent's reference run. Proceeding, but flag this. ***")
    majority = P.majority_baseline(labels, val_idx=te)
    print(f"[orig_merged] labels: gap={int((labels==0).sum())} knows={int((labels==1).sum())} "
          f"majority_baseline(test)={majority:.3f}")

    # TF-IDF, refit on the identical split -- position-independent (text only).
    vec = TfidfVectorizer(max_features=5000, ngram_range=(1, 2))
    Xtr = vec.fit_transform(texts[tr])
    Xte = vec.transform(texts[te])
    clf = LogisticRegression(max_iter=3000, class_weight="balanced", random_state=C.SEED)
    clf.fit(Xtr, labels[tr])
    tfidf_pred = clf.predict(Xte)
    tfidf_bal_acc = balanced_accuracy_score(labels[te], tfidf_pred)
    print(f"[orig_merged] TF-IDF (bigrams, balanced), refit on identical test rows: "
          f"balanced_acc={tfidf_bal_acc:.4f}")

    out = {
        "split": {"n_train": len(tr), "n_test": len(te), "grouped_by": "merged (ablation_groups.json)",
                  "method": "GroupShuffleSplit(n_splits=1, test_size=0.3, seed=0)",
                  "majority_baseline": majority},
        "tfidf": {"config": "TfidfVectorizer(max_features=5000, ngram_range=(1,2)) + "
                             "LogisticRegression(max_iter=3000, class_weight='balanced')",
                  "balanced_acc": float(tfidf_bal_acc)},
        "positions": {},
    }

    print(f"\n{'position':10s} {'probe_bal_acc':>13s} {'@layer':>7s} {'tfidf_bal_acc':>13s} "
          f"{'diff':>7s} {'95% CI':>18s} {'p(diff<=0)':>11s}")
    for position in C.READ_POSITIONS:
        acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_{position}.npy")
        print(f"  [orig_merged] position={position} acts={acts.shape}")
        res = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
        best_layer = res["best_layer"]

        # Refit the best-layer probe alone to get its per-row test predictions (the
        # 37-layer sweep above only kept accuracies, not per-layer prediction arrays).
        X = acts[:, best_layer, :]
        probe = _balanced_probe(C.SEED)
        probe.fit(X[tr], labels[tr])
        probe_pred = probe.predict(X[te])
        probe_bal_acc = balanced_accuracy_score(labels[te], probe_pred)
        assert abs(probe_bal_acc - res["best_acc"]) < 1e-9, "best-layer refit mismatch"

        boot = _paired_bootstrap_balanced_acc_diff(labels[te], probe_pred, tfidf_pred,
                                                     n_boot=10000, seed=C.SEED)

        out["positions"][position] = {
            "val_acc": res["val_acc"].tolist(), "best_layer": best_layer,
            "probe_bal_acc": float(probe_bal_acc), "bootstrap": boot,
        }
        print(f"  {position:10s} {probe_bal_acc:13.4f} {best_layer:7d} {tfidf_bal_acc:13.4f} "
              f"{probe_bal_acc - tfidf_bal_acc:+7.4f} "
              f"[{boot['ci_lo']:+.4f}, {boot['ci_hi']:+.4f}] {boot['p_diff_le_0']:11.4f}")

        C.log_run(
            act="1", experiment=f"orig_merged_probe_vs_tfidf/{position}",
            config={"split": out["split"], "tfidf_config": out["tfidf"]["config"],
                    "n_boot": boot["n_boot_valid"], "seed": C.SEED},
            metrics={"probe_bal_acc": float(probe_bal_acc), "best_layer": best_layer,
                     "tfidf_bal_acc": float(tfidf_bal_acc),
                     "diff": float(probe_bal_acc - tfidf_bal_acc),
                     "ci_lo": boot["ci_lo"], "ci_hi": boot["ci_hi"],
                     "p_diff_le_0": boot["p_diff_le_0"], "majority_baseline": majority},
        )

    with open(ORIG_MERGED_RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n[orig_merged] wrote {ORIG_MERGED_RESULTS_PATH}")
    return out


# --------------------------------------------------------------------------------------
# ablation_merged: B and CTRL refit on the merged (non-leaky) split, McNemar not deltas
# --------------------------------------------------------------------------------------

def _mcnemar(correct_a, correct_b):
    """Exact McNemar (binomial test on discordant pairs) + classic chi2-with-continuity-
    correction, on the same paired test items. `correct_a`/`correct_b` are item-aligned
    boolean arrays (True = that model got that item right)."""
    from scipy.stats import binomtest, chi2

    correct_a = np.asarray(correct_a, dtype=bool)
    correct_b = np.asarray(correct_b, dtype=bool)
    both_right = int((correct_a & correct_b).sum())
    both_wrong = int((~correct_a & ~correct_b).sum())
    a_right_b_wrong = int((correct_a & ~correct_b).sum())
    a_wrong_b_right = int((~correct_a & correct_b).sum())
    b, c = a_right_b_wrong, a_wrong_b_right
    n_disc = b + c

    if n_disc:
        exact_p = float(binomtest(min(b, c), n_disc, 0.5, alternative="two-sided").pvalue)
        chi2_stat = (abs(b - c) - 1) ** 2 / n_disc
        chi2_p = float(chi2.sf(chi2_stat, df=1))
    else:
        exact_p, chi2_stat, chi2_p = 1.0, 0.0, 1.0

    return {
        "both_right": both_right, "both_wrong": both_wrong,
        "a_right_b_wrong": a_right_b_wrong, "a_wrong_b_right": a_wrong_b_right,
        "n_discordant": n_disc, "exact_p": exact_p,
        "chi2_stat": float(chi2_stat), "chi2_p": chi2_p,
    }


def step_ablation_merged():
    """B and CTRL (plus orig, for reference), refit on the merged (content-corrected)
    group split from `ablation_groups.json` -- the earlier `probe` step's Δ-vs-CTRL table
    (see notes.md) was built on a raw `eedi_question_id` split that src/grouping.py
    documents as leaky: content-colliding questions (e.g. 1158/552) can straddle
    train/test under that grouping. This reuses the same GroupShuffleSplit(test_size=0.3,
    seed=0) on merged groups as `orig_merged` (n_test=295), and replaces the earlier
    point-estimate Δ comparison with McNemar's test on the shared, paired test items --
    the right tool for "do these two classifiers disagree systematically on the same
    items", which a difference of accuracies cannot answer on its own."""
    from sklearn.metrics import balanced_accuracy_score
    from sklearn.model_selection import GroupShuffleSplit

    C.banner("B / CTRL REFIT ON MERGED GROUPS -- MCNEMAR ON SHARED TEST ITEMS")

    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])

    with open(GROUPS_PATH) as f:
        groups_json = json.load(f)
    group_of_row_id = groups_json["group_of_row_id"]
    merged_groups = np.array([group_of_row_id[r["id"]] for r in orig_rows])

    gss = GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=C.SEED)
    tr, te = next(gss.split(np.zeros(len(orig_rows)), labels, merged_groups))
    print(f"[ablation_merged] split: n_train={len(tr)} n_test={len(te)} "
          f"(merged groups, GroupShuffleSplit test_size=0.3, seed={C.SEED} -- same split "
          f"as orig_merged)")
    if len(te) != 295:
        print(f"  *** WARNING: n_test={len(te)} != 295 -- does not match the reference "
              f"run. Proceeding, but flag this. ***")
    majority = P.majority_baseline(labels, val_idx=te)
    print(f"[ablation_merged] labels: gap={int((labels==0).sum())} "
          f"knows={int((labels==1).sum())} majority_baseline(test)={majority:.3f}")

    variants = ["orig", "B", "CTRL"]
    out = {
        "split": {"n_train": len(tr), "n_test": len(te), "grouped_by": "merged (ablation_groups.json)",
                  "method": "GroupShuffleSplit(n_splits=1, test_size=0.3, seed=0)",
                  "majority_baseline": majority},
        "positions": {},
    }

    for position in C.READ_POSITIONS:
        print(f"\n[ablation_merged] === position={position} ===")
        preds, bal_accs, layers = {}, {}, {}
        for v in variants:
            acts = np.load(C.CACHE / f"{CACHE_PREFIX}_{v}_{position}.npy")
            res = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
            layer = res["best_layer"]
            X = acts[:, layer, :]
            probe = _balanced_probe(C.SEED)
            probe.fit(X[tr], labels[tr])
            pred = probe.predict(X[te])
            bal_acc = balanced_accuracy_score(labels[te], pred)
            assert abs(bal_acc - res["best_acc"]) < 1e-9, "best-layer refit mismatch"
            preds[v], bal_accs[v], layers[v] = pred, float(bal_acc), layer
            print(f"  {v:5s} acts={acts.shape} balanced_acc={bal_acc:.4f} @L{layer}")

        correct = {v: (preds[v] == labels[te]) for v in variants}
        pairs = [("B", "CTRL"), ("B", "orig"), ("CTRL", "orig")]
        mcnemar_results = {}
        print(f"\n  {'pair':14s} {'both_right':>10s} {'both_wrong':>10s} {'a_only':>7s} "
              f"{'b_only':>7s} {'exact_p':>9s} {'chi2_p':>9s}")
        for a, b in pairs:
            res_mc = _mcnemar(correct[a], correct[b])
            mcnemar_results[f"{a}_vs_{b}"] = res_mc
            print(f"  {a + '_vs_' + b:14s} {res_mc['both_right']:10d} {res_mc['both_wrong']:10d} "
                  f"{res_mc['a_right_b_wrong']:7d} {res_mc['a_wrong_b_right']:7d} "
                  f"{res_mc['exact_p']:9.4f} {res_mc['chi2_p']:9.4f}")

        out["positions"][position] = {
            "balanced_acc": bal_accs, "best_layer": layers, "mcnemar": mcnemar_results,
        }

        C.log_run(
            act="1", experiment=f"ablation_merged_mcnemar/{position}",
            config={"split": out["split"], "seed": C.SEED, "variants": variants},
            metrics={"balanced_acc": bal_accs, "best_layer": layers, "mcnemar": mcnemar_results},
        )

    path = C.RESULTS / "ablation_merged_mcnemar.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n[ablation_merged] wrote {path}")
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

STEPS = {"extract": step_extract, "probe": step_probe, "plot": step_plot,
         "orig_merged": step_orig_merged, "ablation_merged": step_ablation_merged}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which == "all":
        step_extract(); step_probe(); step_plot()
    elif which in STEPS:
        STEPS[which]()
    else:
        print(f"unknown step {which!r}; choose from {list(STEPS)} or 'all'")
        sys.exit(1)
