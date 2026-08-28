# 00 — Shared Context

**Read this before any act document. Every act assumes this file.**

---

## 1. The claim we are testing

> The model's inferred model of *what the user knows* — not what the user literally
> said — gates what it tells them.

Two behavioural readouts of "what it tells them":

- **Omission**: when teaching, does it explain concept `C` or silently assume it?
- **Correction**: when the user asserts something false, does it push back or defer?

The project runs in three acts:

| Act | Question | Status in the narrative |
|---|---|---|
| 0 | Does a user-model representation exist at all in a *modern* model? | Replication / sanity |
| 1 | Is it a knowledge model or a style model? Per-concept or one dial? | Structural core |
| 2 | Does steering it change omission and correction? | **Headline** |
| 3 | Does it update rationally across turns? Ignorance vs misconception? | Suggestive extensions |

A negative result in Act 1 is a **legitimate and publishable outcome**, not a failure.
If the representation turns out to be a register/formality direction wearing a costume,
that is the write-up. Do not try to rescue a positive result.

---

## 2. What the probe has to prove

It is trivially true that information about the user's knowledge is present in the user's
text. That is not the claim. An LLM judge can obviously read it; so can TF-IDF, probably.

The probe earns the name "user model" only if it shows:

1. **Cross-register transfer** — trained on one writing register, works on the other.
2. **Per-concept specificity** — "knows modular arithmetic" ≠ "knows induction".
3. **Separability from the output plan** — it is not just "I am about to write an explanation".
4. **Causal use beyond prompting** — steering it moves behaviour more/differently than a
   system-prompt instruction does.

Any one of these failing is an interesting result. Report it as such.

---

## 3. Prior work (do not reinvent; do read)

| Ref | What it did | Why it matters here |
|---|---|---|
| arXiv **2406.07882** (Chen et al., TalkTuner) | Linear probes recover user age/gender/education/SES from LLaMA-2-13B activations; "control probes" steer them | Direct parent. PDF is in the project files. Note their reading-probe vs control-probe distinction, and the finding that mid layers know the attribute but late layers override it. |
| arXiv **2402.18496** (Zhu et al., ICML 2024) | Linearly decodes *belief status of self and others* in ToM narratives; steering top-K attention heads changes ToM performance; random directions do ~nothing | Closest neighbour. "Model represents another agent's beliefs, causally" is already done — for narrative third parties, not the live interlocutor. Code: github.com/Walter0807/RepBelief |
| arXiv **2406.17513** (Bortoletto et al., Brittle Minds) | Systematic probing of belief representations with **control tasks** to rule out confounds; representations are prompt-fragile | Methodology bar. Copy their control-task discipline. |
| arXiv **2305.14536** (Macina et al., MathDial) | 2,861 tutor-student math dialogues, real teachers + LLM student seeded with real GSM8K confusions | Naturalistic eval set. github.com/eth-nlped/mathdial |
| Eedi "Mining Misconceptions in Mathematics" (Kaggle 2024) | Math MCQs where each distractor maps to a **named misconception** | Ground-truth per-concept knowledge-state labels. |

---

## 4. Environment

- **Compute**: RunPod GPU pod, JupyterLab in tmux, persistent kernel.
- **Model under study**: `Qwen/Qwen3-8B` (primary). Fall back to `Qwen/Qwen3-4B` if VRAM
  is tight. Optional second family for cross-model generalisation: `google/gemma-3-12b-it`.
  **Do not use GPT-2, Pythia, LLaMA-2, or Gemma-2.** Old models are an explicit disqualifier
  for this application.
- **Activation access**: plain HuggingFace `transformers`. Read with
  `output_hidden_states=True`; steer with forward hooks on `model.model.layers[L]`.
  TransformerLens / nnsight are optional — do not spend time making them work if HF hooks suffice.
- **Data generation / judging**: OpenRouter via the OpenAI SDK, with
  `response_format={"type": "json_object"}` for anything structured.
- **Probes**: `sklearn.linear_model.LogisticRegression`, L2, with `StandardScaler`.
  No deep probes. If a linear probe fails, that is the finding.

### Qwen3 gotchas to verify before trusting any activations

- `tokenizer.apply_chat_template(..., enable_thinking=False)` — Qwen3 emits `<think>`
  blocks by default. Verify the rendered string with `print(repr(...))` **once** and paste
  it into the run log before extracting anything.
- Confirm `len(outputs.hidden_states)` == `n_layers + 1` (index 0 is the embedding output).
  Print `model.config` (`num_hidden_layers`, `hidden_size`) rather than assuming.
- Left-padding for batched generation; confirm which index is the true last token.

---

## 5. Directory layout

```
mats/
  data/
    raw/            # downloaded MathDial, Eedi
    contrast/       # the 2x3 contrast set built in Act 1
  acts/             # these documents
  src/              # reusable .py modules — NOT notebook-only code
    model.py        # load, chat-template, hidden-state extraction
    probes.py       # train/eval probes, control tasks
    steering.py     # hooks, alpha sweeps
    readouts.py     # omission + correction measures
  cache/            # .npy activation caches (gitignored, big)
  results/
    runs.jsonl      # append-only log, one JSON per experiment
    figs/
  notes.md          # running hypothesis log
```

**Every experiment appends one line to `results/runs.jsonl`** with: timestamp, act,
experiment name, model, layer(s), n samples, seed, metric names + values, and the git
hash or a hash of the config dict. No exceptions. Results that are not in the log did
not happen.

---

## 6. Rules for the agent

These are non-negotiable. Violating them invalidates the work.

1. **Never report a number you did not compute.** No illustrative, plausible, or
   placeholder values. If something did not run, say it did not run.
2. **Print shapes and a sample.** Before training any probe: print the activation array
   shape, the label distribution, and 2 decoded example inputs. Paste them in the output.
3. **Save raw arrays, not just metrics.** Every probe run saves activations (or a path to
   the cache), labels, and fitted coefficients, so results can be re-derived without re-running the model.
4. **Do not tune the treatment harder than the baseline.** If you sweep 8
   hyperparameters for the steering vector, sweep 8 for the random-direction baseline too.
5. **Flag surprises, do not smooth them.** Accuracy of 0.99 on a hard task means a leak.
   Stop and report rather than continuing.
6. **Fix and record the seed** for every split, every generation call, every sample.
7. **One question per cell run.** Do not chain five experiments and report a summary.
8. **When a spec is ambiguous, ask.** Do not pick an interpretation silently.
9. **Do not delete or overwrite `cache/` or `results/`.** Append only.

---

## 7. Shared definitions

- **Concept `C`** — a named, atomic piece of math knowledge (e.g. "converting a mixed
  number to an improper fraction"). Drawn from the Eedi misconception taxonomy.
- **Knowledge state** — one of `mastery` / `gap` / `misconception:M` / `undisclosed`.
- **Register** — surface writing style of the user turn, one of `novice` / `expert`,
  defined by capitalisation, hedging, terminology, punctuation, message length.
  **Register is orthogonal to knowledge state by construction** (see Act 1).
- **Read position** — where in the sequence the residual stream is sampled. Two are used
  throughout: `natural` (last token of the user turn, before generation) and
  `elicited` (last token of an appended assistant prefix, TalkTuner-style).

---

## 8. Time budget

16 hours of work + 2 hours write-up. Hard checkpoint at **hour 8** (end of Act 1).

| Act | Budget |
|---|---|
| 0 | 2h |
| 1 | 6h |
| 2 | 6h |
| 3 | 2h (1h per extension, hard stop) |
| Write-up | 2h |

If Act 2 has not started by hour 9, cut Act 3 entirely.
