"""Per-layer linear probes, control tasks, and baselines.

Linear probes only. If a linear probe fails, that is the finding, not a reason to reach
for a deeper model.
"""

from __future__ import annotations

import math

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def make_probe(seed: int = 0):
    return make_pipeline(
        StandardScaler(),
        # NOTE: no `multi_class` kwarg (removed in scikit-learn 1.7) and no `penalty`
        # kwarg (deprecated in 1.8). The defaults are multinomial + L2; C sets strength.
        LogisticRegression(C=1.0, max_iter=3000, random_state=seed),
    )


def split_indices(labels, groups=None, test_size: float = 0.2, seed: int = 0):
    """Stratified split, grouped if `groups` is given (no group straddles the split).

    Grouping matters from Act 1 onward, where the two register renderings of the same
    content share a paired_id and must never be split across train/val.
    """
    idx = np.arange(len(labels))
    if groups is None:
        return train_test_split(idx, test_size=test_size, stratify=labels, random_state=seed)

    groups = np.asarray(groups)
    uniq = np.unique(groups)
    # Stratify groups by their modal label so class balance survives grouping.
    g_label = np.array(
        [np.bincount(np.asarray(labels)[groups == g]).argmax() for g in uniq]
    )
    g_tr, g_te = train_test_split(
        uniq, test_size=test_size, stratify=g_label, random_state=seed
    )
    tr = idx[np.isin(groups, g_tr)]
    te = idx[np.isin(groups, g_te)]
    return tr, te


def _fit_on_split(acts, labels, tr, te, seed, verbose, log_prefix="probe"):
    """Shared per-layer fit/score loop for a fixed (train_idx, test_idx) pair."""
    acts = np.asarray(acts)
    labels = np.asarray(labels)
    n, n_layers_plus1, d = acts.shape

    if verbose:
        vals, counts = np.unique(labels, return_counts=True)
        print(f"  [{log_prefix}] acts {acts.shape}  "
              f"labels {dict(zip(vals.tolist(), counts.tolist()))}")
        print(f"  [{log_prefix}] train {len(tr)}  val {len(te)}")

    accs, probes = [], []
    for layer in range(n_layers_plus1):
        X, y = acts[:, layer, :], labels
        p = make_probe(seed)
        p.fit(X[tr], y[tr])
        accs.append(float(p.score(X[te], y[te])))
        probes.append(p)

    return {
        "val_acc": np.array(accs),
        "probes": probes,
        "train_idx": np.asarray(tr),
        "val_idx": np.asarray(te),
        "best_layer": int(np.argmax(accs)),
        "best_acc": float(np.max(accs)),
    }


def fit_layer_probes(acts, labels, groups=None, test_size=0.2, seed=0, verbose=True):
    """acts: (n, n_layers+1, d). Splits internally (see `split_indices`). Returns
    per-layer val accuracy and fitted probes."""
    tr, te = split_indices(labels, groups, test_size, seed)
    return _fit_on_split(acts, labels, tr, te, seed, verbose)


def fit_layer_probes_explicit(acts, labels, train_idx, test_idx, seed=0, verbose=True):
    """Like `fit_layer_probes`, but train/test are given explicitly rather than split
    internally. Use this whenever train and test are defined by the data -- a transfer
    condition (Act 1: cross-register, stated->demonstrated, ...) -- not by a random
    split. Do not fake this by shuffling labels into `split_indices`; that leaks."""
    return _fit_on_split(acts, labels, train_idx, test_idx, seed, verbose,
                          log_prefix="probe_explicit")


def control_task(acts, labels, content_keys=None, groups=None, test_size=0.2, seed=0):
    """Hewitt & Liang style control task, as used by Bortoletto et al. (2406.17513).

    Assigns a random label to each unique INPUT, consistently — not a random label to
    each row. The distinction matters and is easy to get wrong:

    - Row-wise permutation gives two copies of the same input two *different* random
      labels, so memorising the input cannot help, and the control stays at chance even
      when the split is badly leaky. It detects nothing.
    - Per-input assignment gives duplicates the *same* random label, so a probe that has
      memorised a duplicate seen in training scores above chance on validation. That is
      exactly the leak we want to surface.

    `content_keys` should be a hashable per-sample identifier of the input content (e.g.
    the normalised first user turn). If omitted, every row is treated as unique and the
    control degrades to an overfitting check only — it can no longer detect leakage.

    Above chance => stop and investigate the split before trusting the real probe.
    """
    labels = np.asarray(labels)
    n = len(labels)
    rng = np.random.default_rng(seed)

    if content_keys is None:
        print("  [control_task] WARNING: no content_keys given. This run can detect "
              "overfitting but NOT duplicate leakage across the split.")
        content_keys = list(range(n))

    classes = np.unique(labels)
    chance = float(np.bincount(np.searchsorted(classes, labels)).max() / n)

    # Offset the control RNG from the split seed. Sharing a seed with whatever produced
    # the true labels can silently make the control labels track them, which would make
    # a clean run look like a leak.
    control_labels = None
    for attempt in range(5):
        rng = np.random.default_rng(seed + 10007 * (attempt + 1))
        key_to_label = {k: rng.choice(classes) for k in dict.fromkeys(content_keys)}
        candidate = np.array([key_to_label[k] for k in content_keys])
        agreement = float((candidate == labels).mean())
        if agreement <= chance + 0.15:
            control_labels = candidate
            break
        print(f"  [control_task] draw {attempt} agrees with true labels at "
              f"{agreement:.2f} (chance {chance:.2f}) — redrawing.")
    if control_labels is None:
        raise RuntimeError(
            "Could not draw control labels uncorrelated with the true labels after 5 "
            "attempts. Inspect the label distribution before proceeding."
        )

    n_unique = len(key_to_label)
    if n_unique < n:
        print(f"  [control_task] {n - n_unique} of {n} samples share an input with "
              "another sample — the control task will expose it if it leaks.")

    res = fit_layer_probes(acts, control_labels, groups, test_size, seed, verbose=False)
    return res["val_acc"]


