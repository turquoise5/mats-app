"""Model loading, chat-template rendering, and residual-stream extraction.

Correctness notes (these are the three failure modes that silently corrupt everything):

1. Chat template. Qwen3 emits <think> blocks unless `enable_thinking=False` is passed.
   `render_chat` handles this and `assert_template_sane` verifies it.
2. Read position. We use RIGHT padding and index the last non-pad token per sample.
   With right padding and causal attention, hidden states at real positions are exact,
   because real tokens never attend to padding that follows them. (Left padding would
   require passing explicit position_ids, since a plain forward() call defaults
   position_ids to arange and would put real tokens at the wrong RoPE positions.)
3. hidden_states length. `out.hidden_states` has num_hidden_layers + 1 entries; index 0
   is the embedding output, index i is the output of block i.
"""

from __future__ import annotations

import re

import numpy as np
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from . import config as C


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------

def load(model_id: str | None = None, dtype=torch.bfloat16, device_map="cuda"):
    model_id = model_id or C.MODEL_ID
    tok = AutoTokenizer.from_pretrained(model_id)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "right"  # see module docstring

    # `dtype` is the current kwarg name. `torch_dtype` still works in transformers 5.x
    # but is explicitly "kept for BC" in the source, so prefer `dtype`.
    model = AutoModelForCausalLM.from_pretrained(
        model_id, dtype=dtype, device_map=device_map
    )
    model.eval()
    return model, tok


def print_env(model, tok) -> dict:
    """Print and return the environment facts the spec requires in the run log."""
    cfg = model.config
    info = {
        "model_id": getattr(cfg, "_name_or_path", None) or type(model).__name__,
        "num_hidden_layers": cfg.num_hidden_layers,
        "hidden_size": cfg.hidden_size,
        "dtype": str(next(model.parameters()).dtype),
        "gpu": torch.cuda.get_device_name() if torch.cuda.is_available() else "cpu",
    }
    if torch.cuda.is_available():
        free, total = torch.cuda.mem_get_info()
        info["vram_free_gb"] = round(free / 1e9, 2)
        info["vram_total_gb"] = round(total / 1e9, 2)
    C.banner("ENVIRONMENT")
    for k, v in info.items():
        print(f"  {k:22s} {v}")
    return info


# --------------------------------------------------------------------------------------
# Chat template
# --------------------------------------------------------------------------------------

def render_chat(tok, turns: list[dict], elicit_prefix: str | None = None) -> str:
    """Render a conversation to a string, ending at the start of the assistant turn.

    turns: [{"role": "user"|"assistant", "content": str}], must end with a user turn.
    elicit_prefix: if given, appended after the generation prompt so the final token is
        the last token of the prefix (TalkTuner-style elicitation).
    """
    if not turns or turns[-1]["role"] != "user":
        raise ValueError("conversation must end with a user turn")

    try:
        text = tok.apply_chat_template(
            turns, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
    except TypeError:
        # Template does not accept enable_thinking (non-Qwen models).
        text = tok.apply_chat_template(
            turns, tokenize=False, add_generation_prompt=True
        )

    if elicit_prefix:
        text = text + elicit_prefix
    return text


def assert_template_sane(tok) -> str:
    """Render one toy conversation, print it verbatim, and fail loudly on <think>."""
    toy = [
        {"role": "user", "content": "how do i add fractions"},
        {"role": "assistant", "content": "You need a common denominator first."},
        {"role": "user", "content": "ok but what if the bottoms are different"},
    ]
    natural = render_chat(tok, toy)
    elicited = render_chat(tok, toy, "I think the education level of this user is")

    C.banner("CHAT TEMPLATE (verify by eye before trusting any activations)")
    print("--- natural ---")
    print(repr(natural))
    print("\n--- elicited ---")
    print(repr(elicited))

    # Qwen3's enable_thinking=False mechanism does not omit the <think> tag: per its
    # chat template, it inserts an empty, pre-closed `<think>\n\n</think>\n\n` stub
    # right after the generation prompt, which is how the model is prevented from
    # emitting real reasoning. That stub is expected and harmless -- the read position
    # lands right after `</think>`, not inside reasoning content. What must never
    # happen is a *non-empty* think block (real reasoning text) or an unbalanced tag.
    think_blocks = re.findall(r"<think>(.*?)</think>", natural, flags=re.DOTALL)
    if any(block.strip() for block in think_blocks):
        raise RuntimeError(
            "Rendered template contains a <think> block with non-empty reasoning "
            "content. The read position will land inside a reasoning block. "
            "Fix before proceeding."
        )
    if natural.count("<think>") != natural.count("</think>"):
        raise RuntimeError(
            "Unbalanced <think>/</think> tags in rendered template. Inspect and fix."
        )
    if not elicited.endswith("user is"):
        raise RuntimeError(
            "Elicitation prefix is not at the end of the rendered string. The template "
            "may append tokens after the generation prompt. Inspect and fix."
        )
    return natural


# --------------------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------------------

@torch.no_grad()
def last_token_hidden(
    model, tok, texts: list[str], batch_size: int = 8, max_length: int = 1024
) -> np.ndarray:
    """Residual stream at the final real token, all layers.

    Returns float32 array of shape (n_texts, num_hidden_layers + 1, hidden_size).
    """
    device = next(model.parameters()).device
    n_layers = model.config.num_hidden_layers
    out_chunks = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        enc = tok(
            batch,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=max_length,
            add_special_tokens=False,  # chat template already added them
        ).to(device)

        out = model(**enc, output_hidden_states=True, use_cache=False)
        hs = out.hidden_states

        if len(hs) != n_layers + 1:
            raise RuntimeError(
                f"hidden_states has {len(hs)} entries, expected {n_layers + 1}. "
                "Layer indexing assumptions are wrong; stop and investigate."
            )

        last_idx = enc["attention_mask"].sum(dim=1) - 1  # (B,)
        rows = torch.arange(len(batch), device=device)
        # (B, n_layers+1, d) without materialising the full (B, L, T, d) stack
        picked = torch.stack([h[rows, last_idx] for h in hs], dim=1)
        out_chunks.append(picked.float().cpu().numpy())

        if i == 0:
            print(
                f"  [extract] batch0 seq_len={enc['input_ids'].shape[1]} "
                f"last_idx={last_idx.tolist()} picked={tuple(picked.shape)}"
            )

    acts = np.concatenate(out_chunks, axis=0)
    print(f"  [extract] final array shape {acts.shape} dtype {acts.dtype}")
    return acts


@torch.no_grad()
def verify_last_token_indexing(model, tok, texts: list[str], n_check: int = 3) -> None:
    """Cross-check batched extraction against unbatched, one sample at a time.

    Catches padding-side and index bugs. Any mismatch invalidates every downstream result.
    """
    sub = texts[:n_check]
    batched = last_token_hidden(model, tok, sub, batch_size=len(sub))
    single = np.concatenate(
        [last_token_hidden(model, tok, [t], batch_size=1) for t in sub], axis=0
    )
    max_diff = float(np.abs(batched - single).max())
    rel = max_diff / (float(np.abs(single).max()) + 1e-8)
    print(f"  [verify] batched vs unbatched max abs diff = {max_diff:.4g} (rel {rel:.2e})")
    if rel > 1e-2:
        raise RuntimeError(
            "Batched and unbatched extraction disagree. Padding side or last-token "
            "indexing is wrong. Stop and fix before extracting the full dataset."
        )
    print("  [verify] OK")
