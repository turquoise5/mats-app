"""Tests for src/probes.py. Pure numpy + sklearn — runs anywhere, no GPU, no downloads."""

from __future__ import annotations

import numpy as np
import pytest

from src import probes as P

N, L, D = 240, 8, 64
CHANCE_MARGIN = 0.12


def synthetic(seed: int = 0, signal_from_layer: int = 4):
    """Activations with a planted linear signal that strengthens with depth."""
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 3, N)
    acts = rng.normal(size=(N, L, D)).astype("float32")
    for layer in range(signal_from_layer, L):
        acts[:, layer, :3] += y[:, None] * (0.4 * (layer - signal_from_layer + 1))
    keys = [f"conv{i}" for i in range(N)]
    return acts, y, keys


# ---------------------------------------------------------------------------------
# Probe fitting
# ---------------------------------------------------------------------------------

def test_probe_recovers_planted_signal():
    acts, y, _ = synthetic()
    res = P.fit_layer_probes(acts, y, seed=0)
    assert res["val_acc"].shape == (L,)
    assert res["best_acc"] > 0.75
    assert res["best_layer"] >= 4, "signal was planted in later layers only"


def test_probe_accuracy_rises_with_depth():
    acts, y, _ = synthetic()
    accs = P.fit_layer_probes(acts, y, seed=0)["val_acc"]
    assert accs[:3].mean() < accs[-3:].mean()


def test_probe_at_chance_when_no_signal():
    rng = np.random.default_rng(1)
    acts = rng.normal(size=(N, L, D)).astype("float32")
    y = rng.integers(0, 3, N)
    accs = P.fit_layer_probes(acts, y, seed=0)["val_acc"]
    chance = P.majority_baseline(y)
    assert accs.max() < chance + 0.25, "probe fitting noise on pure noise"


# ---------------------------------------------------------------------------------
# Control task — this is the one that was silently broken
# ---------------------------------------------------------------------------------

def test_control_task_is_near_chance_on_clean_data():
    acts, y, keys = synthetic()
    ctrl = P.control_task(acts, y, content_keys=keys, seed=0)
    chance = P.majority_baseline(y)
    assert ctrl.max() < chance + CHANCE_MARGIN, (
        f"control task {ctrl.max():.3f} above chance {chance:.3f} on clean data"
    )


def test_control_task_detects_duplicate_leakage():
    """The regression test for the original bug.

    Row-wise label permutation gave duplicates *different* labels, so memorising an
    input could not help and the control stayed at chance even on a badly leaky split.
    Per-input assignment gives duplicates the *same* label, which is detectable.
    """
    acts, y, keys = synthetic()
    acts2 = np.concatenate([acts, acts])
    y2 = np.concatenate([y, y])
    keys2 = keys + keys

    ctrl = P.control_task(acts2, y2, content_keys=keys2, seed=0)
    chance = P.majority_baseline(y2)
    assert ctrl.max() > chance + CHANCE_MARGIN, (
        "control task failed to detect duplicated rows straddling the split"
    )


def test_control_task_labels_not_correlated_with_truth():
    """Guards the seed collision that made a clean control look identical to the probe."""
    acts, y, keys = synthetic()
    ctrl = P.control_task(acts, y, content_keys=keys, seed=0)
    real = P.fit_layer_probes(acts, y, seed=0)["val_acc"]
    assert not np.allclose(ctrl, real), "control labels reproduced the true labels"


# ---------------------------------------------------------------------------------
# Explicit-split probes (Act 1 transfer conditions)
# ---------------------------------------------------------------------------------

def test_fit_layer_probes_explicit_matches_manual_split():
    """With train/test passed explicitly, results must equal fitting fit_layer_probes
    on that same split by hand -- same probe, same per-layer loop."""
    acts, y, _ = synthetic()
    tr, te = P.split_indices(y, seed=0)
    explicit = P.fit_layer_probes_explicit(acts, y, tr, te, seed=0)
    manual_accs = []
    for layer in range(acts.shape[1]):
        p = P.make_probe(0)
        p.fit(acts[tr, layer, :], y[tr])
        manual_accs.append(p.score(acts[te, layer, :], y[te]))
    assert np.allclose(explicit["val_acc"], manual_accs)
    assert np.array_equal(explicit["train_idx"], tr)
    assert np.array_equal(explicit["val_idx"], te)


def test_fit_layer_probes_explicit_recovers_planted_signal():
    """Sanity check on a real transfer-style split: two disjoint index blocks, signal
    still present in both, accuracy should climb with depth as with the internal split."""
    acts, y, _ = synthetic()
    half = N // 2
    train_idx, test_idx = np.arange(half), np.arange(half, N)
    res = P.fit_layer_probes_explicit(acts, y, train_idx, test_idx, seed=0)
    assert res["val_acc"].shape == (L,)
    assert res["best_acc"] > 0.6
    assert res["best_layer"] >= 4


def test_fit_layer_probes_explicit_does_not_leak_via_split_indices():
    """Regression guard for the handover's warning: explicit train/test must be used
    as given, never re-derived by shuffling labels through split_indices."""
    acts, y, _ = synthetic()
    train_idx, test_idx = np.arange(0, 50), np.arange(50, 100)
    res = P.fit_layer_probes_explicit(acts, y, train_idx, test_idx, seed=0)
    assert set(res["train_idx"].tolist()) == set(train_idx.tolist())
    assert set(res["val_idx"].tolist()) == set(test_idx.tolist())
    assert len(set(res["train_idx"].tolist()) & set(res["val_idx"].tolist())) == 0


# ---------------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------------

