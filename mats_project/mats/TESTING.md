# Validation

Run these before renting a GPU. Both are CPU-only and need no model download.

```bash
pip install pytest torch transformers scikit-learn numpy matplotlib
python -m pytest tests/ -q          # 31 tests, ~5s
python run_act0.py dryrun           # full extract -> probe -> plot on a fake model
```

## What the tests cover

`tests/test_probes.py` — probe fitting, control task, splits, directions, verdict logic.
`tests/test_extraction.py` — chat rendering and last-token indexing, using a tiny
random-weight Qwen3 and a stub tokenizer (`tests/stub.py`). No GPU, no downloads.

Notable regression tests, each tied to a bug that was actually found:

| Test | Bug it guards |
|---|---|
| `test_control_task_detects_duplicate_leakage` | Row-wise label permutation gave duplicates *different* labels, so memorising an input could not help and the control stayed at chance on a badly leaky split. It detected nothing. |
| `test_control_task_labels_not_correlated_with_truth` | Sharing a seed with whatever produced the true labels made the control labels track them, so a clean run looked like a leak. |
| `test_small_val_set_does_not_trigger_false_leak` | A fixed `chance + 0.10` leakage cutoff is wrong in both directions. Now a Bonferroni-corrected binomial bound on `n_val` and layer count. |
| `test_overall_verdict_rejects_when_any_key_leaks` | `any(PASS)` printed "REPLICATION PASSED" with two leakage FAILs present. |
| `test_left_padding_is_caught` | A plain `forward()` defaults `position_ids` to `arange`, so left padding puts real tokens at the wrong RoPE positions and reads silently wrong values. |
| `test_assert_template_sane_catches_think_block` | Without `enable_thinking=False`, Qwen3 opens a `<think>` block and the read position lands inside it. |

## What the dry run covers

`python run_act0.py dryrun` fabricates **label-free** conversations, runs the real
extraction and probing code against a tiny random model, and then **asserts the probes
come out at chance**. Above-chance accuracy on label-free input means the pipeline is
leaking the label — a wiring bug, not a finding.

## What is still untested

Nothing here touches a real model or a real API. Still unverified:

- Qwen3's actual chat template (the stub only mimics its shape). `assert_template_sane`
  checks this at runtime, in seconds, on first real extraction.
- The OpenRouter generation path in `src/gen_data.py`, and whether `GEN_MODEL` is a live
  model ID. **Verify at openrouter.ai/models before a ~900-call run.**
- The MathDial repo layout (`data/train.csv` is assumed).
- Whether a real model encodes user attributes at all. That is the experiment.

Cheapest shakeout order on the pod:

```bash
N_PER_SUBCATEGORY=2 python run_act0.py gen   # 12 API calls, proves the generation path
rm -rf cache && python run_act0.py extract   # trips template + indexing asserts
python run_act0.py gen                       # the real run
```

## Version notes

Validated against `transformers 5.16.1`, `scikit-learn 1.8.0`, `torch 2.13`.

- `LogisticRegression(multi_class=...)` — removed in scikit-learn 1.7.
- `LogisticRegression(penalty=...)` — deprecated in scikit-learn 1.8.
- `from_pretrained(torch_dtype=...)` — still works in transformers 5.x but marked
  "kept for BC" in the source; the code uses `dtype=`.

Older probing code on the internet uses all three. If an agent reintroduces them from
memory, the tests will not catch it (they use the tiny model path) but the first real
`load()` will fail loudly.
