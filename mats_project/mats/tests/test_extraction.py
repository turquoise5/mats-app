"""Tests for src/model.py using a tiny random Qwen3 on CPU. No downloads, no GPU.

These cover the three failure modes that silently corrupt every downstream result:
chat template rendering, last-token indexing, and hidden_states layer count.
"""

from __future__ import annotations

import numpy as np
import pytest

from src import model as M
from tests.stub import tiny_setup

TURNS = [
    {"role": "user", "content": "how do i add fractions"},
    {"role": "assistant", "content": "You need a common denominator first."},
    {"role": "user", "content": "ok but what if the bottoms are different"},
]
PREFIX = "I think the education level of this user is"


# ---------------------------------------------------------------------------------
# Chat template
# ---------------------------------------------------------------------------------

def test_render_chat_ends_with_generation_prompt():
    _, tok = tiny_setup()
    text = M.render_chat(tok, TURNS)
    assert text.endswith("<|im_start|>assistant\n")


def test_render_chat_appends_elicitation_prefix_last():
    _, tok = tiny_setup()
    text = M.render_chat(tok, TURNS, PREFIX)
    assert text.endswith(PREFIX), "prefix must be the final content in the sequence"


def test_render_chat_rejects_assistant_final_turn():
    _, tok = tiny_setup()
    with pytest.raises(ValueError):
        M.render_chat(tok, TURNS[:2])


def test_render_chat_rejects_empty():
    _, tok = tiny_setup()
    with pytest.raises(ValueError):
        M.render_chat(tok, [])


def test_assert_template_sane_passes_on_clean_tokenizer():
    _, tok = tiny_setup()
    M.assert_template_sane(tok)


def test_assert_template_sane_catches_think_block():
    """The Qwen3 failure: without enable_thinking=False the read position lands
    inside a reasoning block, and every activation is taken from the wrong place."""
    _, tok = tiny_setup(emit_think=True)
    tok.emit_think = True
    # Simulate a template that ignores enable_thinking.
    original = tok.apply_chat_template

    def ignores_flag(turns, tokenize=False, add_generation_prompt=False, **kw):
        return original(turns, tokenize, add_generation_prompt, enable_thinking=None)

    tok.apply_chat_template = ignores_flag
    with pytest.raises(RuntimeError, match="think"):
        M.assert_template_sane(tok)


# ---------------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------------

def test_last_token_hidden_shape():
    model, tok = tiny_setup()
    texts = [M.render_chat(tok, TURNS), M.render_chat(tok, TURNS, PREFIX)]
    acts = M.last_token_hidden(model, tok, texts, batch_size=2)
    assert acts.shape == (2, model.config.num_hidden_layers + 1, model.config.hidden_size)
    assert acts.dtype == np.float32
    assert np.isfinite(acts).all()


def test_batched_matches_unbatched_with_right_padding():
    """Different-length sequences in one batch must not corrupt the read position."""
    model, tok = tiny_setup()
    texts = [
        M.render_chat(tok, [{"role": "user", "content": "short"}]),
        M.render_chat(tok, TURNS),
        M.render_chat(tok, TURNS, PREFIX),
    ]
    batched = M.last_token_hidden(model, tok, texts, batch_size=3)
    single = np.concatenate(
        [M.last_token_hidden(model, tok, [t], batch_size=1) for t in texts], axis=0
    )
    assert np.abs(batched - single).max() / np.abs(single).max() < 1e-3


def test_verify_last_token_indexing_passes_with_right_padding():
    model, tok = tiny_setup()
    texts = [
        M.render_chat(tok, [{"role": "user", "content": "short"}]),
        M.render_chat(tok, TURNS),
        M.render_chat(tok, TURNS, PREFIX),
    ]
    M.verify_last_token_indexing(model, tok, texts)


def test_left_padding_is_caught():
    """Regression guard. A plain forward() defaults position_ids to arange, so with
    LEFT padding real tokens get the wrong RoPE positions and the read is silently
    wrong. If someone flips padding_side, this check must fail loudly."""
    model, tok = tiny_setup()
    tok.padding_side = "left"
    texts = [
        M.render_chat(tok, [{"role": "user", "content": "short"}]),
        M.render_chat(tok, TURNS),
        M.render_chat(tok, TURNS, PREFIX),
    ]
    with pytest.raises(RuntimeError, match="disagree"):
        M.verify_last_token_indexing(model, tok, texts)


def test_layer_count_mismatch_raises(monkeypatch):
    model, tok = tiny_setup()
    monkeypatch.setattr(model.config, "num_hidden_layers", 99)
    with pytest.raises(RuntimeError, match="hidden_states"):
        M.last_token_hidden(model, tok, [M.render_chat(tok, TURNS)], batch_size=1)


def test_print_env_does_not_crash_off_gpu():
    model, tok = tiny_setup()
    info = M.print_env(model, tok)
    assert info["num_hidden_layers"] == model.config.num_hidden_layers
    assert info["hidden_size"] == model.config.hidden_size
