"""Build and evaluate the Act 1 text ablations (handover_cpu_ablations.md).

    python run_ablations.py

Writes data/contrast/contrast_v1_abl{A,B,AB,CTRL}.jsonl, data/contrast/ablation_stats.json
and results/ablation_samples.txt, prints the five-variant TF-IDF table, and appends one
entry to results/runs.jsonl. `contrast_v1.jsonl` is opened read-only and never written.
"""

from __future__ import annotations

# Rule 7 of the handover: pin BLAS threads before numpy/sklearn are imported. This box
# has 128 cores and OpenBLAS oversubscription made an earlier probe run ~11x slower.
import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_v] = "1"

import json
import random
import re
import sys
from collections import Counter

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import ablations as AB  # noqa: E402
from src import config as C  # noqa: E402
from src import eedi  # noqa: E402
from src import grouping  # noqa: E402

SEED = 0
VARIANTS = ["orig", "A", "B", "AB", "CTRL"]
ORIG_PATH = C.CONTRAST / "contrast_v1.jsonl"
STATS_PATH = C.CONTRAST / "ablation_stats.json"
SAMPLES_PATH = C.RESULTS / "ablation_samples.txt"
GROUPS_PATH = C.CONTRAST / "ablation_groups.json"

# Two question ids are merged into one split group when their demonstrated rows share
# at least this many normalised equations.
MIN_SHARED_EQ = 5

# Handover-specified TF-IDF pipeline.
TFIDF_KW = dict(max_features=5000, ngram_range=(1, 2))
LOGREG_KW = dict(max_iter=3000, class_weight="balanced")


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[write] {len(rows)} rows -> {path}")


def row_text(row) -> str:
    return " ".join(t["content"] for t in row["turns"])


def clone(row, texts):
    """Copy a row with new turn contents; every other field is carried through as-is."""
    new = dict(row)
    new["turns"] = [dict(t, content=c) for t, c in zip(row["turns"], texts)]
    return new


# ======================================================================================
# 1. Load
# ======================================================================================

C.banner("1. LOAD")
orig_all = read_jsonl(ORIG_PATH)
orig = [r for r in orig_all if r["disclosure"] == "demonstrated"]
print(f"contrast_v1.jsonl: {len(orig_all)} rows total")
print(f"demonstrated subset: {len(orig)} rows "
      f"({sum(r['knowledge_state'] == 'gap' for r in orig)} gap, "
      f"{sum(r['knowledge_state'] == 'knows' for r in orig)} knows)")
print("SCOPE DECISION: stated/undisclosed rows are OMITTED from every variant file "
      "(the handover allows omit-or-carry-through; omitting is what makes the id list "
      "identical to the demonstrated subset).")

cq = eedi.load_concept_questions()
QUESTIONS = {q.question_id: q for lst in cq.values() for q in lst}
missing_q = sorted({r["eedi_question_id"] for r in orig} - set(QUESTIONS))
assert not missing_q, f"question ids not in Eedi source: {missing_q}"

ORIG_IDS = [r["id"] for r in orig]

# ======================================================================================
# 2. Ablation A -- answer -> [ANS]
# ======================================================================================

C.banner("2. ABLATION A -- answer strings -> [ANS]")
PATTERNS = {qid: AB.question_answer_patterns(QUESTIONS[qid])
            for qid in {r["eedi_question_id"] for r in orig}}
print(f"answer patterns built for {len(PATTERNS)} distinct eedi questions "
      f"(correct answer + all labelled distractors, per question)")


def ablate_a(row):
    """Returns (new_texts, n_hits, used_variable_fallback)."""
    literal, loose = PATTERNS[row["eedi_question_id"]]
    texts = [t["content"] for t in row["turns"]]
    out, hits = [], 0
    for t in texts:
        new, n = AB.apply_ablation_a(t, literal)
        out.append(new)
        hits += n
    if hits == 0 and loose:
        out2, hits2 = [], 0
        for t in texts:
            new, n = AB.apply_ablation_a(t, loose)
            out2.append(new)
            hits2 += n
        if hits2:
            return out2, hits2, True
    return out, hits, False


a_texts, a_hits, a_fallback = {}, {}, {}
for r in orig:
    txts, hits, fb = ablate_a(r)
    a_texts[r["id"]] = txts
    a_hits[r["id"]] = hits
    a_fallback[r["id"]] = fb

a_matched = [r for r in orig if a_hits[r["id"]] > 0]
A_MATCH_RATE = len(a_matched) / len(orig)
a_match_by_class = Counter(r["knowledge_state"] for r in a_matched)
a_n_by_class = Counter(r["knowledge_state"] for r in orig)
# Net, not "removed": `[ANS]` is 5 characters and is usually longer than the answer it
# replaces, so A's text is slightly *longer* than the original.
a_chars = Counter()
for r in orig:
    a_chars[r["knowledge_state"]] += (
        sum(len(t) for t in a_texts[r["id"]]) - sum(len(t["content"]) for t in r["turns"])
    )
