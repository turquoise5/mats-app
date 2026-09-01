#!/usr/bin/env python
"""Act 1 Task 1.2 driver: extract activations over the contrast set and run the
transfer tests. See mats/act1_handover_probing.md — this implements it end to end.

    python run_act1.py extract   # GPU -- cache activations for all read positions
    python run_act1.py justask   # GPU -- "just ask" baseline via local Qwen3-8B
    python run_act1.py probe     # CPU -- per-layer probes, TF-IDF, control tasks, verdict
    python run_act1.py plot      # CPU -- results/figs/act1_transfer.png
    python run_act1.py all       # extract -> justask -> probe -> plot

Act 0 must have already passed (act0_replication.md). This step reuses src/model.py
unchanged; it does not generate data and does not touch data/contrast/contrast_v1.jsonl.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter

import numpy as np

from src import act1_data as AD
from src import config as C
from src import probes as P

CACHE_PREFIX = "act1_all"  # cache/act1_all_{position}.npy, mirrors act0_{attribute}_*


# --------------------------------------------------------------------------------------
# extract
# --------------------------------------------------------------------------------------

def step_extract(batch_size: int = 8):
    import torch  # noqa: F401  (import here so other steps need no GPU stack)

    from src import model as M

    C.banner("ACT 1.2 — EXTRACT ACTIVATIONS (contrast_v1.jsonl)")
    rows = AD.load_rows()
    print(f"[extract] {len(rows)} conversations")

    mdl, tok = M.load()
    env = M.print_env(mdl, tok)
    M.assert_template_sane(tok)

    ids = [r["id"] for r in rows]

    for position in C.READ_POSITIONS:
        if position == "elicited":
            # Per-row prefix: the knowledge target has no Act 0 attribute entry, so it
            # is formatted per concept from C.ACT1_ELICIT_PREFIX (recorded in the run
            # log below -- it's a real experimental parameter, not an implementation
            # detail).
            texts = [
                M.render_chat(tok, r["turns"], C.ACT1_ELICIT_PREFIX.format(concept=r["concept"]))
                for r in rows
            ]
        else:
            texts = [M.render_chat(tok, r["turns"]) for r in rows]
            M.verify_last_token_indexing(mdl, tok, texts)

        print(f"[extract] position={position}")
        acts = M.last_token_hidden(mdl, tok, texts, batch_size=batch_size)

        np.save(C.CACHE / f"{CACHE_PREFIX}_{position}.npy", acts)
        with open(C.CACHE / f"{CACHE_PREFIX}_ids.json", "w") as f:
            json.dump(ids, f)

        C.log_run(
            act="1",
            experiment="extract",
            config={
                "position": position, "batch_size": batch_size,
                "elicit_prefix_template": C.ACT1_ELICIT_PREFIX, **env,
            },
            metrics={"shape": list(acts.shape), "n_rows": len(rows)},
        )

    print(f"[extract] done. elicit prefix template used: {C.ACT1_ELICIT_PREFIX!r}")


# --------------------------------------------------------------------------------------
# "just ask" baseline
# --------------------------------------------------------------------------------------

JUST_ASK_TEMPLATE = (
    "Based on this conversation, does the user understand {concept}? Answer yes or no."
)


def _parse_yes_no(text: str) -> str | None:
    low = text.strip().lower()
    has_yes = re.search(r"\byes\b", low) is not None
    has_no = re.search(r"\bno\b", low) is not None
    if has_yes and not has_no:
        return "yes"
    if has_no and not has_yes:
        return "no"
    return None  # ambiguous or neither -- do not guess


def step_justask(batch_size: int = 8, max_new_tokens: int = 12):
    import torch  # noqa: F401

    from src import model as M

    C.banner('ACT 1.2 — "JUST ASK" BASELINE (local Qwen3-8B, no OpenRouter)')
    rows = AD.load_rows()
    mdl, tok = M.load()
    M.assert_template_sane(tok)

    prompt_turns = [
        r["turns"] + [{"role": "user", "content": JUST_ASK_TEMPLATE.format(concept=r["concept"])}]
        for r in rows
    ]
    texts = [M.render_chat(tok, t) for t in prompt_turns]

    print(f"[justask] generating for {len(texts)} conversations "
          f"(max_new_tokens={max_new_tokens}, batch_size={batch_size})")
    gens = M.greedy_generate(mdl, tok, texts, max_new_tokens=max_new_tokens, batch_size=batch_size)

    print("\n[justask] 3 sample generations, for eyeballing:")
    for r, g in list(zip(rows, gens))[:3]:
        print(f"  {r['id']}: concept={r['concept_slug']} knowledge_state={r['knowledge_state']!r} "
              f"-> {g!r}")

    parsed = [_parse_yes_no(g) for g in gens]
    n_ambig = sum(1 for p in parsed if p is None)
    print(f"\n[justask] {n_ambig}/{len(parsed)} generations were not parseable as yes/no "
          f"(neither predicted nor guessed).")

    out = [
        {"id": r["id"], "generation": g, "parsed": p}
        for r, g, p in zip(rows, gens, parsed)
    ]
    with open(C.CACHE / "act1_justask.json", "w") as f:
        json.dump(out, f)

    C.log_run(
        act="1",
        experiment="justask_generate",
        config={"template": JUST_ASK_TEMPLATE, "max_new_tokens": max_new_tokens,
                "batch_size": batch_size},
        metrics={"n": len(out), "n_ambiguous": n_ambig},
    )
    print(f"[justask] wrote {C.CACHE / 'act1_justask.json'}")


def _load_justask() -> dict:
    path = C.CACHE / "act1_justask.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return {r["id"]: r["parsed"] for r in json.load(f)}


# --------------------------------------------------------------------------------------
# probe: per-layer probes, TF-IDF, control task, undisclosed prior, verdict
# --------------------------------------------------------------------------------------

def _tfidf_condition(b: AD.Subset, train_local, test_local, seed: int) -> dict:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    v = TfidfVectorizer(max_features=5000, ngram_range=(1, 2))
    Xt = v.fit_transform(b.text[train_local])
    Xe = v.transform(b.text[test_local])
    m = LogisticRegression(max_iter=3000, random_state=seed).fit(Xt, b.y[train_local])
    acc = float(m.score(Xe, b.y[test_local]))
    return {"tfidf_acc": acc}


def _overlap_stats(b: AD.Subset, train_local, test_local, orphan_pids: set) -> dict:
    tr_keys, te_keys = set(b.content_key[train_local]), set(b.content_key[test_local])
    tr_pids, te_pids = set(b.paired_id[train_local]), set(b.paired_id[test_local])
    return {
        "content_key_overlap": len(tr_keys & te_keys),
        "paired_id_overlap": len(tr_pids & te_pids),
        "n_orphan_train": int(sum(1 for p in b.paired_id[train_local] if p in orphan_pids)),
        "n_orphan_test": int(sum(1 for p in b.paired_id[test_local] if p in orphan_pids)),
    }


def _run_condition(spec: dict, b: AD.Subset, acts_binary: np.ndarray, seed: int):
    """Returns (res, train_local, test_local, ctrl_or_None)."""
    if spec["kind"] == "internal":
        sub_local = np.where(spec["mask"])[0]
        sub_acts = acts_binary[sub_local]
        sub_y = b.y[sub_local]
        sub_groups = spec["groups"][sub_local]
        res = P.fit_layer_probes(sub_acts, sub_y, groups=sub_groups, seed=seed)
        train_local = sub_local[res["train_idx"]]
        test_local = sub_local[res["val_idx"]]
        ctrl = P.control_task(
            sub_acts, sub_y, content_keys=b.content_key[sub_local].tolist(),
            groups=sub_groups, seed=seed,
        )
    else:
        train_local = np.where(spec["train_mask"])[0]
        test_local = np.where(spec["test_mask"])[0]
        res = P.fit_layer_probes_explicit(acts_binary, b.y, train_local, test_local, seed=seed)
        ctrl = None
    return res, train_local, test_local, ctrl


def step_probe():
    C.banner("ACT 1.2 — PER-LAYER PROBES, TF-IDF, CONTROL TASKS")

    rows = AD.load_rows()
    idx = AD.Index(rows)
    b = idx.binary()

    with open(C.CACHE / f"{CACHE_PREFIX}_ids.json") as f:
        cached_ids = json.load(f)
    if cached_ids != idx.ids.tolist():
        raise RuntimeError(
            "cache/act1_all_ids.json does not match the row order of contrast_v1.jsonl "
            "as loaded now -- activations and metadata would silently misalign. Re-run "
            "`extract` (the dataset must not have changed under a frozen file)."
        )

    pair_counts = Counter(idx.paired_id.tolist())
    orphan_pids = {p for p, c in pair_counts.items() if c == 1}
    print(f"[probe] {len(pair_counts)} unique paired_id, "
          f"{sum(1 for c in pair_counts.values() if c == 2)} complete pairs, "
          f"{len(orphan_pids)} orphans (whole dataset)")

    conds = AD.build_conditions(b)

    all_results = {}  # position -> condition -> metrics dict (for the verdict + plot)

    for position in C.READ_POSITIONS:
        acts_full = np.load(C.CACHE / f"{CACHE_PREFIX}_{position}.npy")
        acts_binary = acts_full[b.global_idx]
        justask = _load_justask()

        print(f"\n[probe] === position={position} ===")
        pos_results = {}

        for name, spec in conds.items():
            res, train_local, test_local, ctrl = _run_condition(spec, b, acts_binary, C.SEED)
            n_layers = len(res["val_acc"])
            chance = P.majority_baseline(b.y, val_idx=test_local)
            overlap = _overlap_stats(b, train_local, test_local, orphan_pids)
            tfidf = _tfidf_condition(b, train_local, test_local, C.SEED)

            justask_ids = idx.ids[b.global_idx[test_local]]
            justask_pred = [justask.get(i) for i in justask_ids]
            n_parsed = sum(1 for p in justask_pred if p is not None)
            if n_parsed:
                y_test = b.y[test_local]
                correct = sum(
                    1 for p, y in zip(justask_pred, y_test)
                    if p is not None and (p == "yes") == bool(y)
                )
                justask_acc = correct / n_parsed
            else:
                justask_acc = None

            thresh = P.leakage_threshold(chance, len(test_local), n_layers) if len(test_local) else None
            control_max = float(ctrl.max()) if ctrl is not None else None
            control_clean = (control_max <= thresh) if control_max is not None else None

            metrics = {
                "val_acc": res["val_acc"].tolist(),
                "best_acc": res["best_acc"],
                "best_layer": res["best_layer"],
                "n_train": len(train_local),
                "n_test": len(test_local),
                "majority_baseline": chance,
                "control_acc": ctrl.tolist() if ctrl is not None else None,
                "control_max": control_max,
                "leakage_threshold": thresh,
                "control_clean": control_clean,
                "tfidf_acc": tfidf["tfidf_acc"],
                "justask_acc": justask_acc,
                "justask_n_parsed": n_parsed,
                **overlap,
            }
            pos_results[name] = metrics

            print(f"  {name:26s} n_train={metrics['n_train']:4d} n_test={metrics['n_test']:4d} "
                  f"best_acc={metrics['best_acc']:.3f}@L{metrics['best_layer']:<2d} "
                  f"majority={chance:.3f} tfidf={tfidf['tfidf_acc']:.3f} "
                  + (f"ctrl_max={control_max:.3f} " if control_max is not None else "")
                  + (f"justask={justask_acc:.3f} " if justask_acc is not None else "justask=n/a "))

            np.save(C.RESULTS / f"act1_acc_{name}_{position}.npy", res["val_acc"])
            if ctrl is not None:
                np.save(C.RESULTS / f"act1_ctrl_{name}_{position}.npy", ctrl)

            C.log_run(
                act="1", experiment=f"transfer/{position}/{name}",
                config={"condition": name, "position": position, "seed": C.SEED},
                metrics=metrics,
            )

        all_results[position] = pos_results

        # -- undisclosed-cell prior: what does the pooled probe say about rows that
        #    reveal nothing? Uses the pooled condition's best-layer probe.
        pooled_res, *_ = _run_condition(conds["pooled"], b, acts_binary, C.SEED)
        best_layer = pooled_res["best_layer"]
        undisclosed_acts = acts_full[idx.undisclosed_idx][:, best_layer, :]
        pred = pooled_res["probes"][best_layer].predict(undisclosed_acts)
        frac_knows = float(np.mean(pred))
        justask_undisc = [justask.get(i) for i in idx.ids[idx.undisclosed_idx]]
        n_parsed_u = sum(1 for p in justask_undisc if p is not None)
        justask_frac_yes = (
            sum(1 for p in justask_undisc if p == "yes") / n_parsed_u if n_parsed_u else None
        )
        print(f"  [undisclosed prior] pooled probe @L{best_layer}: "
              f"P(predict 'knows') = {frac_knows:.3f} over {len(undisclosed_acts)} rows "
              f"| just-ask yes-rate = {justask_frac_yes}")
        C.log_run(
            act="1", experiment=f"undisclosed_prior/{position}",
            config={"best_layer": best_layer, "position": position},
            metrics={"n_undisclosed": len(undisclosed_acts), "frac_predicted_knows": frac_knows,
                     "justask_frac_yes": justask_frac_yes, "justask_n_parsed": n_parsed_u},
        )
        all_results[position]["_undisclosed_prior"] = {
            "frac_predicted_knows": frac_knows, "best_layer": best_layer,
            "justask_frac_yes": justask_frac_yes,
        }

    # -- verdict, per act1_handover_probing.md §7 -------------------------------------
    C.banner("ACT 1 TASK 1.2 — VERDICT")
    verdict_lines = []
    for position in C.READ_POSITIONS:
        cr = all_results[position]["cross-register"]
        sd = all_results[position]["stated->demonstrated"]
        line = (f"{position}: cross-register={cr['best_acc']:.3f} "
                f"(tfidf={cr['tfidf_acc']:.3f}, n_train={cr['n_train']}) | "
                f"stated->demonstrated={sd['best_acc']:.3f} "
                f"(tfidf={sd['tfidf_acc']:.3f}, n_train={sd['n_train']})")
        print(line)
        verdict_lines.append((position, cr, sd))

    def branch(cr_acc, sd_acc):
        if cr_acc >= 0.75 and sd_acc >= 0.75:
            return "PASS: proceed to Act 2 as planned"
        if cr_acc >= 0.75:
            return "NARROW: cross-register holds, stated->demonstrated collapses -- " \
                   "the probe reads self-reports, not demonstrated knowledge"
        return "PIVOT: cross-register collapses -- style probe"

    best_position, best_cr, best_sd = max(
        verdict_lines, key=lambda t: min(t[1]["best_acc"], t[2]["best_acc"])
    )
    overall = branch(best_cr["best_acc"], best_sd["best_acc"])
    print(f"\nBest position by min(headline1, headline2): {best_position}")
    print(f"VERDICT: {overall}")

    C.log_run(
        act="1", experiment="task1.2_verdict",
        config={"positions": list(C.READ_POSITIONS)},
        metrics={
            "by_position": {p: {"cross_register": cr["best_acc"], "cr_tfidf": cr["tfidf_acc"],
                                 "stated_to_demonstrated": sd["best_acc"], "sd_tfidf": sd["tfidf_acc"]}
                             for p, cr, sd in verdict_lines},
            "best_position": best_position, "verdict": overall,
        },
    )

    with open(C.RESULTS / "act1_probe_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n[probe] wrote {C.RESULTS / 'act1_probe_results.json'}")
    return all_results


# --------------------------------------------------------------------------------------
# plot
# --------------------------------------------------------------------------------------

CONDITION_ORDER = [
    "pooled", "within-expert", "within-novice", "cross-register", "cross-register-rev",
    "within-stated", "stated->demonstrated", "demonstrated->stated", "cross-both",
]


def step_plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    C.banner("ACT 1 — PLOT")
    with open(C.RESULTS / "act1_probe_results.json") as f:
        all_results = json.load(f)

    fig, axes = plt.subplots(3, 3, figsize=(16, 13), squeeze=False)
    for ax, name in zip(axes.flat, CONDITION_ORDER):
        for position in C.READ_POSITIONS:
            m = all_results.get(position, {}).get(name)
            if m is None:
                continue
            acc = np.array(m["val_acc"])
            x = np.arange(len(acc))
            ax.plot(x, acc, marker="o", ms=2.5, label=f"probe ({position})")
            if m["control_acc"] is not None:
                ax.plot(x, m["control_acc"], ls=":", alpha=0.6, label=f"control ({position})")
            ax.axhline(m["tfidf_acc"], ls="--", lw=1, alpha=0.7, label=f"TF-IDF ({position})")
            if m["justask_acc"] is not None:
                ax.axhline(m["justask_acc"], ls="-.", lw=1, alpha=0.6, color="purple",
                            label=f"just-ask ({position})")
            ax.axhline(m["majority_baseline"], color="k", ls="--", lw=1, alpha=0.5,
                        label="majority" if position == C.READ_POSITIONS[0] else None)
        ax.axhline(0.75, color="green", ls="-.", lw=1, alpha=0.4)
        ax.set_title(f"{name}\n(n_train={all_results[C.READ_POSITIONS[0]][name]['n_train']}, "
                      f"n_test={all_results[C.READ_POSITIONS[0]][name]['n_test']})", fontsize=9)
        ax.set_xlabel("layer", fontsize=8)
        ax.set_ylabel("accuracy", fontsize=8)
        ax.set_ylim(0, 1)
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=5.5, loc="lower right")

    fig.suptitle(f"Act 1 Task 1.2: transfer conditions, {C.MODEL_ID}", fontsize=13)
    fig.tight_layout()
    out = C.FIGS / "act1_transfer.png"
    fig.savefig(out, dpi=150)
    print(f"[plot] wrote {out}")


# --------------------------------------------------------------------------------------

STEPS = {
    "extract": step_extract,
    "justask": step_justask,
    "probe": step_probe,
    "plot": step_plot,
}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which == "all":
        step_extract(); step_justask(); step_probe(); step_plot()
    elif which in STEPS:
        STEPS[which]()
    else:
        print(f"unknown step {which!r}; choose from {list(STEPS)} or 'all'")
        sys.exit(1)
