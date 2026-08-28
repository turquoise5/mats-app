#!/usr/bin/env python
"""Act 0 driver: replication and data contact.

    python run_act0.py dryrun     # CPU, fake model — validates plumbing
    python run_act0.py gen        # API only, no GPU
    python run_act0.py mathdial   # clone + print 20 dialogues to read by hand
    python run_act0.py extract    # GPU
    python run_act0.py probe
    python run_act0.py plot
    python run_act0.py all        # gen -> extract -> probe -> plot
"""

from __future__ import annotations

import json
import random
import subprocess
import sys

import numpy as np

from src import config as C
from src import probes as P


# --------------------------------------------------------------------------------------
# gen
# --------------------------------------------------------------------------------------

def step_gen():
    from src.gen_data import generate_all

    C.banner("ACT 0.2 — GENERATE REPLICATION CONVERSATIONS")
    generate_all()


# --------------------------------------------------------------------------------------
# extract
# --------------------------------------------------------------------------------------

def step_extract(batch_size: int = 8):
    import torch  # noqa: F401  (import here so `gen` needs no GPU stack)

    from src import model as M

    C.banner("ACT 0.3 — EXTRACT ACTIVATIONS")
    rows = C.read_jsonl(C.RAW / "talktuner_repro.jsonl")
    print(f"[extract] {len(rows)} conversations")

    mdl, tok = M.load()
    env = M.print_env(mdl, tok)
    M.assert_template_sane(tok)

    for attribute, meta in C.ATTRIBUTES.items():
        subset = [r for r in rows if r["attribute"] == attribute]
        labels = np.array([meta["subcategories"].index(r["subcategory"]) for r in subset])
        ids = [r["id"] for r in subset]
        print(f"\n[extract] {attribute}: {len(subset)} samples, "
              f"label counts {np.bincount(labels).tolist()}")

        for position in C.READ_POSITIONS:
            prefix = meta["elicit_prefix"] if position == "elicited" else None
            texts = [M.render_chat(tok, r["turns"], prefix) for r in subset]

            if position == "natural":
                M.verify_last_token_indexing(mdl, tok, texts)

            print(f"[extract] {attribute} / {position}")
            acts = M.last_token_hidden(mdl, tok, texts, batch_size=batch_size)

            np.save(C.CACHE / f"act0_{attribute}_{position}.npy", acts)
            np.save(C.CACHE / f"act0_{attribute}_labels.npy", labels)
            with open(C.CACHE / f"act0_{attribute}_ids.json", "w") as f:
                json.dump(ids, f)

            C.log_run(
                act="0",
                experiment="extract",
                config={"attribute": attribute, "position": position,
                        "batch_size": batch_size, **env},
                metrics={"shape": list(acts.shape),
                         "label_counts": np.bincount(labels).tolist()},
            )


# --------------------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------------------

def step_probe():
    C.banner("ACT 0.4 — PER-LAYER PROBES")
    accs, ctrls, chances, results, n_vals = {}, {}, {}, {}, {}

    # Content keys let the control task detect near-duplicate conversations that
    # straddle the train/val split. Without them it only checks for overfitting.
    import re
    all_rows = C.read_jsonl(C.RAW / "talktuner_repro.jsonl")
    first_turn = {
        r["id"]: re.sub(r"\s+", " ", r["turns"][0]["content"].lower().strip())
        for r in all_rows
    }

    for attribute in C.ATTRIBUTES:
        labels = np.load(C.CACHE / f"act0_{attribute}_labels.npy")
        with open(C.CACHE / f"act0_{attribute}_ids.json") as f:
            ids = json.load(f)
        content_keys = [first_turn[i] for i in ids]
        for position in C.READ_POSITIONS:
            key = f"{attribute}/{position}"
            path = C.CACHE / f"act0_{attribute}_{position}.npy"
            if not path.exists():
                print(f"[probe] MISSING {path} — run `extract` first")
                continue

            print(f"\n[probe] {key}")
            acts = np.load(path)
            res = P.fit_layer_probes(acts, labels, seed=C.SEED)
            ctrl = P.control_task(acts, labels, content_keys=content_keys, seed=C.SEED)
            chance = P.majority_baseline(labels, res["val_idx"])

            print(f"  best acc {res['best_acc']:.3f} @ layer {res['best_layer']}  "
                  f"| control max {ctrl.max():.3f} | chance {chance:.3f}")

            accs[key], ctrls[key], chances[key], results[key] = (
                res["val_acc"], ctrl, chance, res
            )
            n_vals[key] = len(res["val_idx"])
            np.save(C.RESULTS / f"act0_acc_{attribute}_{position}.npy", res["val_acc"])
            np.save(C.RESULTS / f"act0_ctrl_{attribute}_{position}.npy", ctrl)

    verdicts = P.verdict(accs, ctrls, chances, n_vals)

    C.banner("ACT 0 VERDICT")
    for key, v in verdicts.items():
        print(f"\n{key}")
        for k, val in v.items():
            print(f"   {k:24s} {val}")

    passed, why = P.overall_verdict(verdicts)
    print("\n" + ("-" * 78))
    if passed:
        print(f"REPLICATION PASSED ({why}) — proceed to Act 1.")
    else:
        print(f"REPLICATION DID NOT PASS ({why}). Do NOT start Act 1.")
        print("Diagnose in order: (1) chat template rendering, (2) read-position "
              "indexing, (3) train/val leakage from near-duplicate generations, "
              "(4) label noise in the synthetic data.")
    print("-" * 78)

    C.log_run(act="0", experiment="probe_by_layer",
              config={"attributes": list(C.ATTRIBUTES), "positions": list(C.READ_POSITIONS)},
              metrics=verdicts)
    return verdicts