a_unmatched_q = Counter(r["eedi_question_id"] for r in orig if a_hits[r["id"]] == 0)


def answer_kind(qid):
    """`numeric` if the question's options are values/expressions, else `verdict`.

    `Only Katie` / `Both steps are correct` / `always true` are verdicts about someone
    else's work: there is no numeric answer in the row to neutralise, and the student
    states the verdict in their own words ("katie's right") rather than quoting the
    option, so a string lookup against the Eedi cell cannot match them.
    """
    q = QUESTIONS[qid]
    cells = [q.correct_answer_text] + [d.text for d in q.distractors]
    plain = [AB.latex_to_plain(c) for c in cells]
    return "numeric" if any(re.search(r"\d", p) and re.search(r"[=+\-*/^]", p)
                            for p in plain) else "verdict"


a_kind = {qid: answer_kind(qid) for qid in PATTERNS}
kind_n, kind_hit = Counter(), Counter()
for r in orig:
    k = a_kind[r["eedi_question_id"]]
    kind_n[k] += 1
    if a_hits[r["id"]] > 0:
        kind_hit[k] += 1

print(f"answer-match rate: {A_MATCH_RATE:.4f}  ({len(a_matched)}/{len(orig)} rows had at "
      f"least one answer string replaced)")
for k in ("gap", "knows"):
    print(f"  {k:6s} {a_match_by_class[k]}/{a_n_by_class[k]} "
          f"= {a_match_by_class[k] / a_n_by_class[k]:.3f}")
print(f"  variable-agnostic fallback fired on {sum(a_fallback.values())} rows")
print(f"  total [ANS] substitutions: {sum(a_hits.values())}")
for k in ("numeric", "verdict"):
    if kind_n[k]:
        print(f"  {k:8s} answer questions: {kind_hit[k]}/{kind_n[k]} "
              f"= {kind_hit[k] / kind_n[k]:.3f}")
print(f"  worst unmatched questions: {a_unmatched_q.most_common(10)}")
if A_MATCH_RATE < 0.80:
    print()
    print("  *** GATE FAILURE (handover rule 6): match rate < 0.80. ***")
    print("  The A variant is still written so the pipeline is complete, but it must not")
    print("  be treated as a clean answer ablation until a human has ruled on this.")

# ======================================================================================
# 3. Ablation B -- cue sentences removed
# ======================================================================================

C.banner("3. ABLATION B -- metacognitive/verification cue sentences removed")
print(f"cue lexicon: {len(AB.ALL_CUES)} terms "
      f"({len(AB.CUES_KNOWS_SEED) + len(AB.CUES_GAP_SEED)} seed from handover, "
      f"{len(AB.LEXICON_ADDED)} added by this run)")

b_texts, b_removed, b_dropped = {}, {}, {}
b_emptied = []
for r in orig:
    texts = [t["content"] for t in r["turns"]]
    out, ndrop = [], 0
    for t in texts:
        new, n = AB.apply_ablation_b(t)
        out.append(new)
        ndrop += n
    removed = sum(len(t) for t in texts) - sum(len(t) for t in out)
    if not any(t.strip() for t in out):
        out = [AB.EMPTY_PLACEHOLDER] + [""] * (len(out) - 1)
        b_emptied.append(r["id"])
    b_texts[r["id"]] = out
    b_removed[r["id"]] = removed
    b_dropped[r["id"]] = ndrop

b_chars = Counter()
b_orig_chars = Counter()
b_sent = Counter()
for r in orig:
    k = r["knowledge_state"]
    b_chars[k] += b_removed[r["id"]]
    b_orig_chars[k] += len(row_text(r))
    b_sent[k] += b_dropped[r["id"]]

print(f"rows emptied by B (kept with {AB.EMPTY_PLACEHOLDER}): {len(b_emptied)}")
for k in ("gap", "knows"):
    n = a_n_by_class[k]
    print(f"  {k:6s} chars_removed={b_chars[k]:7d}  of {b_orig_chars[k]:7d} "
          f"({100 * b_chars[k] / b_orig_chars[k]:5.2f}%)  "
          f"sentences_dropped={b_sent[k]:4d}  per_row={b_chars[k] / n:7.1f}")
# Audit the lexicon extension: what does each added term buy over the handover's seed,
# and would the seed alone have produced a smaller class imbalance?
_seed_re = AB._cue_pattern(AB.CUES_KNOWS_SEED + AB.CUES_GAP_SEED)
_all_sentences = [(r["knowledge_state"], b)
                  for r in orig for t in r["turns"]
                  for b, _ in AB.split_sentences(t["content"])]
_seed_hit = [i for i, (_, b) in enumerate(_all_sentences) if _seed_re.search(b)]
_seed_hit_set = set(_seed_hit)
_seed_removed = Counter()
for i in _seed_hit:
    _seed_removed[_all_sentences[i][0]] += len(_all_sentences[i][1])
