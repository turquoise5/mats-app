"""Loader for the Eedi "Mining Misconceptions in Mathematics" data (Act 1 contrast set).

Curriculum area: **Solving Equations** (linear + quadratic) -- 8 concepts, approved by the
human in the loop on 2026-08-28. Do not change this list without going back through the
Stage A confirmation step in `act1_handover_generation.md`.

Data provenance: `data/raw/eedi/{train,misconception_mapping}.csv` is a community re-upload
of the Kaggle "eedi-mining-misconceptions-in-mathematics" competition data (the machine
running this had no Kaggle credentials configured). Flag that provenance in the write-up,
or refetch via the official Kaggle API if a cleaner citation trail is needed.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from pathlib import Path

from . import config as C

EEDI_DIR = C.RAW / "eedi"

# The 8 approved concepts, in the exact ConstructName spelling used in train.csv.
SELECTED_CONCEPTS = [
    "Solve two-step linear equations, with the variable on one side, with all positive integers",
    "Solve linear equations with the variable appearing on both sides, with all positive integers",
    "Solve two-step linear equations, with the variable on one side, involving positive fractions",
    "Solve linear equations with the variable appearing on both sides, involving positive fractions",
    "Solve quadratic equations using the quadratic formula where the coefficient of x² is not 1 ",
    "Solve quadratic equations using factorisation in the form (x + a)(x + b) ",
    "Solve quadratic equations using balancing",
    "Solve quadratic equations using factorisation in the form x(x + b) ",
]

CURRICULUM_AREA = "Solving Equations (linear + quadratic)"

# Short stable slugs for ids/filenames -- the ConstructNames are full sentences.
CONCEPT_SLUGS = {
    SELECTED_CONCEPTS[0]: "linear_2step_int",
    SELECTED_CONCEPTS[1]: "linear_both_int",
    SELECTED_CONCEPTS[2]: "linear_2step_frac",
    SELECTED_CONCEPTS[3]: "linear_both_frac",
    SELECTED_CONCEPTS[4]: "quad_formula",
    SELECTED_CONCEPTS[5]: "quad_factor_ab",
    SELECTED_CONCEPTS[6]: "quad_balance",
    SELECTED_CONCEPTS[7]: "quad_factor_xb",
}

_ANSWER_COLS = ["A", "B", "C", "D"]


@dataclass
class Distractor:
    letter: str
    text: str
    misconception_id: str | None
    misconception_name: str | None


@dataclass
class EediQuestion:
    question_id: str
    construct_name: str
    question_text: str
    correct_answer: str
    correct_answer_text: str
    distractors: list[Distractor] = field(default_factory=list)  # wrong answers with a labelled misconception

    def random_gap_distractor(self, rng: random.Random) -> Distractor | None:
        pool = [d for d in self.distractors if d.misconception_name]
        return rng.choice(pool) if pool else None


def _norm_misconception_id(x: str) -> str | None:
    x = (x or "").strip()
    if not x:
        return None
    return str(int(float(x)))  # train.csv stores these as floats, e.g. "1672.0"


def _load_misconception_map() -> dict[str, str]:
    path = EEDI_DIR / "misconception_mapping.csv"
    with open(path, encoding="utf-8") as f:
        return {r["MisconceptionId"]: r["MisconceptionName"] for r in csv.DictReader(f)}


def load_concept_questions() -> dict[str, list[EediQuestion]]:
    """Return {concept_name: [EediQuestion, ...]} for the 8 selected concepts only."""
    if not EEDI_DIR.exists():
        raise FileNotFoundError(
            f"{EEDI_DIR} not found. Expected train.csv + misconception_mapping.csv there."
        )
    misc_map = _load_misconception_map()
    train_path = EEDI_DIR / "train.csv"
    with open(train_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    selected = set(SELECTED_CONCEPTS)
    out: dict[str, list[EediQuestion]] = {c: [] for c in SELECTED_CONCEPTS}
    for r in rows:
        cname = r["ConstructName"]
        if cname not in selected:
            continue
        correct = r["CorrectAnswer"].strip()
        distractors = []
        for letter in _ANSWER_COLS:
            if letter == correct:
                continue
            mid = _norm_misconception_id(r.get(f"Misconception{letter}Id", ""))
            distractors.append(Distractor(
                letter=letter,
                text=r[f"Answer{letter}Text"],
                misconception_id=mid,
                misconception_name=misc_map.get(mid) if mid else None,
            ))
        out[cname].append(EediQuestion(
            question_id=r["QuestionId"],
            construct_name=cname,
            question_text=r["QuestionText"],
            correct_answer=correct,
            correct_answer_text=r[f"Answer{correct}Text"],
            distractors=distractors,
        ))
    missing = [c for c, qs in out.items() if not qs]
    if missing:
        raise ValueError(f"No questions found for concepts: {missing}")
    return out


def print_stage_a_report(concept_questions: dict[str, list[EediQuestion]]) -> None:
    print(f"Curriculum area: {CURRICULUM_AREA}")
    for i, (concept, qs) in enumerate(concept_questions.items(), 1):
        miscs = sorted({d.misconception_name for q in qs for d in q.distractors if d.misconception_name})
        print(f"{i}. {concept.strip()}")
        print(f"   n_questions={len(qs)}  n_distinct_misconceptions={len(miscs)}")
