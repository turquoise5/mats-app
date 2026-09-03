#!/usr/bin/env python
"""Does steering change the CORRECTION RATE (probe vs random vs orig), quantitatively?

Follow-up to the 27-item qualitative pilot (notes.md "Correction items" section), which
found: (a) most items truncate before a verdict at 200 tokens, (b) steered generations
sometimes hallucinate what the user wrote rather than affirm it -- a failure mode a
naive "corrects vs affirms" binary would miss. This experiment fixes both: a cheap,
validated log-prob proxy (Act 2 Task 2.1's design) instead of full generation for the
main grid, and an explicit "confused/misattribution" label in the validation pass.

Item set: all 109 gap-labelled demonstrated rows in the held-out merged-split test set
-- real items, already in "user asserts a wrong step, asks for confirmation" shape.

    python run_correction_rate.py calibrate   # GPU -- unsteered generations, all 109 items
    python run_correction_rate.py validate_gen  # GPU -- steered generations, 25-item subsample
    # <- read results/correction_validate_samples.txt, fill in
    #    results/correction_validation_labels.json by hand ->
    python run_correction_rate.py proxy_grid  # GPU -- single-forward-pass proxy, full grid
    python run_correction_rate.py analyze     # CPU -- rates, McNemar, bootstrap CI
    python run_correction_rate.py plot        # CPU -- dose-response figure
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"

import json
import sys
from collections import Counter

import numpy as np

from run_ablation_probes import CACHE_PREFIX, _balanced_probe, _mcnemar, load_variant_rows, merged_split
from src import config as C

RESULTS = C.RESULTS
FIGS = C.FIGS

CALIB_PATH = RESULTS / "correction_calibration.json"
VALIDATE_SAMPLES_PATH = RESULTS / "correction_validate_samples.txt"
VALIDATE_GEN_PATH = RESULTS / "correction_validate_generations.json"
LABELS_PATH = RESULTS / "correction_validation_labels.json"
PROXY_PATH = RESULTS / "correction_proxy_grid.json"
ANALYSIS_PATH = RESULTS / "correction_rate_analysis.json"
FIG_PATH = FIGS / "correction_rate_doseresponse.png"

MAIN_ALPHA_FRACS = [-0.25, -0.15, 0.0, 0.15, 0.25]
MAIN_VECTORS = ["probe", "random"]
DOM_BONUS_FRACS = [-0.25, 0.25]  # dom run only at these two, as a secondary arm

N_VALIDATE = 25
VALIDATE_SEED = 0
VALIDATE_CONDITIONS = [("probe", -0.25), ("random", -0.25)]  # + orig, reused from calibrate

MAX_NEW_TOKENS = 260  # the 27-item pilot at 200 truncated most items before a verdict


# --------------------------------------------------------------------------------------
# shared setup
# --------------------------------------------------------------------------------------

def load_items():
    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)
    te_rows = [orig_rows[i] for i in te]
    te_labels = labels[te]
    gap_rows = [r for r, y in zip(te_rows, te_labels) if y == 0]
    return orig_rows, labels, tr, te, gap_rows


def fit_direction(orig_rows, labels, tr, te):
    from run_ablation_probes import fit_layer_probes_balanced
    from src import steering as S

    acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_natural.npy")
    d0_sweep = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
    layer = d0_sweep["best_layer"]
    mean_norm = float(np.linalg.norm(acts[:, layer, :], axis=1).mean())
    X = acts[:, layer, :]
    frozen_probe = _balanced_probe(C.SEED)
    frozen_probe.fit(X[tr], labels[tr])
    probe_vec = S.probe_direction(frozen_probe)
    dom_vec = S.diff_of_means_direction(X[tr], labels[tr])
    rand_vec = S.random_direction(X.shape[1], seed=C.SEED)
    return layer, mean_norm, {"probe": probe_vec, "random": rand_vec, "dom": dom_vec}


# --------------------------------------------------------------------------------------
# calibrate: unsteered generations for all 109 items -- becomes both the alpha=0
# condition AND the source for mining opener tokens
# --------------------------------------------------------------------------------------

def step_calibrate(batch_size: int = 8):
    import torch  # noqa: F401

    from src import model as M

    C.banner("CALIBRATE -- UNSTEERED GENERATIONS, ALL 109 GAP ITEMS")
    orig_rows, labels, tr, te, gap_rows = load_items()
    print(f"[calibrate] {len(gap_rows)} gap items")

    mdl, tok = M.load()
    M.assert_template_sane(tok)

    texts = [M.render_chat(tok, it["turns"]) for it in gap_rows]
    gens = M.greedy_generate(mdl, tok, texts, max_new_tokens=MAX_NEW_TOKENS, batch_size=batch_size)

    out = {it["id"]: g for it, g in zip(gap_rows, gens)}
    with open(CALIB_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"[calibrate] wrote {CALIB_PATH}")

    # first-token frequency table, for manually building AFFIRM_TOKENS / CORRECT_TOKENS
    first_words = [g.strip().split()[0] if g.strip() else "" for g in gens]
    counts = Counter(first_words)
    C.banner("FIRST-WORD FREQUENCY (paste into CORRECTION_TOKENS / AFFIRM_TOKENS below)")
    for w, n in counts.most_common(30):
        print(f"  {n:3d}  {w!r}")

    C.log_run(act="2", experiment="correction_calibrate",
               config={"n_items": len(gap_rows), "max_new_tokens": MAX_NEW_TOKENS},
               metrics={"first_word_counts": dict(counts)})


# --------------------------------------------------------------------------------------
# validate_gen: steered generations for a 25-item subsample, 2 conditions (+ orig reused
# from calibrate) -- for hand-labelling into {corrects, hedges, affirms, confused}
# --------------------------------------------------------------------------------------

def step_validate_gen(batch_size: int = 8):
    import torch  # noqa: F401

    from src import model as M
    from src import steering as S

    C.banner("VALIDATE_GEN -- STEERED GENERATIONS, 25-ITEM SUBSAMPLE")
    orig_rows, labels, tr, te, gap_rows = load_items()
    rng = np.random.default_rng(VALIDATE_SEED)
    sub_idx = rng.choice(len(gap_rows), size=min(N_VALIDATE, len(gap_rows)), replace=False)
    sub_items = [gap_rows[i] for i in sorted(sub_idx)]
    print(f"[validate_gen] {len(sub_items)} items: {[it['id'] for it in sub_items]}")

    if not CALIB_PATH.exists():
        raise RuntimeError("Run `calibrate` first -- orig/alpha=0 text for these items is reused from there.")
    with open(CALIB_PATH) as f:
        calib = json.load(f)

    layer, mean_norm, vectors = fit_direction(orig_rows, labels, tr, te)
    print(f"[validate_gen] layer={layer} mean_norm={mean_norm:.2f}")

    mdl, tok = M.load()
    M.assert_template_sane(tok)
    texts = [M.render_chat(tok, it["turns"]) for it in sub_items]

    out = {it["id"]: {"orig": calib[it["id"]]} for it in sub_items}
    for vname, frac in VALIDATE_CONDITIONS:
        alpha = frac * mean_norm
        h = S.register_steering(mdl, layer, vectors[vname], alpha=alpha)
        try:
            gens = M.greedy_generate(mdl, tok, texts, max_new_tokens=MAX_NEW_TOKENS, batch_size=batch_size)
        finally:
            h.remove()
        for it, g in zip(sub_items, gens):
            out[it["id"]][f"{vname}@{frac:+.2f}"] = g
        print(f"[validate_gen] done: {vname}@{frac:+.2f}")

    with open(VALIDATE_GEN_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    with open(VALIDATE_SAMPLES_PATH, "w", encoding="utf-8") as f:
        for it in sub_items:
            f.write(f"=========== {it['id']}  ({it['eedi_misconception']}) ===========\n")
            f.write(f"USER: {it['turns'][-1]['content']}\n\n")
            for cond, text in out[it["id"]].items():
                f.write(f"--- {cond} ---\n{text}\n\n")
            f.write("\n")
    print(f"[validate_gen] wrote {VALIDATE_GEN_PATH} and {VALIDATE_SAMPLES_PATH}")
    print(f"\n[validate_gen] NEXT STEP (manual): read {VALIDATE_SAMPLES_PATH}, label every "
          f"(item, condition) pair into results/correction_validation_labels.json as "
          f"{{item_id: {{condition: label}}}}, label in "
          f"{{'corrects','hedges','affirms','confused'}}.")


# --------------------------------------------------------------------------------------
# proxy: canonical-continuation log-prob, NOT first-token. Calibration showed 75/109
# unsteered responses open with the neutral "Let's..." regardless of outcome -- a
# first-token classifier (the naive Act 2 Task 2.1 sketch) has no signal here. Instead,
# teacher-force a small set of short correcting/affirming continuations right after the
# real read position and compare their mean per-token log-prob -- this measures what the
# model would say *if* it opened decisively, marginalising over whatever lead-in style
# it would naturally use.
# --------------------------------------------------------------------------------------

CORRECT_CONTINUATIONS = [
    "That's not quite right.",
    "This is not correct.",
    "Actually, this is incorrect.",
    "There's a mistake here.",
]
AFFIRM_CONTINUATIONS = [
    "Yes, that's correct.",
    "You're right.",
    "This is correct.",
    "Great, that's right.",
]


def continuation_logprob(model, tok, prompts, continuation, device, batch_size: int = 8):
    """Teacher-forced mean per-token log-prob of `continuation` right after each prompt.
    Tokenises prompt and continuation separately and concatenates ids -- avoids BPE
    retokenising differently across the boundary than a naive string-concat would.
    Chunks into `batch_size`-row batches: the lm_head computes logits for every position
    of the whole batch (no `logits_to_keep` slicing here, to stay simple), and doing that
    for all 109 rows x ~152k vocab at once OOMs even on this GPU. Only the tiny per-row
    slice of logits actually needed is log-softmaxed, not the full (B,T,V) tensor.
    Returns np.array shape (len(prompts),)."""
    import torch

    cont_ids = tok(continuation, add_special_tokens=False)["input_ids"]
    n_cont = len(cont_ids)
    pad_id = tok.pad_token_id
    scores = []

    for start in range(0, len(prompts), batch_size):
        chunk = prompts[start : start + batch_size]
        prompt_ids_list = [tok(p, add_special_tokens=False)["input_ids"] for p in chunk]
        full_ids_list = [pids + cont_ids for pids in prompt_ids_list]
        maxlen = max(len(x) for x in full_ids_list)

        input_ids = torch.full((len(full_ids_list), maxlen), pad_id, dtype=torch.long)
        attn = torch.zeros((len(full_ids_list), maxlen), dtype=torch.long)
        for i, ids in enumerate(full_ids_list):
            input_ids[i, : len(ids)] = torch.tensor(ids)
            attn[i, : len(ids)] = 1
        input_ids, attn = input_ids.to(device), attn.to(device)

        with torch.no_grad():
            out = model(input_ids=input_ids, attention_mask=attn)

        for i, pids in enumerate(prompt_ids_list):
            plen = len(pids)
            row_logits = out.logits[i, plen - 1 : plen - 1 + n_cont, :].float()
            row_logprobs = torch.log_softmax(row_logits, dim=-1)
            tgt = torch.tensor(full_ids_list[i][plen : plen + n_cont], device=row_logprobs.device)
            row_score = row_logprobs.gather(1, tgt.unsqueeze(1)).sum().item()
            scores.append(row_score / n_cont)
        del out
        torch.cuda.empty_cache()

    return np.array(scores)


def step_proxy_grid(batch_size: int = None):
    """Full grid: probe/random at MAIN_ALPHA_FRACS (alpha=0 shared, vector-independent),
    dom at DOM_BONUS_FRACS. Single forward pass per candidate continuation per condition
    (all 109 items batched together) -- no generation loop."""
    import torch  # noqa: F401

    from src import model as M
    from src import steering as S

    C.banner("PROXY_GRID -- CANONICAL-CONTINUATION LOG-PROB, FULL GRID")
    orig_rows, labels, tr, te, gap_rows = load_items()
    layer, mean_norm, vectors = fit_direction(orig_rows, labels, tr, te)
    print(f"[proxy_grid] layer={layer} mean_norm={mean_norm:.2f} n_items={len(gap_rows)}")

    mdl, tok = M.load()
    M.assert_template_sane(tok)
    device = next(mdl.parameters()).device
    prompts = [M.render_chat(tok, it["turns"]) for it in gap_rows]
    item_ids = [it["id"] for it in gap_rows]

    conditions = [("probe", 0.0)]  # alpha=0, vector label arbitrary (v*0==0)
    for v in MAIN_VECTORS:
        for frac in MAIN_ALPHA_FRACS:
            if frac == 0.0:
                continue
            conditions.append((v, frac))
    for frac in DOM_BONUS_FRACS:
        conditions.append(("dom", frac))

    out = {"layer": layer, "mean_layer_norm": mean_norm, "item_ids": item_ids,
           "correct_continuations": CORRECT_CONTINUATIONS, "affirm_continuations": AFFIRM_CONTINUATIONS,
           "conditions": {}}

    for vname, frac in conditions:
        alpha = frac * mean_norm
        key = f"{vname}@{frac:+.2f}"
        h = S.register_steering(mdl, layer, vectors[vname], alpha=alpha) if frac != 0.0 else None
        try:
            corr_scores = np.stack([continuation_logprob(mdl, tok, prompts, c, device)
                                     for c in CORRECT_CONTINUATIONS])  # (4, n_items)
            aff_scores = np.stack([continuation_logprob(mdl, tok, prompts, c, device)
                                    for c in AFFIRM_CONTINUATIONS])
        finally:
            if h is not None:
                h.remove()
        corr_mean = corr_scores.mean(axis=0)
        aff_mean = aff_scores.mean(axis=0)
        score = corr_mean - aff_mean  # >0 leans correction, <0 leans affirmation
        out["conditions"][key] = {
            "vector": vname, "alpha_frac": frac, "alpha": alpha,
            "score": score.tolist(), "corr_logprob": corr_mean.tolist(), "aff_logprob": aff_mean.tolist(),
        }
        print(f"  [{key:14s}] mean score={score.mean():+.3f}  "
              f"frac>0 (leans correct)={float((score > 0).mean()):.3f}")
        C.log_run(act="2", experiment="correction_proxy_grid",
                   config={"vector": vname, "alpha_frac": frac, "alpha": alpha, "layer": layer,
                           "n_items": len(item_ids), "seed": C.SEED},
                   metrics={"mean_score": float(score.mean()), "frac_leans_correct": float((score > 0).mean())})

    with open(PROXY_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n[proxy_grid] wrote {PROXY_PATH}")
    return out


# --------------------------------------------------------------------------------------
# analyze: validate proxy against hand labels, then compute rates / McNemar / bootstrap
# --------------------------------------------------------------------------------------

def _paired_bootstrap_rate_diff(a_bool, b_bool, n_boot=10000, seed=0, alpha=0.05):
    a_bool, b_bool = np.asarray(a_bool), np.asarray(b_bool)
    n = len(a_bool)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    diffs = a_bool[idx].mean(axis=1) - b_bool[idx].mean(axis=1)
    lo, hi = np.percentile(diffs, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {"mean_diff": float(diffs.mean()), "ci_lo": float(lo), "ci_hi": float(hi),
            "p_diff_le_0": float(np.mean(diffs <= 0))}


def step_analyze(threshold: float = 0.0):
    C.banner("ANALYZE -- PROXY VALIDATION + CORRECTION-RATE DOSE-RESPONSE")

    with open(PROXY_PATH) as f:
        proxy = json.load(f)
    item_ids = proxy["item_ids"]

    out = {"threshold": threshold, "validation": None, "conditions": {}}

    # -- validate proxy against hand labels, if present ---------------------------------
    if LABELS_PATH.exists():
        with open(LABELS_PATH) as f:
            labels_map = json.load(f)
        with open(VALIDATE_GEN_PATH) as f:
            val_gens = json.load(f)
        y_true, y_proxy = [], []
        confused_count, total_labelled = 0, 0
        for item_id, conds in labels_map.items():
            for cond, lab in conds.items():
                total_labelled += 1
                if lab == "confused":
                    confused_count += 1
                    continue
                if lab not in ("corrects", "affirms"):
                    continue  # hedges excluded from the binary check, same as a real judge protocol
                y_true.append(1 if lab == "corrects" else 0)
                if cond == "orig":
                    key = "probe@+0.00"
                else:
                    key = cond  # already "probe@-0.25" / "random@-0.25"
                idx = item_ids.index(item_id)
                score = proxy["conditions"][key]["score"][idx]
                y_proxy.append(1 if score > threshold else 0)
        y_true, y_proxy = np.array(y_true), np.array(y_proxy)
        agree = float((y_true == y_proxy).mean()) if len(y_true) else None
        confused_rate = confused_count / total_labelled if total_labelled else None
        out["validation"] = {
            "n_labelled_pairs": total_labelled, "n_binary_pairs": len(y_true),
            "proxy_vs_label_agreement": agree, "confused_rate": confused_rate,
        }
        print(f"[analyze] validation: {total_labelled} labelled pairs, "
              f"{len(y_true)} binary (corrects/affirms), agreement={agree}, "
              f"confused_rate={confused_rate}")
        if agree is not None and agree < 0.7:
            print("  *** proxy agreement < 0.7 -- treat the dose-response below as "
                  "provisional, not validated. ***")
    else:
        print(f"[analyze] {LABELS_PATH} not found -- skipping validation, "
              f"reporting the proxy dose-response unvalidated.")

    # -- correction rate per condition, McNemar and bootstrap vs orig and vs random -----
    orig_key = "probe@+0.00"
    orig_score = np.array(proxy["conditions"][orig_key]["score"])
    orig_correct = orig_score > threshold

    print(f"\n[analyze] orig correction rate (proxy): {orig_correct.mean():.3f}")

    for key, cond in proxy["conditions"].items():
        if key == orig_key:
            continue
        score = np.array(cond["score"])
        correct = score > threshold
        rate = float(correct.mean())

        mc_vs_orig = _mcnemar(correct, orig_correct)
        boot_vs_orig = _paired_bootstrap_rate_diff(correct, orig_correct, seed=C.SEED)

        entry = {
            "vector": cond["vector"], "alpha_frac": cond["alpha_frac"], "alpha": cond["alpha"],
            "correction_rate": rate,
            "mcnemar_vs_orig": mc_vs_orig, "bootstrap_vs_orig": boot_vs_orig,
        }
        out["conditions"][key] = entry
        print(f"  [{key:14s}] rate={rate:.3f}  vs orig: diff={boot_vs_orig['mean_diff']:+.3f} "
              f"CI=[{boot_vs_orig['ci_lo']:+.3f},{boot_vs_orig['ci_hi']:+.3f}] "
              f"McNemar p={mc_vs_orig['exact_p']:.4f}")

    # probe vs random at matched alpha
    print("\n[analyze] probe vs random at matched alpha:")
    for frac in MAIN_ALPHA_FRACS:
        if frac == 0.0:
            continue
        pk, rk = f"probe@{frac:+.2f}", f"random@{frac:+.2f}"
        if pk not in proxy["conditions"] or rk not in proxy["conditions"]:
            continue
        p_correct = np.array(proxy["conditions"][pk]["score"]) > threshold
        r_correct = np.array(proxy["conditions"][rk]["score"]) > threshold
        mc = _mcnemar(p_correct, r_correct)
        boot = _paired_bootstrap_rate_diff(p_correct, r_correct, seed=C.SEED)
        out["conditions"][f"probe_vs_random@{frac:+.2f}"] = {
            "alpha_frac": frac, "probe_rate": float(p_correct.mean()), "random_rate": float(r_correct.mean()),
            "mcnemar": mc, "bootstrap": boot,
        }
        print(f"  alpha={frac:+.2f}  probe={p_correct.mean():.3f}  random={r_correct.mean():.3f}  "
              f"diff={boot['mean_diff']:+.3f} CI=[{boot['ci_lo']:+.3f},{boot['ci_hi']:+.3f}] "
              f"McNemar p={mc['exact_p']:.4f}")
        C.log_run(act="2", experiment=f"correction_rate_probe_vs_random/{frac:+.2f}",
                   config={"alpha_frac": frac, "threshold": threshold, "seed": C.SEED},
                   metrics={"probe_rate": float(p_correct.mean()), "random_rate": float(r_correct.mean()),
                            "diff": boot["mean_diff"], "ci_lo": boot["ci_lo"], "ci_hi": boot["ci_hi"],
                            "mcnemar_p": mc["exact_p"]})

    with open(ANALYSIS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n[analyze] wrote {ANALYSIS_PATH}")
    return out


def step_plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    C.banner("PLOT -- CORRECTION-RATE DOSE-RESPONSE")
    with open(ANALYSIS_PATH) as f:
        out = json.load(f)
    with open(PROXY_PATH) as f:
        proxy = json.load(f)
    threshold = out["threshold"]
    orig_rate = float((np.array(proxy["conditions"]["probe@+0.00"]["score"]) > threshold).mean())

    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    for vname, color in (("probe", "tab:blue"), ("random", "tab:orange")):
        fracs, rates = [0.0], [orig_rate]
        for frac in MAIN_ALPHA_FRACS:
            if frac == 0.0:
                continue
            key = f"{vname}@{frac:+.2f}"
            if key in out["conditions"]:
                fracs.append(frac)
                rates.append(out["conditions"][key]["correction_rate"])
        order = np.argsort(fracs)
        fracs_sorted = np.array(fracs)[order]
        rates_sorted = np.array(rates)[order]
        ax.plot(fracs_sorted, rates_sorted, marker="o", color=color, label=vname)

    for frac in DOM_BONUS_FRACS:
        key = f"dom@{frac:+.2f}"
        if key in out["conditions"]:
            ax.scatter([frac], [out["conditions"][key]["correction_rate"]], marker="s",
                       color="tab:green", s=60, label="dom" if frac == DOM_BONUS_FRACS[0] else None)

    ax.set_xlabel("alpha (x mean layer-norm)")
    ax.set_ylabel("proxy-estimated correction rate")
    ax.set_ylim(-0.02, 1.02)
    ax.axhline(0.5, color="k", ls=":", lw=1, alpha=0.4)
    ax.set_title("Correction rate under steering (probe vs random vs orig)\n"
                 "proxy = teacher-forced canonical-continuation log-prob, n=109 items")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG_PATH, dpi=150)
    print(f"[plot] wrote {FIG_PATH}")


STEPS = {"calibrate": step_calibrate, "validate_gen": step_validate_gen,
         "proxy_grid": step_proxy_grid, "analyze": step_analyze, "plot": step_plot}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else None
    if which in STEPS:
        STEPS[which]()
    else:
        print(f"unknown step {which!r}; choose from {list(STEPS)}")
        sys.exit(1)