_seed_pct = {k: 100 * _seed_removed[k] / b_orig_chars[k] for k in ("gap", "knows")}
_added_contrib = {}
for term in AB.LEXICON_ADDED:
    tre = AB._cue_pattern([term])
    extra = [i for i, (_, b) in enumerate(_all_sentences)
             if i not in _seed_hit_set and tre.search(b)]
    if extra:
        _added_contrib[term] = {
            "sentences": len(extra),
            "chars": int(sum(len(_all_sentences[i][1]) for i in extra)),
            "gap": sum(1 for i in extra if _all_sentences[i][0] == "gap"),
            "knows": sum(1 for i in extra if _all_sentences[i][0] == "knows"),
        }
print(f"  seed lexicon alone would remove: gap {_seed_pct['gap']:.2f}% / "
      f"knows {_seed_pct['knows']:.2f}%  "
      f"(knows - gap = {_seed_pct['knows'] - _seed_pct['gap']:+.2f} pp)")
print(f"  {len(_added_contrib)}/{len(AB.LEXICON_ADDED)} added terms fire at all; "
      f"biggest: "
      f"{sorted(_added_contrib.items(), key=lambda kv: -kv[1]['chars'])[:3]}")

_gap_pct = 100 * b_chars["gap"] / b_orig_chars["gap"]
_kn_pct = 100 * b_chars["knows"] / b_orig_chars["knows"]
B_LENGTH_CONFOUND = abs(_kn_pct - _gap_pct)
print(f"  per-class removal gap: knows - gap = {_kn_pct - _gap_pct:+.2f} percentage points")
if B_LENGTH_CONFOUND > 2.0:
    print("  NOTE: B removes a materially different share of text from the two classes. "
          "Residual length itself is now weakly class-informative; read B against CTRL.")

# ======================================================================================
# 4. Ablation AB
# ======================================================================================

C.banner("4. ABLATION AB -- A then B")
ab_texts = {}
ab_emptied = []
for r in orig:
    out = []
    for t in a_texts[r["id"]]:
        new, _ = AB.apply_ablation_b(t)
        out.append(new)
    if not any(t.strip() for t in out):
        out = [AB.EMPTY_PLACEHOLDER] + [""] * (len(out) - 1)
        ab_emptied.append(r["id"])
    ab_texts[r["id"]] = out
ab_chars = Counter()
for r in orig:
    ab_chars[r["knowledge_state"]] += len(row_text(r)) - sum(len(t) for t in ab_texts[r["id"]])
print(f"rows emptied by AB: {len(ab_emptied)}")
for k in ("gap", "knows"):
    print(f"  {k:6s} chars_removed={ab_chars[k]:7d} of {b_orig_chars[k]:7d} "
          f"({100 * ab_chars[k] / b_orig_chars[k]:5.2f}%)")

# ======================================================================================
# 5. Ablation CTRL -- random sentences, length-matched to B
# ======================================================================================

C.banner("5. ABLATION CTRL -- random sentences, length-matched to B")
ctrl_texts, ctrl_removed, ctrl_dropped = {}, {}, {}
ctrl_in_tol, ctrl_no_target, ctrl_emptied, ctrl_capped = 0, 0, [], []
for r in orig:
    rng = random.Random(f"{SEED}:{r['id']}")
    target = b_removed[r["id"]]
    texts = [t["content"] for t in r["turns"]]
    out, removed, ndrop = AB.apply_ablation_ctrl(texts, target, rng)
    if not any(t.strip() for t in out):
        out = [AB.EMPTY_PLACEHOLDER] + [""] * (len(out) - 1)
        ctrl_emptied.append(r["id"])
    ctrl_texts[r["id"]] = out
    ctrl_removed[r["id"]] = removed
    ctrl_dropped[r["id"]] = ndrop
    if target == 0:
        ctrl_no_target += 1
        ctrl_in_tol += 1
    elif abs(removed - target) <= 0.15 * target:
        ctrl_in_tol += 1
    elif AB.ctrl_eligible_chars(texts) < 0.85 * target:
        # B removed more of this row than there is non-cue text left to delete, so no
        # length-matched control is constructible here at all.
        ctrl_capped.append(r["id"])

ctrl_chars = Counter()
ctrl_sent = Counter()
for r in orig:
    ctrl_chars[r["knowledge_state"]] += ctrl_removed[r["id"]]
    ctrl_sent[r["knowledge_state"]] += ctrl_dropped[r["id"]]

CTRL_TOL_RATE = ctrl_in_tol / len(orig)
_tot_b = sum(b_removed.values())
_tot_c = sum(ctrl_removed.values())
print(f"rows within +/-15% of B's per-row removal: {ctrl_in_tol}/{len(orig)} "
      f"= {CTRL_TOL_RATE:.4f}   ({ctrl_no_target} rows had B target 0)")
print(f"rows where no length-match is constructible (B removed more than the whole "
      f"non-cue remainder): {len(ctrl_capped)}")
