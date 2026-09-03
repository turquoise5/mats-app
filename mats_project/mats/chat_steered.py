#!/usr/bin/env python
"""Interactive chat with Qwen3-8B under live activation steering.

    python chat_steered.py

This needs a real terminal (stdin/stdout) -- run it directly, not through an agent.
Loads the model once, refits the D0 probe direction (same one from run_steering.py:
merged split, natural position, layer 20), and drops you into a chat loop where you can
dial the steering in and out mid-conversation.

Commands (anything else is sent as your next chat message):
  /help                     show this list
  /status                   current vector / alpha / layer
  /frac <float>             set alpha as a fraction of the hooked layer's mean
                            activation norm (the calibration used throughout notes.md --
                            e.g. 0.15 is a strong, still-fluent, still-graded push;
                            beyond ~1.0 things saturate; beyond ~2.0 it's gibberish)
  /alpha <float>            set alpha directly in raw activation units instead
  /vector probe|dom|random|off
                            probe = the LogisticRegression direction; dom = difference-
                            of-means (mastery - gap); random = a fresh random direction
                            (reseed with /reseed); off = unhooked, exactly baseline
  /layer <int>              which decoder layer to hook (0-35). The probe/dom vectors
                            were fit AT layer 20 -- hooking a different layer with them
                            is exactly Act 2's "random_layer" diagnostic (does the same
                            direction still do anything somewhere uninformative?).
                            Alpha is recalibrated against *that* layer's own mean norm.
  /reseed                   draw a new random direction (only matters for /vector random)
  /load knows|gap|<row_id>  replace the conversation with a real demonstrated-subset
                            row's turns (knows/gap picks one at random from the test
                            split) and continue chatting from there
  /reset                    clear the conversation
  /quit, /exit
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_v] = "1"

import random
import sys

import numpy as np
import torch

from run_ablation_probes import CACHE_PREFIX, _balanced_probe, load_variant_rows, merged_split
from src import config as C
from src import model as M
from src import steering as S

LAYER0 = 20  # where the probe/dom vectors were fit (natural position, merged split)
MAX_NEW_TOKENS = 200


def banner(text):
    print("\n" + "-" * 72)
    print(text)
    print("-" * 72)


def load_direction_stuff():
    orig_rows = load_variant_rows("orig")
    labels = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig_rows])
    tr, te = merged_split(orig_rows, labels)

    acts = np.load(C.CACHE / f"{CACHE_PREFIX}_orig_natural.npy")  # (816, 37, 4096)
    X20 = acts[:, LAYER0, :]
    probe = _balanced_probe(C.SEED)
    probe.fit(X20[tr], labels[tr])
    probe_vec = S.probe_direction(probe)
    dom_vec = S.diff_of_means_direction(X20[tr], labels[tr])

    # per-layer mean activation norm, for calibrating alpha at whichever layer is hooked
    layer_norms = {L: float(np.linalg.norm(acts[:, L, :], axis=1).mean()) for L in range(acts.shape[1])}

    return orig_rows, labels, te, probe_vec, dom_vec, layer_norms


def main():
    C.banner("CHAT_STEERED -- loading model and D0 direction")
    orig_rows, labels, te, probe_vec, dom_vec, layer_norms = load_direction_stuff()
    te_rows = [orig_rows[i] for i in te]
    print(f"[chat_steered] probe fit at layer {LAYER0}. "
          f"cos(probe, diff-of-means) = "
          f"{torch.nn.functional.cosine_similarity(probe_vec, dom_vec, dim=0):.4f}")

    mdl, tok = M.load()
    M.assert_template_sane(tok)
    device = next(mdl.parameters()).device
    dtype = next(mdl.parameters()).dtype

    state = {"vector": "off", "alpha_frac": 0.0, "layer": LAYER0, "seed": 0}
    rand_vec = S.random_direction(probe_vec.shape[0], seed=state["seed"])
    handle = None
    turns: list[dict] = []

    def vector_for(name):
        return {"probe": probe_vec, "dom": dom_vec, "random": rand_vec}.get(name)

    def apply_hook():
        nonlocal handle
        if handle is not None:
            handle.remove()
            handle = None
        if state["vector"] == "off" or state["alpha_frac"] == 0.0:
            return
        vec = vector_for(state["vector"])
        alpha = state["alpha_frac"] * layer_norms[state["layer"]]
        handle = S.register_steering(mdl, state["layer"], vec, alpha=alpha)

    def status():
        alpha = state["alpha_frac"] * layer_norms[state["layer"]]
        print(f"  vector={state['vector']}  alpha_frac={state['alpha_frac']:+.3f}  "
              f"alpha={alpha:+.2f}  layer={state['layer']} "
              f"(mean norm at this layer: {layer_norms[state['layer']]:.2f})")

    @torch.no_grad()
    def generate_reply():
        text = M.render_chat(tok, turns)
        enc = tok(text, return_tensors="pt", add_special_tokens=False).to(device)
        gen = mdl.generate(
            **enc, max_new_tokens=MAX_NEW_TOKENS, do_sample=False,
            pad_token_id=tok.pad_token_id,
        )
        new_tokens = gen[:, enc["input_ids"].shape[1]:]
        return tok.decode(new_tokens[0], skip_special_tokens=True).strip()

    print(__doc__)
    status()
    print("\nType a message, or a /command. /help to see this again.\n")

    try:
        while True:
            try:
                line = input("you> ").strip()
            except EOFError:
                break
            if not line:
                continue

            if line in ("/quit", "/exit"):
                break
            if line == "/help":
                print(__doc__)
                continue
            if line == "/status":
                status()
                continue
            if line == "/reset":
                turns = []
                print("[chat_steered] conversation cleared.")
                continue
            if line == "/reseed":
                state["seed"] += 1
                rand_vec = S.random_direction(probe_vec.shape[0], seed=state["seed"])
                print(f"[chat_steered] new random direction drawn (seed={state['seed']}).")
                apply_hook()
                continue
            if line.startswith("/frac "):
                state["alpha_frac"] = float(line.split(maxsplit=1)[1])
                apply_hook()
                status()
                continue
            if line.startswith("/alpha "):
                raw = float(line.split(maxsplit=1)[1])
                state["alpha_frac"] = raw / layer_norms[state["layer"]]
                apply_hook()
                status()
                continue
            if line.startswith("/vector "):
                v = line.split(maxsplit=1)[1].strip()
                if v not in ("probe", "dom", "random", "off"):
                    print("  vector must be one of: probe, dom, random, off")
                    continue
                state["vector"] = v
                apply_hook()
                status()
                continue
            if line.startswith("/layer "):
                state["layer"] = int(line.split(maxsplit=1)[1])
                apply_hook()
                status()
                continue
            if line.startswith("/load "):
                arg = line.split(maxsplit=1)[1].strip()
                if arg in ("knows", "gap"):
                    y = 1 if arg == "knows" else 0
                    pool = [r for r, lab in zip(te_rows, labels[te]) if lab == y]
                    row = random.choice(pool)
                else:
                    matches = [r for r in orig_rows if r["id"] == arg]
                    if not matches:
                        print(f"  no row with id {arg!r}")
                        continue
                    row = matches[0]
                turns = [dict(t) for t in row["turns"]]
                print(f"[chat_steered] loaded {row['id']} "
                      f"(concept={row['concept_slug']}, knowledge_state={row['knowledge_state']}, "
                      f"register={row['register']})")
                for t in turns:
                    print(f"  [{t['role']}] {t['content']}")
                continue

            if line.startswith("/"):
                print(f"  unknown command: {line}")
                continue

            turns.append({"role": "user", "content": line})
            reply = generate_reply()
            turns.append({"role": "assistant", "content": reply})
            print(f"model> {reply}")
    except KeyboardInterrupt:
        pass
    finally:
        if handle is not None:
            handle.remove()
    print("\n[chat_steered] bye.")


if __name__ == "__main__":
    main()