# --------------------------------------------------------------------------------------
# plot
# --------------------------------------------------------------------------------------

def step_plot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    C.banner("ACT 0 — PLOT")
    fig, axes = plt.subplots(1, len(C.ATTRIBUTES), figsize=(6 * len(C.ATTRIBUTES), 4.5),
                             squeeze=False)

    for ax, attribute in zip(axes[0], C.ATTRIBUTES):
        labels = np.load(C.CACHE / f"act0_{attribute}_labels.npy")
        chance = P.majority_baseline(labels)
        plotted = False
        for position in C.READ_POSITIONS:
            f_acc = C.RESULTS / f"act0_acc_{attribute}_{position}.npy"
            f_ctrl = C.RESULTS / f"act0_ctrl_{attribute}_{position}.npy"
            if not f_acc.exists():
                continue
            acc, ctrl = np.load(f_acc), np.load(f_ctrl)
            x = np.arange(len(acc))
            ax.plot(x, acc, marker="o", ms=3, label=f"probe ({position})")
            ax.plot(x, ctrl, ls=":", alpha=0.7, label=f"control task ({position})")
            plotted = True
        if plotted:
            ax.axhline(chance, color="k", ls="--", lw=1, label="majority class")
            ax.axhline(0.80, color="green", ls="-.", lw=1, alpha=0.5, label="pass threshold")
        ax.set_title(attribute)
        ax.set_xlabel("layer (0 = embeddings)")
        ax.set_ylabel("validation accuracy")
        ax.set_ylim(0, 1)
        ax.legend(fontsize=7)

    fig.suptitle(f"Act 0: user-attribute probes, {C.MODEL_ID}")
    fig.tight_layout()
    out = C.FIGS / "act0_probe_accuracy_by_layer.png"
    fig.savefig(out, dpi=160)
    print(f"[plot] wrote {out}")



# --------------------------------------------------------------------------------------
# dryrun
# --------------------------------------------------------------------------------------