print(f"total chars removed: B={_tot_b}  CTRL={_tot_c}  "
      f"({100 * (_tot_c - _tot_b) / max(_tot_b, 1):+.2f}%)")
for k in ("gap", "knows"):
    print(f"  {k:6s} chars_removed={ctrl_chars[k]:7d} of {b_orig_chars[k]:7d} "
          f"({100 * ctrl_chars[k] / b_orig_chars[k]:5.2f}%)  "
          f"sentences_dropped={ctrl_sent[k]:4d}")
print("CTRL never deletes a sentence B's cue rule selected -- by construction "
      "(cue sentences are excluded from the eligible pool).")

# ======================================================================================
# 6. Assemble + alignment assertions + write
# ======================================================================================

C.banner("6. WRITE VARIANTS")
VARIANT_TEXTS = {"orig": {r["id"]: [t["content"] for t in r["turns"]] for r in orig},
                 "A": a_texts, "B": b_texts, "AB": ab_texts, "CTRL": ctrl_texts}
VARIANT_ROWS = {}
for v in VARIANTS:
    rows = [clone(r, VARIANT_TEXTS[v][r["id"]]) for r in orig]
    assert [r["id"] for r in rows] == ORIG_IDS, f"{v}: id order differs from orig"
    for a, b in zip(rows, orig):
        for key in ("paired_id", "eedi_question_id", "eedi_misconception",
                    "knowledge_state", "register", "concept"):
            assert a.get(key) == b.get(key), f"{v}: field {key} mutated on {a['id']}"
        assert len(a["turns"]) == len(b["turns"]), f"{v}: turn count changed on {a['id']}"
    VARIANT_ROWS[v] = rows
print("id alignment asserted across all five variants (same ids, same order).")
print("preserved fields asserted unchanged: paired_id, eedi_question_id, "
      "eedi_misconception, knowledge_state, register, concept.")

for v in VARIANTS:
    if v == "orig":
        continue
    write_jsonl(C.CONTRAST / f"contrast_v1_abl{v}.jsonl", VARIANT_ROWS[v])
assert ORIG_PATH.stat().st_size > 0
print(f"[read-only] {ORIG_PATH.name} untouched.")

# ======================================================================================
# 7. TF-IDF baselines
# ======================================================================================

C.banner("7. SPLIT GROUPS -- eedi_question_id, content-corrected")
y = np.array([1 if r["knowledge_state"] == "knows" else 0 for r in orig])
qid_groups = np.array([str(r["eedi_question_id"]) for r in orig])

GROUP_OF_QID, LINKED = grouping.merge_questions(orig, row_text, min_shared=MIN_SHARED_EQ)
merged_groups = np.array([GROUP_OF_QID[q] for q in qid_groups])
print(f"`eedi_question_id` is only a proxy for content: some Eedi questions carry their "
      f"content in the answer options, and the generator invented its own option set.")
print(f"merging question ids whose demonstrated rows share >= {MIN_SHARED_EQ} equations:")
print(f"  {len(set(qid_groups))} question ids -> {len(set(merged_groups))} groups "
      f"({len(LINKED)} pairs linked)")
for L in LINKED:
    print(f"    {L['a']} + {L['b']}  ({L['n_shared_equations']} shared equations)")

GROUPINGS = {"qid": qid_groups, "merged": merged_groups}
SPLITS = {}
for gname, g in GROUPINGS.items():
    SPLITS[gname] = next(GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=SEED)
                         .split(np.zeros(len(orig)), y, g))
    tr, te = SPLITS[gname]
    print(f"  split[{gname}]: n_train={len(tr)} n_test={len(te)} "
          f"majority={max(y[te].mean(), 1 - y[te].mean()):.4f}")
print("Each split is built once and reused across all five variants.")


def variant_texts(v):
    return [" ".join(VARIANT_TEXTS[v][r["id"]]).strip() or AB.EMPTY_PLACEHOLDER
            for r in orig]


def fit_tfidf(texts, gname):
    """Handover-specified pipeline on one grouping."""
    tr_idx, te_idx = SPLITS[gname]
    vec = TfidfVectorizer(**TFIDF_KW)
    Xtr = vec.fit_transform([texts[i] for i in tr_idx])
    Xte = vec.transform([texts[i] for i in te_idx])
    clf = LogisticRegression(random_state=SEED, **LOGREG_KW).fit(Xtr, y[tr_idx])
    pred = clf.predict(Xte)
    names = np.array(vec.get_feature_names_out())
    order = np.argsort(clf.coef_[0])
    return {
        "balanced_accuracy": float(balanced_accuracy_score(y[te_idx], pred)),
        "accuracy": float(accuracy_score(y[te_idx], pred)),
        "majority_baseline": float(max(y[te_idx].mean(), 1 - y[te_idx].mean())),
        "n_train": int(len(tr_idx)),
        "n_test": int(len(te_idx)),
        "n_features": int(len(names)),
        "top_coef_gap": [[str(n), float(c)] for n, c in
                         zip(names[order[:15]], clf.coef_[0][order[:15]])],
        "top_coef_knows": [[str(n), float(c)] for n, c in
                           zip(names[order[-15:]][::-1], clf.coef_[0][order[-15:]][::-1])],
    }