def majority_baseline(labels, val_idx=None) -> float:
    labels = np.asarray(labels)
    y = labels[val_idx] if val_idx is not None else labels
    _, counts = np.unique(y, return_counts=True)
    return float(counts.max() / counts.sum())


def probe_directions(result, layer: int) -> np.ndarray:
    """Weight vectors from a fitted pipeline at `layer`, un-scaled back to activation space.

    StandardScaler divides by sigma, so the direction in raw activation space is w / sigma.
    Shape (n_classes_or_1, d). Used in Act 1 geometry and Act 2 steering.
    """
    pipe = result["probes"][layer]
    scaler = pipe.named_steps["standardscaler"]
    clf = pipe.named_steps["logisticregression"]
    return clf.coef_ / scaler.scale_


def leakage_threshold(chance: float, n_val: int, n_layers: int, alpha: float = 0.05) -> float:
    """Upper bound on control-task accuracy under the null, corrected for layer count.

    A fixed margin (e.g. chance + 0.10) is wrong in both directions: too tight for small
    validation sets, where binomial noise alone clears it, and too loose for large ones.
    We also take a MAX over layers, so the null must be Bonferroni-corrected or a long
    model will trip the alarm by chance.
    """
    from statistics import NormalDist

    sd = math.sqrt(max(chance * (1.0 - chance), 1e-9) / max(n_val, 1))
    z = NormalDist().inv_cdf(1.0 - alpha / max(n_layers, 1))
    return min(1.0, chance + z * sd)


def verdict(accs_by_key: dict, control_by_key: dict, chance_by_key: dict,
            n_val_by_key: dict | None = None) -> dict:
    """Act 0 pass/partial/fail against the criterion in act0_replication.md."""
    out = {}
    for key, accs in accs_by_key.items():
        accs = np.asarray(accs)
        ctrl = np.asarray(control_by_key[key])
        chance = chance_by_key[key]
        n_val = (n_val_by_key or {}).get(key)
        best, best_layer = float(accs.max()), int(accs.argmax())

        early = float(accs[: len(accs) // 3].mean())
        late = float(accs[len(accs) // 3 :].mean())
        rises = late > early + 0.03

        thresh = (
            leakage_threshold(chance, n_val, len(ctrl))
            if n_val else chance + 0.10
        )
        control_clean = float(ctrl.max()) <= thresh

        if not control_clean:
            v = "FAIL (control task above chance -> leakage across split)"
        elif best >= 0.80 and rises:
            v = "PASS"
        elif best >= 0.80:
            v = "PARTIAL (high accuracy but flat across layers)"
        elif best >= 0.70:
            v = "PARTIAL (weak accuracy)"
        else:
            v = "FAIL"

        out[key] = {
            "verdict": v,
            "best_acc": best,
            "best_layer": best_layer,
            "chance": chance,
            "control_max": float(ctrl.max()),
            "leakage_threshold": float(thresh),
            "n_val": n_val,
            "mean_acc_early_third": early,
            "mean_acc_later_thirds": late,
            "rises_with_depth": bool(rises),
        }
    return out


def overall_verdict(verdicts: dict) -> tuple[bool, str]:
    """Replication passes only if something PASSes AND nothing shows leakage.

    `any(PASS)` is wrong: one good probe does not excuse a leaky split elsewhere,
    because the leak usually comes from the shared generation process.
    """
    leaky = [k for k, v in verdicts.items() if "leakage" in v["verdict"]]
    passes = [k for k, v in verdicts.items() if v["verdict"] == "PASS"]
    if leaky:
        return False, f"leakage detected in: {', '.join(leaky)}"
    if not passes:
        return False, "no attribute/position reached the PASS criterion"
    return True, f"passed on: {', '.join(passes)}"
