# Act 0 — Summary and Verdict

**Status: REPLICATION PASSED (per `act0_replication.md` criterion) — proceed to Act 1,
with one caveat carried forward per attribute below. Read this whole doc before starting
Act 1; the education result is not a clean pass once the TF-IDF baseline is applied.**

Model: `Qwen/Qwen3-8B` (36 layers, hidden 4096, bf16) on one RTX PRO 4500 (33.7 GB).
Data: `data/raw/talktuner_repro.jsonl`, 1018 synthetic conversations (443 education,
575 age), generated per `act0_replication.md` Task 0.2. Full numbers in
`results/runs.jsonl`; raw activations in `cache/act0_*.npy`; figure at
`results/figs/act0_probe_accuracy_by_layer.png`.

---

## 1. Headline numbers

| attribute / position | best val acc | best layer | control max | chance | leakage thresh | rises w/ depth | verdict |
|---|---|---|---|---|---|---|---|
| education / natural   | 0.966 | 12 | 0.337 | 0.337 | 0.487 | yes | PASS |
| education / elicited  | 0.989 | 20 | 0.416 | 0.337 | 0.487 | yes | PASS |
| age / natural          | 0.991 | 27 | 0.304 | 0.252 | 0.374 | yes | PASS |
| age / elicited         | 1.000 | 27 | 0.296 | 0.252 | 0.374 | yes | PASS |

