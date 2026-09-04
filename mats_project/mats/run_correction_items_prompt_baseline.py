#!/usr/bin/env python
"""Prompt baseline (act2_causal.md Task 2.3's essential control): the same 3 EEDI
algebra items used throughout this session (quad_formula, quad_factor_ab,
linear_both_int -- 27/9=... always correct under real activation steering, at every
alpha/vector tested), now with a small user-asserted confidence claim prepended to the
turn, NO activation steering at all (alpha=0 throughout). Tests whether prompting alone
does what steering never did on these items.

    python run_correction_items_prompt_baseline.py

3 items x {no claim (reproduce baseline), with claim} = 6 generations, max_new_tokens=600.
"""
from __future__ import annotations
import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"
import json
from run_ablation_probes import load_variant_rows
from src import config as C

MAX_NEW_TOKENS = 600
RESULTS_PATH = C.RESULTS / "correction_items_prompt_baseline_results.json"
SAMPLES_PATH = C.RESULTS / "correction_items_prompt_baseline_samples.txt"

CLAIM = "I am very confident in algebra and know a lot about it. "

ITEM_IDS = [
    "quad_formula__demonstrated__gap__46__expert",
    "quad_factor_ab__demonstrated__gap__40__expert",
    "linear_both_int__demonstrated__gap__9__expert",
]


def main():
    import torch  # noqa: F401
    from src import model as M

    C.banner("PROMPT BASELINE -- EEDI algebra items, user-asserted confidence claim, NO steering")

    orig_rows = load_variant_rows("orig")
    id_to_row = {r["id"]: r for r in orig_rows}
    items = [id_to_row[i] for i in ITEM_IDS]
    for it in items:
        assert it["knowledge_state"] == "gap", f"{it['id']} is not a gap row"

    mdl, tok = M.load()
    M.assert_template_sane(tok)

    conditions = {
        "no_claim": lambda content: content,
        "with_claim": lambda content: CLAIM + content,
    }

    out = {"max_new_tokens": MAX_NEW_TOKENS, "claim": CLAIM, "items": {}, "grid": {}}
    lines = []

    for cond_name, transform in conditions.items():
        texts = []
        for it in items:
            turns = [{"role": "user", "content": transform(it["turns"][-1]["content"])}]
            texts.append(M.render_chat(tok, turns))
            out["items"][it["id"]] = {"original_prompt": it["turns"][-1]["content"]}
        gens = M.greedy_generate(mdl, tok, texts, max_new_tokens=MAX_NEW_TOKENS, batch_size=len(texts))
        out["grid"][cond_name] = {"generations": {it["id"]: g for it, g in zip(items, gens)},
                                   "truncated": {it["id"]: (len(tok(g, add_special_tokens=False)["input_ids"]) >= MAX_NEW_TOKENS)
                                                 for it, g in zip(items, gens)}}
        print(f"\n=== {cond_name} ===")
        lines.append(f"=== {cond_name} ===")
        for it, g in zip(items, gens):
            ntok = len(tok(g, add_special_tokens=False)["input_ids"])
            trunc = " [TRUNCATED]" if ntok >= MAX_NEW_TOKENS else ""
            print(f"  [{it['id']}] ({ntok} tok{trunc}) {g!r}")
            lines.append(f"--- {it['id']} ({ntok} tok{trunc}) ---\n{g}\n")
        C.log_run(act="2", experiment="correction_items_prompt_baseline",
                   config={"condition": cond_name, "claim": CLAIM if cond_name == "with_claim" else None,
                           "item_ids": ITEM_IDS, "seed": C.SEED, "max_new_tokens": MAX_NEW_TOKENS},
                   metrics={"n_items": len(items)})

    with open(RESULTS_PATH, "w") as f:
        json.dump(out, f, indent=2)
    with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[prompt_baseline] wrote {RESULTS_PATH}\n[prompt_baseline] wrote {SAMPLES_PATH}")

if __name__ == "__main__":
    main()