def test_grouped_split_never_straddles():
    """Act 1 depends on this: two register renderings of one item share a paired_id."""
    _, y, _ = synthetic()
    groups = np.repeat(np.arange(N // 2), 2)
    tr, te = P.split_indices(y, groups=groups, seed=0)
    assert len(set(groups[tr]) & set(groups[te])) == 0
    assert len(tr) + len(te) == N


def test_ungrouped_split_is_stratified():
    _, y, _ = synthetic()
    tr, te = P.split_indices(y, seed=0)
    p_tr = np.bincount(y[tr], minlength=3) / len(tr)
    p_te = np.bincount(y[te], minlength=3) / len(te)
    assert np.abs(p_tr - p_te).max() < 0.1


# ---------------------------------------------------------------------------------
# Directions (used for Act 2 steering)
# ---------------------------------------------------------------------------------

def test_probe_directions_match_decision_function():
    """coef / scale must be the direction in *raw activation* space, not scaled space."""
    acts, y, _ = synthetic()
    res = P.fit_layer_probes(acts, y, seed=0)
    layer = res["best_layer"]
    X = acts[:, layer, :]

    w = P.probe_directions(res, layer)
    manual = X @ w.T
    actual = res["probes"][layer].decision_function(X)

    # Differ only by a constant per class (the folded-in intercept + mean offset).
    resid = actual - manual
    assert np.allclose(resid, resid[0], atol=1e-3), "direction is not in raw space"


# ---------------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------------

def test_verdict_passes_clean_run():
    acts, y, keys = synthetic()
    res = P.fit_layer_probes(acts, y, seed=0)
    ctrl = P.control_task(acts, y, content_keys=keys, seed=0)
    v = P.verdict({"k": res["val_acc"]}, {"k": ctrl}, {"k": P.majority_baseline(y)})
    assert v["k"]["verdict"] == "PASS"
    assert v["k"]["rises_with_depth"] is True


def test_verdict_fails_when_control_is_dirty():
    acts, y, _ = synthetic()
    res = P.fit_layer_probes(acts, y, seed=0)
    dirty = np.full(L, 0.95)
    v = P.verdict({"k": res["val_acc"]}, {"k": dirty}, {"k": P.majority_baseline(y)})
    assert v["k"]["verdict"].startswith("FAIL")
    assert "leakage" in v["k"]["verdict"]


def test_majority_baseline():
    y = np.array([0, 0, 0, 1])
    assert P.majority_baseline(y) == pytest.approx(0.75)


# ---------------------------------------------------------------------------------
# Leakage threshold calibration (regression: a fixed +0.10 margin was wrong)
# ---------------------------------------------------------------------------------

def test_leakage_threshold_tightens_with_more_data():
    small = P.leakage_threshold(0.333, n_val=24, n_layers=5)
    large = P.leakage_threshold(0.333, n_val=600, n_layers=5)
    assert small > large, "threshold must loosen for small validation sets"
    assert large > 0.333


def test_leakage_threshold_loosens_with_more_layers():
    """We take a max over layers, so the null needs a multiple-comparisons correction."""
    few = P.leakage_threshold(0.333, n_val=90, n_layers=4)
    many = P.leakage_threshold(0.333, n_val=90, n_layers=80)
    assert many > few


def test_small_val_set_does_not_trigger_false_leak():
    """The exact case the dry run surfaced: control at 0.50, chance 0.33, n_val 24."""
    accs = np.linspace(0.3, 0.9, 8)
    ctrl = np.full(8, 0.50)
    v = P.verdict({"k": accs}, {"k": ctrl}, {"k": 0.333}, {"k": 24})
    assert "leakage" not in v["k"]["verdict"]


def test_large_val_set_does_trigger_real_leak():
    accs = np.linspace(0.3, 0.9, 8)
    ctrl = np.full(8, 0.50)
    v = P.verdict({"k": accs}, {"k": ctrl}, {"k": 0.333}, {"k": 600})
    assert "leakage" in v["k"]["verdict"]


# ---------------------------------------------------------------------------------
# Overall verdict (regression: any(PASS) let one good probe mask a leaky split)
# ---------------------------------------------------------------------------------

def _v(label):
    return {"verdict": label}


def test_overall_verdict_rejects_when_any_key_leaks():
    ok, why = P.overall_verdict({
        "a": _v("PASS"),
        "b": _v("FAIL (control task above chance -> leakage across split)"),
    })
    assert ok is False and "leakage" in why


def test_overall_verdict_requires_at_least_one_pass():
    ok, _ = P.overall_verdict({"a": _v("PARTIAL (weak accuracy)"), "b": _v("FAIL")})
    assert ok is False


def test_overall_verdict_accepts_clean_pass():
    ok, why = P.overall_verdict({"a": _v("PASS"), "b": _v("PARTIAL (weak accuracy)")})
    assert ok is True and "a" in why


# ---------------------------------------------------------------------------------
# JSON extraction (regression: bare json.loads died on empty/fenced content)
# ---------------------------------------------------------------------------------

def test_extract_json_handles_provider_variation():
    from src.gen_data import _extract_json

    assert _extract_json('{"turns": []}') == {"turns": []}
    assert _extract_json('```json\n{"turns": []}\n```') == {"turns": []}
    assert _extract_json('Here you go:\n{"turns": []}\nHope that helps') == {"turns": []}
    # braces inside strings must not confuse the balanced scan
    assert _extract_json('{"a": "use {} here"}') == {"a": "use {} here"}
    assert _extract_json('{"a": "close } brace"}') == {"a": "close } brace"}


@pytest.mark.parametrize("bad", ["", "   ", None, "no json at all"])
def test_extract_json_raises_on_unusable(bad):
    from src.gen_data import _extract_json

    with pytest.raises(ValueError):
        _extract_json(bad)
