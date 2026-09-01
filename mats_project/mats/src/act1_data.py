"""Loading and indexing for the Act 1 contrast set (data/contrast/contrast_v1.jsonl).

The dataset is frozen — this module only reads it, never writes it.

## A data hazard, discovered while writing this loader

`contrast_v1.jsonl` does not parse with `config.read_jsonl` (one `json.loads` per
physical line): `json.dumps` always escapes control characters, so a clean write would
never contain a literal newline inside a string, but this file does — some records span
several physical lines. Worse, the file has a large (~1.4MB) trailing block of prior-run
content appended after the 2112 official records, byte-for-byte identical to records
already present under the same `id` (verified: zero content mismatches across all 1139
duplicated ids). This is consistent with a non-atomic overwrite during one of the crashes
`act1_handover_generation.md` §"Known hazards" describes — a resumed run's rewrite did not
truncate the file first.

`load_rows` below parses leniently (`json.JSONDecoder(strict=False)`, so embedded raw
control characters in strings don't error) and stops at the first record it cannot decode,
which lands exactly on the boundary between the good data and the stale tail. It then
asserts the count matches `contrast_v1_stats.json["final_n"]` — the number the generation
run itself reported as correct — so a different corruption pattern in some future file
fails loudly instead of silently returning the wrong rows.
"""

from __future__ import annotations

import json
import re

import numpy as np

from . import config as C

BINARY_STATES = ("gap", "knows")


def load_rows(path=None, verify_against_stats: bool = True) -> list[dict]:
    path = path or C.CONTRAST_FILE
    raw = open(path, encoding="utf-8").read()
    dec = json.JSONDecoder(strict=False)

    rows, i, n = [], 0, len(raw)
    while i < n:
        while i < n and raw[i] in " \t\n\r":
            i += 1
        if i >= n:
            break
        try:
            obj, end = dec.raw_decode(raw, i)
        except json.JSONDecodeError as e:
            print(f"[act1_data] stopped parsing at byte {i}/{n} ({n - i} bytes left "
                  f"unparsed): {e}. See module docstring — this is expected, not a bug "
                  f"to chase, if the row count below matches contrast_v1_stats.json.")
            break
        rows.append(obj)
        i = end

    print(f"[act1_data] loaded {len(rows)} rows from {path}")

    if verify_against_stats:
        stats_path = path.parent / "contrast_v1_stats.json" if hasattr(path, "parent") \
            else C.CONTRAST / "contrast_v1_stats.json"
        if stats_path.exists():
            expected = json.load(open(stats_path))["final_n"]
            if len(rows) != expected:
                raise RuntimeError(
                    f"Parsed {len(rows)} rows but contrast_v1_stats.json says final_n="
                    f"{expected}. The lenient parser's stop-at-first-error assumption no "
                    "longer matches this file's corruption pattern -- do not proceed "
                    "silently; inspect the byte offset printed above."
                )

    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise RuntimeError("Duplicate ids after parsing -- the frozen dataset invariant "
                            "(id is unique) is violated. Stop and inspect.")
    return rows


def normalise_text(rows: list[dict]) -> list[str]:
    """First user turn, whitespace-collapsed. The content key used to detect duplicate
    inputs straddling a split -- same convention as run_act0.py:step_probe."""
    return [re.sub(r"\s+", " ", r["turns"][0]["content"].lower().strip()) for r in rows]


def full_user_text(rows: list[dict]) -> list[str]:
    """All user turns joined -- the TF-IDF baseline's input."""
    return [" ".join(t["content"] for t in r["turns"] if t["role"] == "user") for r in rows]


