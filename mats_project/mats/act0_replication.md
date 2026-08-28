# Act 0 — Replication and Data Contact

**Budget: 2 hours. Read `00_CONTEXT.md` first.**

---

## Purpose

Two things, both cheap, both load-bearing.

1. **Replication.** TalkTuner (arXiv 2406.07882) found user-attribute probes in
   LLaMA-2-13B. We need one sentence in the write-up: *"the phenomenon replicates in my
   setting"* — modern model, our extraction code, our probe code. Building on an effect
   without checking it replicates in your own setup is a named failure mode. Everything
   downstream is noise if this fails.
2. **Data contact.** Read actual dialogues by hand before designing anything. This is not
   optional busywork; the design of Act 1 depends on what real confusion looks like.

If Act 0 fails, **stop and report** — do not proceed to Act 1.

---

## Task 0.1 — Environment and model (20 min)

```python
# src/model.py
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

MODEL_ID = "Qwen/Qwen3-8B"

def load(model_id=MODEL_ID):
    tok = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(
        model_id, torch_dtype=torch.bfloat16, device_map="cuda"
    )
    model.eval()
    return model, tok
```

**Print and paste into the run log:**
- `model.config.num_hidden_layers`, `model.config.hidden_size`
- `torch.cuda.get_device_name()`, free VRAM after load
- The full rendered chat template for one toy conversation, via
  `print(repr(tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)))`
- `len(out.hidden_states)` for one forward pass, and confirm it equals `n_layers + 1`

Do not proceed until the rendered template has been eyeballed. A wrong template silently
corrupts every activation in the project.

---

## Task 0.2 — Synthetic conversations (30 min)

Reproduce a *small* version of TalkTuner's dataset. Do not rebuild their full 14k
conversations — we need enough to show the effect, not to beat it.

- **Attributes**: `education` (some schooling / high school / college+) and
  `age` (child / adolescent / adult / older adult). Two attributes only. Skip gender and
  SES here; they cost time and are not on our critical path.
- **Volume**: 150 conversations per subcategory. So 450 for education, 600 for age.
- **Generator**: OpenRouter, a mid-size instruct model, `temperature=1.0` for topic
  diversity, `response_format={"type": "json_object"}`.
- **Format**: 2–4 turn conversations. The attribute should be reflected **implicitly**
  in roughly half the samples and explicitly in the rest — mirror TalkTuner's split.
- Save to `data/raw/talktuner_repro.jsonl`, one JSON per line:
  `{"id", "attribute", "subcategory", "turns": [{"role","content"}], "explicit": bool, "seed"}`

**Deduplicate.** Check for exact and near-duplicate first turns; report the count removed.

---

## Task 0.3 — Extract activations (20 min)

Both read positions (see `00_CONTEXT.md` §7):

- **`elicited`** — append an assistant message that begins
  `"I think the {attribute} of this user is"` and take the residual stream at the final
  token. This is TalkTuner's setup; use it for the replication claim.
- **`natural`** — the last token of the final user turn, with `add_generation_prompt=True`,
  no elicitation prefix. This is what we actually care about in Acts 1–2, so extract it now
  and get the comparison for free.

Cache as `cache/act0_{attribute}_{position}.npy`, shape `(n_samples, n_layers+1, d_model)`,
`float32`. Save the label array alongside as `.npy` and the id list as `.json`.

**Print the array shape and the label counts before moving on.**

---

## Task 0.4 — Per-layer probes (25 min)

```python
# src/probes.py — sketch
def fit_layer_probes(acts, labels, groups=None, seed=0):
    """acts: (n, n_layers+1, d). Returns per-layer val accuracy + fitted probes."""
    # StandardScaler -> LogisticRegression(penalty='l2', C=1.0, max_iter=2000)
    # 80/20 split, stratified, grouped by conversation id if any conv appears twice
    # one-vs-rest for multiclass
```

Requirements:
- Stratified 80/20 split, fixed seed.
- **Control task**: refit with labels shuffled within the training set. A probe that gets
  meaningfully above chance on shuffled labels means a leak (usually duplicated samples
  across the split). Report both curves on the same axes.
- **Majority-class baseline** on the plot as a horizontal line.

**Deliverable:** `results/figs/act0_probe_accuracy_by_layer.png` — x = layer, y = validation
accuracy, one line per attribute per read position, plus control-task and chance lines.

---

## Success criterion

**Pass** if at least one attribute reaches **≥0.80** validation accuracy at some layer,
with the control task at ~chance, and with accuracy **rising with depth** (TalkTuner's
signature — it indicates the probe is not just reading surface text from early layers).

**Partial pass** — good accuracy but flat across layers. Report it; it weakens the
"abstraction" story and should be stated plainly in the write-up.

**Fail** — under 0.70 everywhere, or the control task tracks the real probe. Stop.
Diagnose in this order: (1) chat template rendering, (2) read-position indexing, (3)
train/val leakage from duplicate generations, (4) label noise in the synthetic data.

---

## Task 0.5 — Read the data (25 min, do not skip)

```bash
git clone https://github.com/eth-nlped/mathdial data/raw/mathdial
```

Then, by hand:

1. Read **20 randomly sampled** MathDial dialogues end to end. Not cherry-picked — sample
   with a fixed seed and read what comes up.
2. For each, note in `notes.md`: what did the student actually not understand? Was it a
   named concept, a procedural slip, or a reading-comprehension failure? Did the teacher's
   first move reveal an inference about the student's knowledge?
3. Then: paste 5 of these dialogues into the model under study and ask it to tutor. **Read
   its responses.** Where does it assume knowledge? Where does it over-explain?
4. Write 3–5 sentences of impressions. Specifically: **does the model behave as if it has
   a model of this student?** If it explains everything at the same depth regardless of
   who it is talking to, that is a red flag for the whole project and needs to be known now.

Also download the Eedi Kaggle dataset ("Eedi - Mining Misconceptions in Mathematics") and
print the misconception taxonomy — the list of named misconceptions is the input to Act 1.

---

## Outputs of Act 0

- [ ] `src/model.py`, `src/probes.py` working and importable
- [ ] `data/raw/talktuner_repro.jsonl`, `data/raw/mathdial/`, Eedi misconception list
- [ ] `cache/act0_*.npy`
- [ ] `results/figs/act0_probe_accuracy_by_layer.png`
- [ ] Entries in `results/runs.jsonl`
- [ ] Hand-written impressions in `notes.md`
- [ ] A stated pass/partial/fail verdict on the replication

**Report back before starting Act 1.**
