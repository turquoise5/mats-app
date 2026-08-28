# Act 3 — Extensions (Suggestive Only)

**Budget: 2 hours total. One hour each, hard stop. Read `00_CONTEXT.md` first.**

---

## Purpose and posture

These are **secondary results**, reported as suggestive. They exist to show the direction
the work goes next, not to carry the write-up.

The single biggest risk in this act is scope creep: both questions are interesting enough
to eat six hours. **Set a timer.** If Act 2 is not finished by hour 14, skip Act 3 entirely
and spend the time on the write-up instead. A tight write-up of Acts 0–2 beats a sprawling
one that gestures at four things.

Everything here is framed in the write-up as: *"a quick check suggests X; a proper test
would require Y."* Do not present these with the confidence of Act 2.

---

## Extension A — Does the representation update rationally across turns? (60 min)

The dynamic question. TalkTuner's user model got more accurate over turns, but their study
also found it **over-updated on weak cues** — user-model accuracy was substantially worse
for women, with the probe flipping a user to "male, some schooling" on the basis of a
single remark about camping or not owning dresses. The analogous failure here would be a
model that downgrades its whole picture of your competence because you fumbled one concept.

### Design

Build 3-turn conversations from the Act 1 contrast set:

- **Turn 1**: user demonstrates mastery of concepts `C_1` and `C_2` (register held fixed).
- **Turn 2**: one of four conditions —
  - `botch_C3`: user makes a clear error on an *unrelated* concept `C_3`
  - `botch_C1`: user makes an error on a concept they demonstrated in turn 1 (contradiction)
  - `neutral`: user says something contentless ("ok, thanks, one more question")
  - `master_C3`: user demonstrates mastery of `C_3`
- **Turn 3**: user asks a question requiring `C_1`.

### Measure

Probe for `knows C_1`, `knows C_2`, `knows C_3` at the end of each turn. Plot the three
trajectories across turns, per condition.

The question: after `botch_C3`, does `knows C_1` drop?

- Stays flat → the representation is concept-specific and updates rationally.
- Drops → **over-generalisation**. One visible failure contaminates the whole user model.

Run the same trajectories through the **omission readout** from Act 2: after `botch_C3`,
does the model start explaining `C_1` when it previously did not? A behavioural confirmation
of a representational finding is worth far more than the representational finding alone.

**Baseline**: `neutral` condition. Some drift across turns is expected just from context
length; the `neutral` arm tells you how much.

**Deliverable:** `results/figs/act3_turn_dynamics.png` — probe value vs turn, faceted by
condition, one line per concept.

**Hard stop at 60 minutes.** If the conversation construction is taking too long, cut to
two conditions (`botch_C3` and `neutral`) and report that.

---

## Extension B — Ignorance vs misconception (60 min)

Pedagogically these demand opposite responses: a gap needs filling, a misconception needs
dislodging. If the model does not distinguish them, its teaching cannot be right for both.

The Eedi taxonomy gives ground truth for free — each distractor maps to a **named**
misconception, so "user believes M" is a labelled state, not a guess.

### Design

Three states for each concept `C`, register held fixed:

- `gap` — user has never encountered `C` and says so
- `misconception:M` — user confidently applies a specific wrong rule `M`
- `mastery` — control

### Measure

1. **Three-way probe** accuracy per layer. Can a linear probe separate `gap` from
   `misconception` at all?
2. **Geometry**: is `misconception` a distinct direction, or does it sit on the
   `mastery ← → gap` axis? Project the `misconception` centroid onto the mastery-gap axis
   and measure the residual. A large residual means a genuinely separate representation.
3. **Different misconceptions**: for one concept with 3+ labelled Eedi misconceptions, are
   `M_1`, `M_2`, `M_3` distinguishable from each other, or collapsed into "wrong"?
4. **Behavioural correlate**: does the model's response *name and address the specific
   misconception*, or give a generic explanation? Judge on 100 samples.

The most interesting possible finding: the model's activations distinguish `M_1` from `M_2`
but its **output** treats them identically. That is exactly the "model knows X, outputs
not-X" pattern — latent knowledge that fails to surface in behaviour — and it would be a
strong link back to the arithmetic-error probing work (arXiv 2507.12379).

**Deliverable:** `results/figs/act3_ignorance_vs_misconception.png`

---

## What not to do here

- Do not start a fourth extension because one of these produced something intriguing.
  Write it in `notes.md` under "future work" and move on.
- Do not run steering experiments in Act 3. Any causal claim needs the full baseline
  battery from Act 2, and there is no time for that here.
- Do not report these with the same confidence as Act 2 results. Sample sizes are smaller,
  baselines are thinner, and that must be visible in how they are described.

---

## Outputs of Act 3

- [ ] `results/figs/act3_turn_dynamics.png`
- [ ] `results/figs/act3_ignorance_vs_misconception.png`
- [ ] Entries in `results/runs.jsonl` with sample sizes clearly recorded
- [ ] Two paragraphs in `notes.md`, each stating the finding **and** the specific reason it
      is preliminary, plus what the proper version of the experiment would be
