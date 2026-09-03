#!/usr/bin/env python
"""Minimal first causal check (act2_causal.md Task 2.2, a slice of 2.3/2.4): does
steering the residual stream along the D0 (persist experiment's) probe direction, on
D0's own conversations, causally move a behavioural readout? Not the full Act 2
protocol (no omission proxy, no MMLU/perplexity sanity suite, no LLM judge) -- a direct,
minimal dose-response with the one control Act 2 calls essential (a norm-matched random
direction), plus a hook-fires sanity check.

    python run_steering.py steer_d0   # GPU -- verify hook, dose-response, controls, plot

Direction: the same merged-split D0 probe used throughout the `persist` experiments
(StandardScaler + LogisticRegression(class_weight='balanced'), layer = D0's own best
layer on that split, natural position -- L20, per persist_results.json). Read position
for the behavioural readout: the Act 1 "just ask" template (run_act1.JUST_ASK_TEMPLATE),
appended after the row's real turns and generated under the hook -- reuses an already-
validated readout (0/2112 unparseable in Act 1) instead of building a new proxy.
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"

import json
import sys

import numpy as np

from run_ablation_probes import CACHE_PREFIX, _balanced_probe, load_variant_rows, merged_split
from run_act1 import JUST_ASK_TEMPLATE, _parse_yes_no
from src import config as C

RESULTS_PATH = C.RESULTS / "steering_d0_results.json"
SAMPLES_PATH = C.RESULTS / "steering_d0_samples.txt"
FIG_PATH = C.FIGS / "steering_d0_doseresponse.png"

# x mean layer-norm. A first coarse pass at [-2,-1,0,1,2] showed: +-1 already saturates
# the just-ask readout (P(yes)=0/1, i.e. flips every row) while staying fluent
# (`steering_d0_samples.txt`: "No. The user might have made a mistake..." -- coherent,
# on-topic); +-2 is outright gibberish (garbage tokens, matches the explicit 20x-mean-
# norm degeneracy check). So the informative transition lives inside [-1, 1], densely
# sampled here; +-2 stays in the grid only to show where fluency actually breaks.
ALPHA_FRACS = [-2.0, -1.0, -0.5, -0.25, -0.1, 0.0, 0.1, 0.25, 0.5, 1.0, 2.0]
# x mean layer-norm, run for both random-direction and diff-of-means. NOT 1.0: the first
# pass at frac=1.0 showed random_direction ALSO saturates to P(yes)=1.0/1.0 there (0/295
# ambiguous) -- exactly the RMU failure mode act2_causal.md Task 2.4 warns about, a big-
# enough perturbation breaking the readout regardless of direction. These two points sit
# where the probe direction shows a real, non-saturated, label-dependent effect instead
# (knows/gap still separated at both), so a random/diff-of-means direction saturating
# there too would be a real specificity failure, not a magnitude artifact.
CONTROL_FRACS = [0.1, 0.2]


def step_steer_d0(batch_size: int = 8, max_new_tokens: int = 12):
    import torch

    from src import model as M
    from src import steering as S

    C.banner("STEER_D0 -- STEERING THE D0 DIRECTION ON D0'S OWN CONVERSATIONS")

    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)
    print(f"[steer_d0] split: n_train={len(tr)} n_test={len(te)} (merged groups, same as "
          f"orig_merged / ablation_merged / persist)")

    acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_natural.npy")

    from run_ablation_probes import fit_layer_probes_balanced
    d0_sweep = fit_layer_probes_balanced(acts, labels, tr, te, seed=C.SEED)
    layer = d0_sweep["best_layer"]
    print(f"[steer_d0] D0 best layer (natural) = L{layer}, balanced_acc={d0_sweep['best_acc']:.4f} "
          f"-- matches persist_results.json")

    X = acts[:, layer, :]
    frozen_probe = _balanced_probe(C.SEED)
    frozen_probe.fit(X[tr], labels[tr])

    probe_vec = S.probe_direction(frozen_probe)
    dom_vec = S.diff_of_means_direction(X[tr], labels[tr])
    rand_vec = S.random_direction(X.shape[1], seed=C.SEED)
    print(f"[steer_d0] probe_vec norm={probe_vec.norm():.3f}  dom_vec norm={dom_vec.norm():.3f}  "
          f"cos(probe, dom)={torch.nn.functional.cosine_similarity(probe_vec, dom_vec, dim=0):.4f}")

    layer_norms = np.linalg.norm(X, axis=1)
    mean_norm = float(layer_norms.mean())
    print(f"[steer_d0] layer {layer} residual-stream norm: mean={mean_norm:.2f} "
          f"std={layer_norms.std():.2f} -- alpha calibrated as (fraction) x this mean")

    mdl, tok = M.load()
    M.assert_template_sane(tok)

    te_rows = [orig_rows[i] for i in te]
    te_labels = labels[te]
    prompt_turns = [
        r["turns"] + [{"role": "user", "content": JUST_ASK_TEMPLATE.format(concept=r["concept"])}]
        for r in te_rows
    ]
    texts = [M.render_chat(tok, t) for t in prompt_turns]
    print(f"[steer_d0] {len(texts)} just-ask prompts (test split only, held out from the "
          f"probe's training fold)")

    # -- verify the hook fires (act2_causal.md Task 2.2, mandatory) --------------------
    C.banner("VERIFY HOOK")
    sample_texts = texts[:5]
    unhooked = M.greedy_generate(mdl, tok, sample_texts, max_new_tokens=max_new_tokens,
                                  batch_size=len(sample_texts))
    h = S.register_steering(mdl, layer, probe_vec, alpha=0.0)
    try:
        zero_alpha = M.greedy_generate(mdl, tok, sample_texts, max_new_tokens=max_new_tokens,
                                        batch_size=len(sample_texts))
    finally:
        h.remove()
    identical = unhooked == zero_alpha
    print(f"[verify] alpha=0 bit-identical to unhooked: {identical}")
    if not identical:
        print("  *** alpha=0 did not reproduce the unhooked output -- inspect before "
              "trusting any alpha!=0 result below. ***")

    huge_alpha = 20.0 * mean_norm
    h = S.register_steering(mdl, layer, probe_vec, alpha=huge_alpha)
    try:
        degenerate = M.greedy_generate(mdl, tok, sample_texts, max_new_tokens=max_new_tokens,
                                        batch_size=len(sample_texts))
    finally:
        h.remove()
    print(f"[verify] alpha={huge_alpha:.0f} (20x mean norm) sample outputs (expect garbage):")
    for g in degenerate:
        print(f"    {g!r}")
    hook_verified = identical

    # -- main dose-response, probe direction --------------------------------------------
    C.banner("DOSE-RESPONSE -- PROBE DIRECTION")
    out = {
        "layer": layer, "mean_layer_norm": mean_norm, "n_test": len(te),
        "alpha_fracs": ALPHA_FRACS, "hook_verified_alpha0_identical": bool(identical),
        "huge_alpha_sample_outputs": degenerate,
        "probe_vs_dom_cosine": float(torch.nn.functional.cosine_similarity(probe_vec, dom_vec, dim=0)),
        "sweep": {}, "controls": {},
    }

    def run_condition(vec, alpha, tag):
        h = S.register_steering(mdl, layer, vec, alpha=alpha)
        try:
            gens = M.greedy_generate(mdl, tok, texts, max_new_tokens=max_new_tokens,
                                      batch_size=batch_size)
        finally:
            h.remove()
        parsed = [_parse_yes_no(g) for g in gens]
        n_amb = sum(1 for p in parsed if p is None)
        knows_yes = [p == "yes" for p, y in zip(parsed, te_labels) if p is not None and y == 1]
        gap_yes = [p == "yes" for p, y in zip(parsed, te_labels) if p is not None and y == 0]
        p_yes_knows = float(np.mean(knows_yes)) if knows_yes else None
        p_yes_gap = float(np.mean(gap_yes)) if gap_yes else None
        usable = (n_amb / len(parsed)) < 0.10  # < 10% unparseable -- fluency spot-check gate
        print(f"  [{tag}] alpha={alpha:+8.2f}  P(yes|knows)={p_yes_knows}  "
              f"P(yes|gap)={p_yes_gap}  n_ambiguous={n_amb}/{len(parsed)} "
              f"{'' if usable else '*** >10% unparseable -- likely outside usable range ***'}")
        return {
            "alpha": alpha, "p_yes_knows": p_yes_knows, "p_yes_gap": p_yes_gap,
            "n_ambiguous": n_amb, "n_total": len(parsed), "usable": usable,
        }, gens

    all_samples = {}
    for frac in ALPHA_FRACS:
        alpha = frac * mean_norm
        res, gens = run_condition(probe_vec, alpha, f"probe frac={frac:+.1f}")
        out["sweep"][f"{frac:+.1f}"] = res
        all_samples[f"probe_frac={frac:+.1f}"] = list(zip([r["id"] for r in te_rows], gens))[:6]
        C.log_run(
            act="2", experiment="steer_d0/probe_direction",
            config={"layer": layer, "alpha": alpha, "alpha_frac": frac, "mean_layer_norm": mean_norm,
                    "vector": "probe", "n_test": len(te), "seed": C.SEED},
            metrics=res,
        )

    # -- essential control: norm-matched random direction, at magnitudes where the probe
    # direction itself is still informative (not saturated) --------------------------
    C.banner("CONTROLS -- RANDOM DIRECTION AND DIFF-OF-MEANS, MATCHED TO PROBE'S NON-SATURATED RANGE")
    for frac in CONTROL_FRACS:
        alpha = frac * mean_norm
        for vec, name in ((rand_vec, "random_direction"), (dom_vec, "diff_of_means")):
            tag = f"{name}@{frac:+.1f}"
            res, gens = run_condition(vec, alpha, tag)
            out["controls"][tag] = res
            all_samples[tag] = list(zip([r["id"] for r in te_rows], gens))[:6]
            C.log_run(
                act="2", experiment=f"steer_d0/{name}",
                config={"layer": layer, "alpha": alpha, "alpha_frac": frac,
                        "mean_layer_norm": mean_norm, "vector": name, "n_test": len(te), "seed": C.SEED},
                metrics=res,
            )
        # reference: what does the probe-direction sweep say at this same frac?
        out["controls"][f"probe_direction@{frac:+.1f}"] = out["sweep"][f"{frac:+.1f}"]

    with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
        for tag, rows in all_samples.items():
            f.write(f"=== {tag} ===\n")
            for rid, gen in rows:
                f.write(f"  {rid}: {gen!r}\n")
            f.write("\n")
    print(f"\n[steer_d0] wrote {SAMPLES_PATH}")

    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"[steer_d0] wrote {RESULTS_PATH}")

    return out


def step_plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    C.banner("STEER_D0 -- PLOT")
    with open(RESULTS_PATH) as f:
        out = json.load(f)

    fracs = out["alpha_fracs"]
    rows = [out["sweep"][f"{fr:+.1f}"] for fr in fracs]
    p_knows = [r["p_yes_knows"] if r["p_yes_knows"] is not None else np.nan for r in rows]
    p_gap = [r["p_yes_gap"] if r["p_yes_gap"] is not None else np.nan for r in rows]
    unusable_fracs = [fr for fr, r in zip(fracs, rows) if not r.get("usable", True)]

    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    for fr in unusable_fracs:
        ax.axvspan(fr - 0.05, fr + 0.05, color="grey", alpha=0.15, zorder=0)
    ax.plot(fracs, p_knows, marker="o", label="knows-labelled rows")
    ax.plot(fracs, p_gap, marker="o", label="gap-labelled rows")

    for frac_str in {k.split("@")[1] for k in out["controls"] if "@" in k}:
        frac_val = float(frac_str)
        rand = out["controls"].get(f"random_direction@{frac_str}")
        dom = out["controls"].get(f"diff_of_means@{frac_str}")
        if rand and rand["p_yes_knows"] is not None:
            ax.scatter([frac_val], [rand["p_yes_knows"]], marker="x", color="tab:blue", s=70,
                       label=f"random dir, knows @ {frac_str}", zorder=5)
        if rand and rand["p_yes_gap"] is not None:
            ax.scatter([frac_val], [rand["p_yes_gap"]], marker="x", color="tab:orange", s=70,
                       label=f"random dir, gap @ {frac_str}", zorder=5)
        if dom and dom["p_yes_knows"] is not None:
            ax.scatter([frac_val], [dom["p_yes_knows"]], marker="s", facecolors="none",
                       edgecolors="tab:blue", s=70, label=f"diff-of-means, knows @ {frac_str}", zorder=5)
        if dom and dom["p_yes_gap"] is not None:
            ax.scatter([frac_val], [dom["p_yes_gap"]], marker="s", facecolors="none",
                       edgecolors="tab:orange", s=70, label=f"diff-of-means, gap @ {frac_str}", zorder=5)

    ax.set_xlabel("alpha (x mean layer-norm at the steered layer)")
    ax.set_ylabel('P("yes, user understands")')
    ax.set_ylim(-0.05, 1.05)
    ax.set_title(f"Steering the D0 probe direction, layer {out['layer']} (natural), "
                 f"n_test={out['n_test']}\n(grey = >10% generations unparseable / outside usable range)")
    ax.legend(fontsize=7, loc="center left", bbox_to_anchor=(1.01, 0.5))
    fig.tight_layout()
    fig.savefig(FIG_PATH, dpi=150)
    print(f"[plot] wrote {FIG_PATH}")


STEPS = {"steer_d0": step_steer_d0, "plot": step_plot}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "steer_d0"
    if which == "all":
        step_steer_d0(); step_plot()
    elif which in STEPS:
        STEPS[which]()
    else:
        print(f"unknown step {which!r}; choose from {list(STEPS)} or 'all'")
        sys.exit(1)
