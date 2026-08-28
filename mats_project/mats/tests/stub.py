"""CPU test doubles: a stub tokenizer and a tiny random-weight Qwen3.

Lets the whole extraction pipeline run on a laptop with no model download and no GPU,
so template rendering and last-token indexing can be validated before renting a pod.

The stub tokenizer deliberately mimics Qwen's chat format (<|im_start|>role\\n ...
<|im_end|>) and RIGHT padding, because those are the two things the real code depends on.
"""

from __future__ import annotations

import torch
from transformers import AutoModelForCausalLM, Qwen3Config


class StubBatch(dict):
    """Minimal stand-in for transformers' BatchEncoding."""

    def to(self, device):
        return StubBatch({k: v.to(device) for k, v in self.items()})


class StubTokenizer:
    """Word-level tokenizer with a Qwen-shaped chat template. Right padding."""

    def __init__(self, vocab_size: int = 512, emit_think: bool = False):
        self.vocab_size = vocab_size
        self.emit_think = emit_think  # set True to simulate the Qwen3 <think> failure
        self._vocab: dict[str, int] = {"<pad>": 0, "<unk>": 1}
        self.pad_token = "<pad>"
        self.eos_token = "<pad>"
        self.padding_side = "right"

    # -- vocab -------------------------------------------------------------------
    def _id(self, word: str) -> int:
        if word not in self._vocab:
            if len(self._vocab) >= self.vocab_size:
                return 1
            self._vocab[word] = len(self._vocab)
        return self._vocab[word]

    # -- chat template -----------------------------------------------------------
    def apply_chat_template(
        self, turns, tokenize=False, add_generation_prompt=False, enable_thinking=None
    ):
        assert tokenize is False, "stub only supports tokenize=False"
        parts = [f"<|im_start|>{t['role']}\n{t['content']}<|im_end|>\n" for t in turns]
        if add_generation_prompt:
            parts.append("<|im_start|>assistant\n")
            # Mirrors real Qwen3: a thinking block appears unless disabled.
            if self.emit_think and enable_thinking is not False:
                parts.append("<think>\n")
        return "".join(parts)

    # -- encoding ----------------------------------------------------------------
    def __call__(
        self, texts, return_tensors="pt", padding=True, truncation=True,
        max_length=1024, add_special_tokens=False,
    ):
        if isinstance(texts, str):
            texts = [texts]
        seqs = [[self._id(w) for w in t.split()][:max_length] for t in texts]
        n = max(len(s) for s in seqs)

        input_ids, attention_mask = [], []
        for s in seqs:
            pad = n - len(s)
            if self.padding_side == "right":
                input_ids.append(s + [0] * pad)
                attention_mask.append([1] * len(s) + [0] * pad)
            else:
                input_ids.append([0] * pad + s)
                attention_mask.append([0] * pad + [1] * len(s))

        return StubBatch(
            input_ids=torch.tensor(input_ids, dtype=torch.long),
            attention_mask=torch.tensor(attention_mask, dtype=torch.long),
        )


def tiny_model(vocab_size: int = 512, n_layers: int = 4, hidden: int = 32):
    """A randomly initialised Qwen3 small enough to run on CPU in milliseconds."""
    cfg = Qwen3Config(
        vocab_size=vocab_size,
        hidden_size=hidden,
        num_hidden_layers=n_layers,
        num_attention_heads=4,
        num_key_value_heads=2,
        head_dim=8,
        intermediate_size=hidden * 2,
        max_position_embeddings=1024,
    )
    model = AutoModelForCausalLM.from_config(cfg)
    model.eval()
    return model


def tiny_setup(**kw):
    tok = StubTokenizer(**{k: v for k, v in kw.items() if k in ("emit_think",)})
    model = tiny_model()
    return model, tok