def fit_reference(texts, gname):
    """The unigram / default-LogReg / GroupKFold-5 config, kept as a second anchor.

    The handover quotes an `orig` reference of ~0.743 balanced accuracy, which the
    pipeline it *specifies* does not produce (see the reference-check section below).
    This config does, so both are reported for every variant and every grouping.
    """
    accs, bals, majs = [], [], []
    for tr, te in GroupKFold(n_splits=5).split(np.zeros(len(texts)), y,
                                               GROUPINGS[gname]):
        vec = TfidfVectorizer(max_features=5000)
        Xtr = vec.fit_transform([texts[i] for i in tr])
        Xte = vec.transform([texts[i] for i in te])
        clf = LogisticRegression(max_iter=1000, random_state=SEED).fit(Xtr, y[tr])
        pred = clf.predict(Xte)
        accs.append(accuracy_score(y[te], pred))
        bals.append(balanced_accuracy_score(y[te], pred))
        majs.append(max(y[te].mean(), 1 - y[te].mean()))
    return {"balanced_accuracy": float(np.mean(bals)), "accuracy": float(np.mean(accs)),
            "majority_baseline": float(np.mean(majs)), "n_folds": 5}


C.banner("7a. TF-IDF BASELINES -- five variants x two groupings x two pipelines")
TFIDF, REFCFG = {}, {}
for v in VARIANTS:
    txt = variant_texts(v)
    TFIDF[v] = {g: fit_tfidf(txt, g) for g in GROUPINGS}
    REFCFG[v] = {g: fit_reference(txt, g) for g in GROUPINGS}
    print(f"  {v:5s} specified[merged]={TFIDF[v]['merged']['balanced_accuracy']:.4f}  "
          f"specified[qid]={TFIDF[v]['qid']['balanced_accuracy']:.4f}  "
          f"reference[merged]={REFCFG[v]['merged']['balanced_accuracy']:.4f}  "
          f"reference[qid]={REFCFG[v]['qid']['balanced_accuracy']:.4f}")

# ---- reference check against the handover's 0.743 anchor -------------------------------
C.banner("7b. REFERENCE CHECK -- handover expects orig ~= 0.743 balanced accuracy")
ORIG_BAL = TFIDF["orig"]["merged"]["balanced_accuracy"]
ORIG_BAL_QID = TFIDF["orig"]["qid"]["balanced_accuracy"]
print("handover-specified pipeline: TfidfVectorizer(max_features=5000, ngram_range=(1,2))")
print("  + LogisticRegression(max_iter=3000, class_weight='balanced'),")
print("  GroupShuffleSplit(test_size=0.3, random_state=0)")
print(f"    orig, grouped by eedi_question_id : {ORIG_BAL_QID:.4f}")
print(f"    orig, content-corrected grouping  : {ORIG_BAL:.4f}   "
      f"(merging costs {ORIG_BAL - ORIG_BAL_QID:+.4f})")
print(f"    expected ~= 0.743")
REFERENCE_OK = abs(ORIG_BAL_QID - 0.743) <= 0.02
if not REFERENCE_OK:
    print("    *** GATE FAILURE (handover rule 5): orig does not reproduce ~=0.743 "
          "under the specified pipeline, on either grouping. ***")
    print("    The unigram / default-LogReg / GroupKFold(5) config does:")
    print(f"      orig, grouped by eedi_question_id : "
          f"{REFCFG['orig']['qid']['balanced_accuracy']:.4f}")
    print(f"      orig, content-corrected grouping  : "
          f"{REFCFG['orig']['merged']['balanced_accuracy']:.4f}")
    print("    All four numbers are reported per variant so downstream work can use any.")

C.banner("7c. FIVE-VARIANT TF-IDF TABLE (content-corrected grouping is primary)")
hdr = (f"{'variant':<8}{'spec/merged':>13}{'spec/qid':>10}{'ref/merged':>12}"
       f"{'ref/qid':>9}{'majority':>10}{'n_train':>9}{'n_test':>8}{'d_vs_orig':>11}")
print(hdr)
print("-" * len(hdr))
for v in VARIANTS:
    t, rf = TFIDF[v]["merged"], REFCFG[v]["merged"]
    print(f"{v:<8}{t['balanced_accuracy']:>13.4f}"
          f"{TFIDF[v]['qid']['balanced_accuracy']:>10.4f}"
          f"{rf['balanced_accuracy']:>12.4f}"
          f"{REFCFG[v]['qid']['balanced_accuracy']:>9.4f}"
          f"{t['majority_baseline']:>10.4f}{t['n_train']:>9d}{t['n_test']:>8d}"
          f"{t['balanced_accuracy'] - ORIG_BAL:>+11.4f}")