def step_dryrun(n_per_subcategory: int = 40):
    """Exercise the full extract -> probe -> plot chain on CPU with fake data.

    Uses a tiny random-weight Qwen3 and a stub tokenizer, so no GPU and no model
    download. Proves the plumbing works before you pay for a pod.

    The fake conversations are LABEL-FREE by construction: the text carries no
    information about the subcategory. So the correct outcome is chance accuracy
    everywhere. Anything above chance means the pipeline is leaking the label
    somewhere it should not — a wiring bug, not a finding. The dry run asserts this.
    """
    import random as _random
    from tests.stub import tiny_setup
    from src import model as M

    C.banner("ACT 0 DRY RUN — CPU, fake model, fake data (accuracies are meaningless)")

    # Fabricate a conversation set with the same schema as the real generator.
    rows = []
    for attribute, meta in C.ATTRIBUTES.items():
        for sub in meta["subcategories"]:
            for i in range(n_per_subcategory):
                rng = _random.Random(hash((attribute, sub, i)) & 0xFFFF)
                # Deliberately label-free: the subcategory must NOT appear in the text.
                filler = " ".join(rng.choices(
                    "alpha bravo charlie delta echo foxtrot golf hotel india".split(),
                    k=rng.randint(8, 16)))
                rows.append({
                    "id": f"{attribute}__{sub.replace(' ', '_')}__{i}",
                    "attribute": attribute, "subcategory": sub,
                    "turns": [{"role": "user", "content": filler}],
                    "explicit": i % 2 == 0, "topic": "dryrun", "seed": C.SEED,
                })
    C.write_jsonl(C.RAW / "talktuner_repro.jsonl", rows)

    mdl, tok = tiny_setup()
    M.print_env(mdl, tok)
    M.assert_template_sane(tok)

    for attribute, meta in C.ATTRIBUTES.items():
        subset = [r for r in rows if r["attribute"] == attribute]
        labels = np.array([meta["subcategories"].index(r["subcategory"]) for r in subset])
        ids = [r["id"] for r in subset]
        for position in C.READ_POSITIONS:
            prefix = meta["elicit_prefix"] if position == "elicited" else None
            texts = [M.render_chat(tok, r["turns"], prefix) for r in subset]
            if position == "natural":
                M.verify_last_token_indexing(mdl, tok, texts)
            acts = M.last_token_hidden(mdl, tok, texts, batch_size=8)
            np.save(C.CACHE / f"act0_{attribute}_{position}.npy", acts)
            np.save(C.CACHE / f"act0_{attribute}_labels.npy", labels)
            with open(C.CACHE / f"act0_{attribute}_ids.json", "w") as f:
                json.dump(ids, f)

    verdicts = step_probe()
    step_plot()

    C.banner("DRY RUN SELF-CHECK")
    bad = []
    for key, v in verdicts.items():
        headroom = v["best_acc"] - v["chance"]
        ok = headroom < 0.25
        print(f"  {key:22s} best {v['best_acc']:.3f}  chance {v['chance']:.3f}  "
              f"{'OK' if ok else 'LEAK'}")
        if not ok:
            bad.append(key)
    if bad:
        raise AssertionError(
            f"Dry-run data is label-free, so these should be at chance: {bad}. "
            "The pipeline is leaking the label. Fix before running for real."
        )
    print("  All at chance, as expected for label-free inputs.")

    C.banner("DRY RUN COMPLETE")
    print("""Plumbing works end to end. What this did NOT test:
  - the real chat template (Qwen3's, not the stub's)
  - the OpenRouter generation path
  - whether a real model actually encodes user attributes

Now delete cache/ and data/raw/talktuner_repro.jsonl before the real run, or you will
probe fake activations and wonder why nothing works.""")


# --------------------------------------------------------------------------------------
# mathdial
# --------------------------------------------------------------------------------------

def step_mathdial(n: int = 20):
    C.banner("ACT 0.5 — READ THE DATA (do not skip, do not automate)")
    dest = C.RAW / "mathdial"
    if not dest.exists():
        subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/eth-nlped/mathdial", str(dest)],
            check=True,
        )

    import csv
    path = dest / "data" / "train.csv"
    if not path.exists():
        print(f"[mathdial] expected {path}; inspect the repo layout and adjust")
        return

    with open(path) as f:
        rows = list(csv.DictReader(f))
    print(f"[mathdial] {len(rows)} dialogues; columns: {list(rows[0].keys())}")

    sample = random.Random(C.SEED).sample(rows, min(n, len(rows)))
    for i, r in enumerate(sample):
        print("\n" + "=" * 78)
        print(f"SAMPLE {i + 1}/{len(sample)}")
        for k, v in r.items():
            if v and len(str(v)) < 4000:
                print(f"\n[{k}]\n{v}")

    print("\n" + "=" * 78)
    print("""Now write in notes.md, by hand, for each dialogue:
  - what did the student actually not understand? a named concept, a procedural
    slip, or a reading-comprehension failure?
  - did the teacher's first move reveal an inference about the student's knowledge?

Then paste 5 of these into the model under study, ask it to tutor, and READ the
responses. Where does it assume knowledge? Where does it over-explain?

Finish with 3-5 sentences: does the model behave as if it has a model of this
student? If it explains everything at the same depth regardless of who it is
talking to, that is a red flag for the whole project and you need to know now.""")


# --------------------------------------------------------------------------------------

STEPS = {
    "gen": step_gen,
    "extract": step_extract,
    "probe": step_probe,
    "plot": step_plot,
    "mathdial": step_mathdial,
    "dryrun": step_dryrun,
}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which == "all":
        step_gen(); step_extract(); step_probe(); step_plot()
    elif which in STEPS:
        STEPS[which]()
    else:
        print(f"unknown step {which!r}; choose from {list(STEPS)} or 'all'")
        sys.exit(1)
