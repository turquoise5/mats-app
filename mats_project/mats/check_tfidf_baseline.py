#!/usr/bin/env python
"""TF-IDF baseline vs. the layer probes -- the confound check 00_CONTEXT.md section 2
calls for explicitly: "It is trivially true that information about the user's knowledge
is present in the user's text ... so can TF-IDF, probably." This does not by itself
prove the probe found anything beyond surface lexical cues -- see act0_replication.md /
act1_structure.md for the cross-register and per-concept tests that actually address
that. This is just: does a bag-of-words baseline on the same labels get anywhere close?

Reuses cached activations (cache/act0_*.npy) and saved per-layer accuracies
(results/act0_acc_*.npy) -- does not re-run extraction or the probes.
"""
from __future__ import annotations

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

from src import config as C

rows = C.read_jsonl(C.RAW / "talktuner_repro.jsonl")

C.banner("TF-IDF BASELINE vs. LAYER PROBES")

tfidf_results = {}
for attribute, meta in C.ATTRIBUTES.items():
    sub = [r for r in rows if r["attribute"] == attribute]
    y = np.array([meta["subcategories"].index(r["subcategory"]) for r in sub])
    X = [" ".join(t["content"] for t in r["turns"] if t["role"] == "user") for r in sub]

    tr, te = train_test_split(
        np.arange(len(y)), test_size=0.2, stratify=y, random_state=C.SEED
    )
    v = TfidfVectorizer(max_features=5000, ngram_range=(1, 2))
    Xt = v.fit_transform([X[i] for i in tr])
    Xe = v.transform([X[i] for i in te])
    m = LogisticRegression(max_iter=3000, random_state=C.SEED).fit(Xt, y[tr])
    tfidf_acc = m.score(Xe, y[te])
    tfidf_results[attribute] = tfidf_acc

    natural_acc = np.load(C.RESULTS / f"act0_acc_{attribute}_natural.npy")
    best_probe_acc = float(natural_acc.max())

    print(f"{attribute}: TF-IDF {tfidf_acc:.3f}  vs probe {best_probe_acc:.3f}  "
          f"(natural, best layer {int(natural_acc.argmax())})")

print()

# Per-key layer-0 (raw embeddings, before any transformer block) vs majority baseline.
# Pulled from the actual probe run's saved arrays / logged verdict, not recomputed --
# 'chance' here is majority_baseline restricted to the *validation* split used by that
# run, which is only reproducible by rerunning the same seeded split, so we read it
# back from results/runs.jsonl instead of re-deriving it under a different name.
import json
with open(C.RUNS_LOG) as f:
    log_lines = [json.loads(line) for line in f]
verdict_metrics = [r for r in log_lines if r["experiment"] == "probe_by_layer"][-1]["metrics"]

for attribute in C.ATTRIBUTES:
    for position in C.READ_POSITIONS:
        key = f"{attribute}/{position}"
        acc = np.load(C.RESULTS / f"act0_acc_{attribute}_{position}.npy")
        chance = verdict_metrics[key]["chance"]
        print(f"{key:22s} layer0 {acc[0]:.4f}   majority {chance:.4f}")

C.log_run(
    act="0",
    experiment="tfidf_baseline",
    config={"max_features": 5000, "ngram_range": [1, 2], "test_size": 0.2},
    metrics={f"{a}_tfidf_acc": v for a, v in tfidf_results.items()},
)