print()
print("spec = handover pipeline; ref = unigram/default-LogReg/GroupKFold(5).")
print("merged = content-corrected groups; qid = raw eedi_question_id.")
print("d_vs_orig is against spec/merged.")

C.banner("7d. TOP 15 COEFFICIENTS PER CLASS, PER VARIANT")
for v in VARIANTS:
    print(f"\n--- {v} ---")
    print("  GAP-indicating  :", [n for n, _ in TFIDF[v]["merged"]["top_coef_gap"]])
    print("  KNOWS-indicating:", [n for n, _ in TFIDF[v]["merged"]["top_coef_knows"]])

# ======================================================================================
# 8. Before/after samples
# ======================================================================================

C.banner("8. BEFORE / AFTER SAMPLES -- 6 rows per variant (3 gap, 3 knows)")
gap_rows = [r for r in orig if r["knowledge_state"] == "gap"]
knows_rows = [r for r in orig if r["knowledge_state"] == "knows"]
srng = random.Random(SEED)
sample_rows = srng.sample(gap_rows, 3) + srng.sample(knows_rows, 3)

lines = []
for v in VARIANTS:
    if v == "orig":
        continue
    lines.append("=" * 78)
    lines.append(f"VARIANT {v} -- 6 rows, before and after")
    lines.append("=" * 78)
    for r in sample_rows:
        lines.append("")
        lines.append(f"--- {r['id']}  [{r['knowledge_state']}] "
                     f"qid={r['eedi_question_id']} register={r['register']}")
        for i, t in enumerate(r["turns"]):
            lines.append(f"  BEFORE turn{i}: {t['content']}")
        for i, t in enumerate(VARIANT_TEXTS[v][r["id"]]):
            lines.append(f"  AFTER  turn{i}: {t}")
    lines.append("")
text_block = "\n".join(lines)
with open(SAMPLES_PATH, "w", encoding="utf-8") as f:
    f.write(text_block)
print(text_block)
print(f"[write] samples -> {SAMPLES_PATH}")

# ======================================================================================
# 9. Stats file + run log
# ======================================================================================