class Index:
    """Row-aligned metadata arrays for the full contrast set, plus convenience masks for
    the binary (knows/gap) subset that every transfer condition is built from."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.n = len(rows)
        self.ids = np.array([r["id"] for r in rows])
        self.paired_id = np.array([r["paired_id"] for r in rows])
        self.spec_id = np.array([r["spec_id"] for r in rows])
        self.concept_slug = np.array([r["concept_slug"] for r in rows])
        self.concept = np.array([r["concept"] for r in rows])
        self.register = np.array([r["register"] for r in rows])
        self.disclosure = np.array([r["disclosure"] for r in rows])
        self.knowledge_state = np.array([r["knowledge_state"] for r in rows])
        self.eedi_qid = np.array([r["eedi_question_id"] or "" for r in rows])
        self.content_key = np.array(normalise_text(rows))
        self.text = np.array(full_user_text(rows))

        # gap -> 0, knows -> 1, undisclosed (and anything else) -> -1
        self.y_full = np.where(
            self.knowledge_state == "knows", 1,
            np.where(self.knowledge_state == "gap", 0, -1),
        )
        self.binary_mask = self.y_full >= 0
        self.binary_idx = np.where(self.binary_mask)[0]
        self.undisclosed_idx = np.where(self.knowledge_state == "undisclosed")[0]

    # -- the binary (knows/gap) subset, as its own local index space ------------------
    def binary(self) -> "Subset":
        b = self.binary_idx
        return Subset(
            global_idx=b,
            y=self.y_full[b],
            register=self.register[b],
            disclosure=self.disclosure[b],
            paired_id=self.paired_id[b],
            eedi_qid=self.eedi_qid[b],
            content_key=self.content_key[b],
            text=self.text[b],
            concept_slug=self.concept_slug[b],
        )


class Subset:
    """Row-aligned arrays for a slice of the full index, e.g. the binary subset.
    `global_idx[i]` maps local row `i` back to its row index in the full contrast set
    (and therefore into the cached (n_full, n_layers+1, d) activation array)."""

    def __init__(self, global_idx, y, register, disclosure, paired_id, eedi_qid,
                 content_key, text, concept_slug):
        self.global_idx = np.asarray(global_idx)
        self.y = np.asarray(y)
        self.register = np.asarray(register)
        self.disclosure = np.asarray(disclosure)
        self.paired_id = np.asarray(paired_id)
        self.eedi_qid = np.asarray(eedi_qid)
        self.content_key = np.asarray(content_key)
        self.text = np.asarray(text)
        self.concept_slug = np.asarray(concept_slug)

    def __len__(self):
        return len(self.global_idx)


# --------------------------------------------------------------------------------------
# Transfer conditions (act1_handover_probing.md §5, "The transfer conditions" table)
# --------------------------------------------------------------------------------------

def build_conditions(b: Subset) -> dict:
    """One entry per condition in the handover's table.

    `kind="internal"` conditions (pooled, within-*) hold out a random split internally,
    grouped by `paired_id` so a pair never straddles train/val -- pass `mask` and
    `groups` to `probes.fit_layer_probes`.

    `kind="explicit"` conditions (the transfer tests) have train and test defined by the
    data itself, not by a random split -- pass `train_idx`/`test_idx` (local to the
    binary subset) to `probes.fit_layer_probes_explicit`.
    """
    expert = b.register == "expert"
    novice = b.register == "novice"
    stated = b.disclosure == "stated"
    demonstrated = b.disclosure == "demonstrated"

    conds = {
        "pooled": {
            "kind": "internal", "mask": np.ones(len(b), dtype=bool), "groups": b.paired_id,
        },
        "within-expert": {"kind": "internal", "mask": expert, "groups": b.paired_id},
        "within-novice": {"kind": "internal", "mask": novice, "groups": b.paired_id},
        "within-stated": {"kind": "internal", "mask": stated, "groups": b.paired_id},
        "cross-register": {
            "kind": "explicit", "train_mask": expert, "test_mask": novice,
        },
        "cross-register-rev": {
            "kind": "explicit", "train_mask": novice, "test_mask": expert,
        },
        "stated->demonstrated": {
            "kind": "explicit", "train_mask": stated, "test_mask": demonstrated,
        },
        "demonstrated->stated": {
            "kind": "explicit", "train_mask": demonstrated, "test_mask": stated,
        },
        "cross-both": {
            "kind": "explicit",
            "train_mask": expert & stated, "test_mask": novice & demonstrated,
        },
        # Robustness check called for in the handover ("also run the headline
        # conditions grouped by eedi_question_id"): the one condition where that
        # grouping is well-defined for every row is within-demonstrated (every
        # demonstrated row carries a real eedi_question_id), and it mirrors exactly
        # the Stage F diagnostic already run on this dataset (paired_id vs
        # eedi_question_id grouping, +24.6 vs +23.5). Not one of the 9 headline
        # conditions; reported alongside them as a diagnostic.
        "within-demonstrated": {
            "kind": "internal", "mask": demonstrated, "groups": b.paired_id,
        },
        "within-demonstrated-by-eedi": {
            "kind": "internal", "mask": demonstrated, "groups": b.eedi_qid,
        },
    }
    return conds
