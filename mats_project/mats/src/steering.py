"""Activation steering via a forward hook on one decoder layer (act2_causal.md Task 2.2).

Adds `alpha * unit_vector` to the residual stream output of `model.model.layers[L]`, on
every position, on every forward call -- which includes each new token during
`model.generate`, so "applied throughout generation, re-applied at each step" falls out
of `register_forward_hook` for free; no per-step bookkeeping needed.
"""

from __future__ import annotations

import torch


def make_hook(vector: torch.Tensor, alpha: float, dtype=None, device=None):
    """`vector` need not be unit-norm -- normalised here. `alpha` is in raw residual-
    stream units (same scale as the layer's typical activation norm), not "normalised
    units" -- see run_steering.py's calibration against the measured per-layer norm."""
    v = vector.detach().clone()
    if dtype is not None or device is not None:
        v = v.to(dtype=dtype, device=device)
    v = v / v.norm()

    def hook(module, inputs, output):
        is_tuple = isinstance(output, tuple)
        h = output[0] if is_tuple else output
        h = h + alpha * v.to(dtype=h.dtype, device=h.device)
        return (h,) + output[1:] if is_tuple else h

    return hook


def register_steering(model, layer: int, vector: torch.Tensor, alpha: float):
    """Returns a handle; call `.remove()` to un-hook."""
    dtype = next(model.parameters()).dtype
    device = next(model.parameters()).device
    hook = make_hook(vector, alpha, dtype=dtype, device=device)
    return model.model.layers[layer].register_forward_hook(hook)


def probe_direction(pipe) -> "torch.Tensor":
    """Un-scale a fitted StandardScaler+LogisticRegression pipeline's weight vector back
    to raw activation space (StandardScaler divides by sigma at inference, so the
    direction in raw space is w / sigma). Positive along this direction moves the
    decision function toward class 1 ("knows"). Returns a torch float32 vector."""
    import numpy as np

    scaler = pipe.named_steps["standardscaler"]
    clf = pipe.named_steps["logisticregression"]
    w = clf.coef_[0] / scaler.scale_
    return torch.tensor(np.asarray(w), dtype=torch.float32)


def diff_of_means_direction(acts_layer, labels) -> "torch.Tensor":
    """mean(knows) - mean(gap), in raw activation space. `acts_layer`: (n, d).
    `labels`: (n,) with 1=knows, 0=gap."""
    import numpy as np

    acts_layer = np.asarray(acts_layer)
    labels = np.asarray(labels)
    v = acts_layer[labels == 1].mean(axis=0) - acts_layer[labels == 0].mean(axis=0)
    return torch.tensor(v, dtype=torch.float32)


def random_direction(dim: int, seed: int) -> "torch.Tensor":
    import numpy as np

    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim)
    return torch.tensor(v, dtype=torch.float32)