C.banner("9. STATS")
stats = {
    "seed": SEED,
    "source": str(ORIG_PATH.name),
    "scope": "demonstrated rows only; stated/undisclosed omitted from every variant",
    "n_rows": len(orig),
    "n_gap": int(a_n_by_class["gap"]),
    "n_knows": int(a_n_by_class["knows"]),
    "id_alignment_asserted": True,
    "tfidf_pipeline": {
        "specified": "TfidfVectorizer(max_features=5000, ngram_range=(1,2)) + "
                     "LogisticRegression(max_iter=3000, class_weight='balanced') + "
                     "GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=0)",
        "reference": "TfidfVectorizer(max_features=5000) + "
                     "LogisticRegression(max_iter=1000) + GroupKFold(5), "
                     "mean over folds",
        "groupings": {
            "qid": "raw eedi_question_id",
            "merged": "eedi_question_id with content-colliding ids merged; see "
                      "`grouping` below and data/contrast/ablation_groups.json",
        },
    },
    "grouping": {
        "why": "eedi_question_id is only a proxy for row content. Questions whose "
               "content lives in the answer options (e.g. 'One of these equations has "
               "exactly one solution. Which is it?') were generated from the stem "
               "alone, so the generator invented its own option set. Questions 1158 "
               "and 552 have byte-identical stems and their rows converge on the same "
               "equations, which lets content straddle a question-grouped split.",
        "method": f"merge question ids whose demonstrated rows share >= "
                  f"{MIN_SHARED_EQ} normalised equations (variable-, spacing-, paren- "
                  f"and side-insensitive); union-find over the resulting links",
        "min_shared_equations": MIN_SHARED_EQ,
        "n_question_ids": len(set(qid_groups)),
        "n_merged_groups": len(set(merged_groups)),
        "linked_pairs": LINKED,
        "group_of_qid": GROUP_OF_QID,
        "cost_on_orig_specified_pipeline":
            TFIDF["orig"]["merged"]["balanced_accuracy"]
            - TFIDF["orig"]["qid"]["balanced_accuracy"],
        "cost_on_orig_reference_pipeline":
            REFCFG["orig"]["merged"]["balanced_accuracy"]
            - REFCFG["orig"]["qid"]["balanced_accuracy"],
    },
    "reference_check": {
        "expected_orig_balanced_accuracy": 0.743,
        "observed_orig_specified_qid_grouped": ORIG_BAL_QID,
        "observed_orig_specified_merged_grouped": ORIG_BAL,
        "observed_orig_reference_qid_grouped":
            REFCFG["orig"]["qid"]["balanced_accuracy"],
        "observed_orig_reference_merged_grouped":
            REFCFG["orig"]["merged"]["balanced_accuracy"],
        "passes": bool(REFERENCE_OK),
        "note": "Handover rule 5 gate. The pipeline the handover specifies does not "
                "reproduce its own quoted 0.743 anchor on this data; the unigram / "
                "default-LogReg / GroupKFold(5) config does (to within 0.002). No "
                "artifact in this working copy records where 0.743 came from.",
    },
    "ablation_A": {
        "method": "correct answer + all labelled distractors for the row's "
                  "eedi_question_id, canonicalised out of LaTeX, matched "
                  "whitespace/glyph-tolerantly, replaced with [ANS]",
        "answer_match_rate": A_MATCH_RATE,
        "answer_match_rate_by_class": {
            k: a_match_by_class[k] / a_n_by_class[k] for k in ("gap", "knows")},
        "n_rows_matched": len(a_matched),
        "n_substitutions": int(sum(a_hits.values())),
        "n_rows_variable_agnostic_fallback": int(sum(a_fallback.values())),
        "net_chars_delta_by_class": {k: int(a_chars[k]) for k in ("gap", "knows")},
        "net_chars_delta_note": "positive because the 5-character [ANS] token is usually "
                                "longer than the answer string it replaces; A removes "
                                "answer identity, not length",
        "unmatched_by_question_id": dict(a_unmatched_q.most_common()),
        "match_rate_by_answer_kind": {
            k: {"n_rows": kind_n[k], "n_matched": kind_hit[k],
                "rate": (kind_hit[k] / kind_n[k]) if kind_n[k] else None}
            for k in ("numeric", "verdict")},
        "answer_kind_by_question_id": a_kind,
        "passes_80pct_gate": bool(A_MATCH_RATE >= 0.80),
        "class_imbalance_note":
            f"A matches {a_match_by_class['gap'] / a_n_by_class['gap']:.3f} of gap rows "
            f"but only {a_match_by_class['knows'] / a_n_by_class['knows']:.3f} of knows "
            f"rows, so the presence of the [ANS] token is itself weakly class-"
            f"informative. TF-IDF picks 'ans' up as a gap-side feature in variant A. "
            f"This is a confound A introduces, not a property of the original data.",
        "limitations": [
            "Intermediate working that coincidentally contains an answer value is also "
            "replaced. Documented and not disambiguated, per the handover.",
            "Question 452's answer cells are the bare letters A-D; single-letter "
            "candidates are skipped as far too generic, so its 37 rows never match.",
            "Many demonstrated rows paraphrase rather than quote the Eedi answer cell "
            "('no solution' vs 'This equation is impossible to solve'), or work an "
            "analogous problem in a different variable. Those rows cannot be matched by "
            "string lookup against the Eedi source, which is the method the handover "
            "specifies.",
        ],
    },
    "ablation_B": {
        "method": "whole sentences containing a cue term dropped; sentences split on "
                  "[.!?] + whitespace and on newlines",
        "n_cue_terms": len(AB.ALL_CUES),
        "lexicon_seed_knows": AB.CUES_KNOWS_SEED,
        "lexicon_seed_gap": AB.CUES_GAP_SEED,
        "lexicon_added": AB.LEXICON_ADDED,
        "lexicon_added_knows": AB.CUES_KNOWS_ADDED,
        "lexicon_added_gap": AB.CUES_GAP_ADDED,
        "chars_removed_by_class": {k: int(b_chars[k]) for k in ("gap", "knows")},
        "chars_total_by_class": {k: int(b_orig_chars[k]) for k in ("gap", "knows")},
        "pct_removed_by_class": {"gap": _gap_pct, "knows": _kn_pct},
        "pct_removed_knows_minus_gap": _kn_pct - _gap_pct,
        "sentences_dropped_by_class": {k: int(b_sent[k]) for k in ("gap", "knows")},
        "n_rows_emptied": len(b_emptied),
        "rows_emptied": b_emptied,
        "seed_lexicon_only_pct_removed": _seed_pct,
        "seed_lexicon_only_knows_minus_gap":
            _seed_pct["knows"] - _seed_pct["gap"],
        "added_term_contribution": _added_contrib,
        "length_confound_note":
            f"B removes {_kn_pct:.2f}% of knows text and {_gap_pct:.2f}% of gap text, a "
            f"{_kn_pct - _gap_pct:+.2f} pp difference. Residual length is therefore "
            f"itself weakly class-informative in B; any B result must be read against "
            f"CTRL, which reproduces the same per-row length change.",
    },
    "ablation_AB": {
        "method": "A applied first, then B; B drops the same sentences as in B alone "
                  "(cue terms are unaffected by [ANS] substitution)",
        "chars_removed_by_class": {k: int(ab_chars[k]) for k in ("gap", "knows")},
        "n_rows_emptied": len(ab_emptied),
    },
    "ablation_CTRL": {
        "method": "random whole sentences deleted from the original text, per row, "
                  "length-matched to B's per-row character removal; cue sentences are "
                  "excluded from the eligible pool so CTRL always removes different text",
        "tolerance": 0.15,
        "rows_within_tolerance": ctrl_in_tol,
        "rows_within_tolerance_rate": CTRL_TOL_RATE,
        "n_rows_with_zero_target": ctrl_no_target,
        "n_rows_no_match_constructible": len(ctrl_capped),
        "rows_no_match_constructible": ctrl_capped,
        "chars_removed_by_class": {k: int(ctrl_chars[k]) for k in ("gap", "knows")},
        "sentences_dropped_by_class": {k: int(ctrl_sent[k]) for k in ("gap", "knows")},
        "total_chars_removed_B": int(_tot_b),
        "total_chars_removed_CTRL": int(_tot_c),
        "n_rows_emptied": len(ctrl_emptied),
        "rng": "random.Random(f'{seed}:{row_id}') per row",
    },
    "variants": {
        v: {
            "file": ("contrast_v1.jsonl" if v == "orig" else f"contrast_v1_abl{v}.jsonl"),
            "n_rows": len(orig),
            "tfidf_specified_pipeline": TFIDF[v],
            "tfidf_reference_pipeline": REFCFG[v],
        } for v in VARIANTS
    },
}
with open(GROUPS_PATH, "w", encoding="utf-8") as f:
    json.dump({
        "note": "Split groups for the demonstrated subset. Use `group_of_row_id` (or "
                "`group_of_qid`) instead of raw eedi_question_id so content-colliding "
                "questions cannot straddle the train/test boundary. Row order matches "
                "every contrast_v1_abl*.jsonl file.",
        "min_shared_equations": MIN_SHARED_EQ,
        "n_question_ids": len(set(qid_groups)),
        "n_merged_groups": len(set(merged_groups)),
        "linked_pairs": LINKED,
        "group_of_qid": GROUP_OF_QID,
        "row_ids": ORIG_IDS,
        "group_of_row_id": {r["id"]: GROUP_OF_QID[str(r["eedi_question_id"])]
                            for r in orig},
    }, f, indent=2, ensure_ascii=False)