All four clear the ≥0.80 pass bar with the control task at/near chance and accuracy
rising with depth (TalkTuner's signature). By the letter of `act0_replication.md`'s
success criterion, this is an unambiguous PASS: the phenomenon replicates in a modern
model with our extraction and probe code.

That criterion, however, only asks whether *a linear probe finds the attribute at all*.
It says nothing about *where* the signal comes from. Section 2 covers that.

---

## 2. What we did to try to break the result (not just confirm it)

Per `00_CONTEXT.md` rule 5 ("flag surprises, do not smooth them") and the project's own
warning that a probe finding an attribute is not by itself interesting — text trivially
carries this information — every positive number above was put through a falsification
attempt before being trusted. In order of how hard each one is to pass:

1. **Pipeline wiring (dry run) — not run this session.** `run_act0.py dryrun` exists
   specifically to exercise the full extract → probe → plot chain on label-free synthetic
   text and assert everything comes out at chance, catching label leakage in the code
   itself rather than in the model. `results/runs.jsonl` has no `dryrun` entry — it was
   not executed as part of this run. Listed here as a check that exists and is still
   worth running, not one we can claim passed.

2. **Chat-template sanity check.** `assert_template_sane` renders a toy conversation and
   fails loudly if the read position could land inside real reasoning content. It
   originally flagged Qwen3's `enable_thinking=False` output as a failure, because that
   mode inserts an empty, pre-closed `<think>\n\n</think>\n\n` stub rather than omitting
   the tag — confirmed directly from the tokenizer's chat template source. The check was
   tightened to fail only on *non-empty* reasoning content or unbalanced tags, not on the
   empty stub. It has passed on every run since; no conversation in this dataset ever
   contained real reasoning tokens at the read position.

3. **Batched vs. unbatched indexing check.** Every `natural`-position extraction is
   cross-checked against an unbatched re-run of the same samples, one at a time, to catch
   padding-side / last-token bugs. Max relative difference: 3.7e-3 (education), 7.4e-3
   (age) — both well under the 1e-2 tolerance.

4. **Layer-0 null.** Layer 0 is the raw token embedding, before any transformer block has
   run. If the probe found signal there, that would mean the discriminative information
   is a literal lookup in the embedding table (bag-of-words), not something the model
   computed. **Layer 0 accuracy equals the majority-class baseline exactly, on all four
   attribute/position combinations** (0.3371 / 0.3371 / 0.2522 / 0.2522). No pre-transformer
   signal at all — whatever separates the classes is built up by the model's processing.

5. **Control task (per-input shuffled labels).** Not a per-row shuffle — a per-*unique-input*
   shuffle, so duplicate or near-duplicate conversations that straddle the train/val split
   would still show up as leakage if the probe had memorized them (Hewitt & Liang style,
   per Bortoletto et al. 2406.17513). Control task RNG is deliberately offset from the
   split seed so it can't accidentally correlate with the true labels. Result: control
   task tops out at 0.42 (education/elicited) and 0.30 (age/natural), both under the
   Bonferroni-corrected (across all 37 layers) leakage threshold of 0.487 / 0.374. No
   evidence of split leakage.

6. **TF-IDF baseline — the one that actually moves the verdict.** A bag-of-words
   TF-IDF + logistic regression classifier (`max_features=5000`, unigrams+bigrams, same
   seed, same stratified 80/20 split) was fit on the raw user-turn text, with no model
   activations involved at all. This directly tests the null hypothesis `00_CONTEXT.md`
   §2 names explicitly: *"it is trivially true that information about the user's
   knowledge is present in the user's text... so can TF-IDF, probably."*
   - **Education: TF-IDF gets 0.966 — identical, to three decimal places, to the best
     residual-stream probe (0.966 at layer 12).** This falsification attempt **fails**:
     we cannot currently distinguish "Qwen3 has a representation of the user's education
     level" from "the synthetic generator writes a recognizably different register per
     education subcategory, and both a linear probe on activations and a linear
     classifier on raw n-grams pick up the same lexical/register cue." See §3.
   - **Age: TF-IDF gets 0.896, ~10 points below the probe's 0.991.** TF-IDF is still far
     above chance (0.252) — surface wording carries real signal for age too — but there
     is a reproducible gap the probe captures that a bag-of-words classifier does not.
     This is evidence, not proof, that the age representation involves something beyond
     raw lexical cues.

---

## 3. Minimum plausible finding

Stripping away everything that isn't yet nailed down, here is the most conservative
claim the data actually supports, attribute by attribute:

- **Both attributes:** something in Qwen3-8B's residual stream at mid-to-late layers
  linearly separates conversations by (synthetic) user age and education, far above
  chance, and this is not an artifact of train/val leakage (control task) or of literal
  token-embedding lookup (layer-0 null). This much is solid.

- **Age:** the representation is not fully reducible to surface lexical/register cues —
  it beats a same-data TF-IDF baseline by ~10 points. That is modest evidence of
  something the model computed beyond wording, but it is a low bar (bag-of-words +
  logistic regression), not evidence of a "user model" in the sense `00_CONTEXT.md` §2
  requires (cross-register transfer, per-concept specificity, separability from the
  output plan, causal steering). None of those four tests have been run yet — that's
  Act 1 and Act 2.

- **Education: the minimum plausible finding is negative.** We have not falsified the
  hypothesis that this is a register/formality detector rather than a knowledge-state
  representation — TF-IDF and the deep probe are statistically indistinguishable here.
  Per `00_CONTEXT.md`'s own framing ("if the representation turns out to be a
  register/formality direction wearing a costume, that is the write-up — do not try to
  rescue a positive result"), this should go into Act 1 flagged as a likely register
  confound, not carried forward as a confirmed finding.

- **A likely contributor, worth naming:** both attributes come from a single synthetic
  generator (one `GEN_MODEL`, temperature 1.0) that plausibly writes in a consistent,
  recognizable register per subcategory by construction (e.g., "some schooling" prompts
  probably read differently than "college and above" ones almost by definition of the
  generation instructions). That would inflate *both* the TF-IDF baseline and the probe
  in a correlated way, and is a property of this synthetic dataset, not necessarily of
  how real users write. Act 1's contrast set is specifically designed to separate
  register from knowledge state for this reason — this result is exactly the motivating
  case for that design, not a surprise to route around.

---

## 4. Bottom line for Act 1

- Replication criterion: **PASS**, as defined by `act0_replication.md`. Proceed.
- Carry forward as an open confound, not a resolved finding: **education may be a
  register detector**; treat any Act 1 education result with the TF-IDF gap in mind and
  re-run this same baseline on the Act 1 contrast set once it exists.
- Age is the more promising attribute for the "knowledge model, not style model"
  question posed in Act 1, purely because it already shows separation from a lexical
  baseline that education does not.
- Task 0.5 (`mathdial`, hand-read dialogues + qualitative notes) is still outstanding and
  intentionally not automated — see `README_ACT0.md`.
