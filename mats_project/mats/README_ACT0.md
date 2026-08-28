# Act 0 — Hardware and How to Run

## GPU requirements

Act 0 is **forward passes only** — no generation, no training, no KV cache to speak of.
Sequences are short (2–4 turn conversations, ~200–600 tokens). It is the cheapest act in
the project.

### Memory arithmetic (Qwen3-8B, bf16)

| Item | Size |
|---|---|
| Weights (~8.2B params × 2 bytes) | ~16.4 GB |
| Hidden states, batch 8 × seq 512 × 37 layers × 4096 dim × 2 bytes | ~1.3 GB |
| Activations + workspace | ~2 GB |
| **Peak** | **~20 GB** |

### What to rent

| GPU | VRAM | Act 0 | Whole project | Notes |
|---|---|---|---|---|
| RTX 4090 / A5000 / L4 | 24 GB | ✅ comfortable | ⚠️ tight in Act 2 | Fine for 8B forward passes. Batch 4–8. Act 2 generation + MMLU eval will make you manage memory. |
| **A40 / L40S / A6000** | **48 GB** | ✅ | ✅ **recommended** | The sweet spot. Headroom for Qwen3-14B later, and for Act 2 batched generation without thinking about it. |
| A100 | 40 / 80 GB | ✅ | ✅ | Faster, more expensive. Overkill for Act 0. |
| H100 | 80 GB | ✅ | ✅ | Overkill. Only worth it if you want Qwen3-32B. |
| RTX 3090 / 4080 | 16 GB | ⚠️ Qwen3-4B only | ⚠️ | Drop to `Qwen/Qwen3-4B` (~8 GB weights). Acceptable for Act 0, weaker for the project. |

**Recommendation: one A40 or L40S (48 GB) for the whole project.** Act 0 would run on a
4090, but you don't want to re-provision and re-download 16 GB of weights halfway through
Act 2. Rent once, keep it for the 16 hours. Check current RunPod pricing — community-cloud
A40s are usually the cheapest thing that comfortably fits.

**Do not rent multi-GPU.** Nothing here needs it, and `device_map="auto"` sharding across
cards makes forward hooks more annoying in Act 2.

### Other resources

- **Disk**: ~25 GB. Model weights ~16 GB, activation caches ~2 GB, datasets <1 GB. Ask for
  50 GB of container volume.
- **Time on GPU for Act 0**: ~10 minutes of actual compute. The 2-hour budget is data
  generation (API-bound, no GPU), and you reading dialogues.
- **Cost saving**: run `gen` before you start the pod — it only needs OpenRouter, no GPU.

---

## Install

```bash
pip install "transformers>=4.51" torch accelerate scikit-learn matplotlib openai tqdm numpy
```

Qwen3 needs `transformers>=4.51`. Check with `python -c "import transformers; print(transformers.__version__)"`.

```bash
export OPENROUTER_API_KEY=sk-or-...
export GEN_MODEL=anthropic/claude-sonnet-4.5   # verify this ID is current on OpenRouter
export MODEL_ID=Qwen/Qwen3-8B
```

---

## Run

```bash
cd mats

python run_act0.py gen        # ~20 min, API only, no GPU needed
python run_act0.py mathdial   # clones MathDial, prints 20 sampled dialogues to read
python run_act0.py extract    # ~10 min on GPU
python run_act0.py probe      # ~2 min, CPU
python run_act0.py plot       # writes results/figs/act0_probe_accuracy_by_layer.png
```

Or `python run_act0.py all` for everything except `mathdial` (which you must do by hand).

Each step prints the diagnostics the spec requires — model config, rendered chat template,
array shapes, label counts — and appends to `results/runs.jsonl`.

---

## Stop conditions

`run_act0.py extract` will **refuse to run** and print a diagnostic if:
- `len(hidden_states) != num_hidden_layers + 1`
- the rendered chat template contains `<think>`
- the last-token index calculation disagrees with a per-sample unbatched check

These are the three failure modes that silently corrupt everything downstream. Do not
comment out the checks.

`run_act0.py probe` prints the pass/partial/fail verdict against the criterion in
`act0_replication.md` (≥0.80 val accuracy for at least one attribute, control task at
chance, accuracy rising with depth). **Read the verdict before starting Act 1.**