print(f"[write] {GROUPS_PATH}")

with open(STATS_PATH, "w", encoding="utf-8") as f:
    json.dump(stats, f, indent=2, ensure_ascii=False)
print(f"[write] {STATS_PATH}")

C.log_run(
    act="1",
    experiment="build_text_ablations_A_B_AB_CTRL",
    config={
        "source": "contrast_v1.jsonl",
        "scope": "demonstrated only",
        "n_rows": len(orig),
        "seed": SEED,
        "variants": VARIANTS,
        "tfidf": "max_features=5000, ngram_range=(1,2), LogReg(max_iter=3000, "
                 "class_weight=balanced), GroupShuffleSplit(0.3, seed 0) by "
                 "eedi_question_id",
        "cue_terms": len(AB.ALL_CUES),
        "grouping": f"content-corrected (min_shared_equations={MIN_SHARED_EQ})",
    },
    metrics={
        "answer_match_rate_A": A_MATCH_RATE,
        "answer_match_gate_passed": bool(A_MATCH_RATE >= 0.80),
        "orig_reference_check_passed": bool(REFERENCE_OK),
        "n_question_ids": len(set(qid_groups)),
        "n_merged_groups": len(set(merged_groups)),
        "grouping_cost_specified_pipeline": ORIG_BAL - ORIG_BAL_QID,
        "tfidf_specified_merged": {v: TFIDF[v]["merged"]["balanced_accuracy"]
                                   for v in VARIANTS},
        "tfidf_specified_qid": {v: TFIDF[v]["qid"]["balanced_accuracy"]
                                for v in VARIANTS},
        "tfidf_reference_merged": {v: REFCFG[v]["merged"]["balanced_accuracy"]
                                   for v in VARIANTS},
        "tfidf_reference_qid": {v: REFCFG[v]["qid"]["balanced_accuracy"]
                                for v in VARIANTS},
        "b_chars_removed_by_class": {k: int(b_chars[k]) for k in ("gap", "knows")},
        "b_pct_removed_by_class": {"gap": _gap_pct, "knows": _kn_pct},
        "ctrl_chars_removed_by_class": {k: int(ctrl_chars[k]) for k in ("gap", "knows")},
        "ctrl_rows_within_15pct": CTRL_TOL_RATE,
        "n_rows_emptied": {"B": len(b_emptied), "AB": len(ab_emptied),
                           "CTRL": len(ctrl_emptied)},
    },
)

C.banner("DONE")
print(f"answer-match rate (A)        : {A_MATCH_RATE:.4f}  "
      f"{'PASS' if A_MATCH_RATE >= 0.80 else 'FAIL (gate is 0.80)'}")
print(f"orig reference (~0.743)      : {ORIG_BAL_QID:.4f} qid / {ORIG_BAL:.4f} merged  "
      f"{'PASS' if REFERENCE_OK else 'FAIL'}")
print(f"split groups                 : {len(set(qid_groups))} qids -> "
      f"{len(set(merged_groups))} content-corrected groups")
print(f"CTRL within +/-15% of B      : {CTRL_TOL_RATE:.4f}")
