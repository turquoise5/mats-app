"""Tests for src/act1_data.py: the lenient contrast_v1.jsonl reader and the transfer
condition builder. No GPU, no downloads."""

from __future__ import annotations

import json

import numpy as np
import pytest

from src import act1_data as AD


def _row(id_, paired_id, spec_id, concept_slug, register, disclosure, knowledge_state,
         eedi_question_id=None, first="hello", second=None):
    turns = [{"role": "user", "content": first}]
    if second:
        turns.append({"role": "assistant", "content": "ok"})
        turns.append({"role": "user", "content": second})
    return {
        "id": id_, "paired_id": paired_id, "spec_id": spec_id,
        "concept": concept_slug.replace("_", " "), "concept_slug": concept_slug,
        "knowledge_state": knowledge_state, "disclosure": disclosure, "register": register,
        "eedi_question_id": eedi_question_id, "eedi_misconception": None,
        "propositions": [], "turns": turns, "seed": 0,
    }


# ---------------------------------------------------------------------------------
# Lenient reader
# ---------------------------------------------------------------------------------

def test_load_rows_handles_embedded_raw_newline(tmp_path):
    """json.dumps always escapes \\n, but a clean-looking hand-built fixture (or, per
    the real file, some other writer) can still put a literal newline inside a string.
    Plain json.loads-per-line chokes on this; the lenient reader must not."""
    rows = [_row("a", "p1", "s1", "c", "novice", "stated", "knows", first="line one\nline two")]
    path = tmp_path / "contrast_v1.jsonl"
    path.write_text(json.dumps(rows[0]) + "\n")
    stats = tmp_path / "contrast_v1_stats.json"
    stats.write_text(json.dumps({"final_n": 1}))

    out = AD.load_rows(path)
    assert len(out) == 1
    assert out[0]["turns"][0]["content"] == "line one\nline two"


def test_load_rows_stops_before_stale_trailing_duplicate(tmp_path):
    """Reproduces the real contrast_v1.jsonl corruption: N good records, then a large
    stale re-write of the same records glued on with no separator, then a torn
    fragment. The reader must recover exactly the good prefix and stop there."""
    good = [
        json.dumps(_row("a", "p1", "s1", "c", "novice", "stated", "knows")),
        json.dumps(_row("b", "p1", "s1", "c", "expert", "stated", "knows")),
    ]
    torn_fragment = '"leftover fragment with no opening brace'
    path = tmp_path / "contrast_v1.jsonl"
    path.write_text("\n".join(good) + "\n" + torn_fragment)
    stats = tmp_path / "contrast_v1_stats.json"
    stats.write_text(json.dumps({"final_n": 2}))

    out = AD.load_rows(path)
    assert [r["id"] for r in out] == ["a", "b"]


def test_load_rows_raises_on_count_mismatch(tmp_path):
    """If the parsed count doesn't match the generation run's own reported final_n,
    fail loudly rather than silently handing back the wrong rows."""
    row = _row("a", "p1", "s1", "c", "novice", "stated", "knows")
    path = tmp_path / "contrast_v1.jsonl"
    path.write_text(json.dumps(row) + "\n")
    stats = tmp_path / "contrast_v1_stats.json"
    stats.write_text(json.dumps({"final_n": 5}))

    with pytest.raises(RuntimeError, match="final_n"):
        AD.load_rows(path)


def test_load_rows_raises_on_duplicate_ids(tmp_path):
    rows = [
        _row("a", "p1", "s1", "c", "novice", "stated", "knows"),
        _row("a", "p2", "s2", "c", "expert", "stated", "gap"),
    ]
    path = tmp_path / "contrast_v1.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    stats = tmp_path / "contrast_v1_stats.json"
    stats.write_text(json.dumps({"final_n": 2}))

    with pytest.raises(RuntimeError, match="[Dd]uplicate"):
        AD.load_rows(path)


# ---------------------------------------------------------------------------------
# Index / binary subset
# ---------------------------------------------------------------------------------

def _toy_rows():
    rows = [
        _row("k1n", "p1", "s1", "c", "novice", "stated", "knows"),
        _row("k1e", "p1", "s1", "c", "expert", "stated", "knows"),
        _row("g1n", "p2", "s2", "c", "novice", "demonstrated", "gap", eedi_question_id="q1"),
        _row("g1e", "p2", "s2", "c", "expert", "demonstrated", "gap", eedi_question_id="q1"),
        _row("u1n", "p3", "s3", "c", "novice", "none", "undisclosed"),
    ]
    return rows


def test_index_binary_mask_excludes_undisclosed():
    idx = AD.Index(_toy_rows())
    assert len(idx.binary_idx) == 4
    assert len(idx.undisclosed_idx) == 1
    b = idx.binary()
    assert set(b.y.tolist()) == {0, 1}


def test_build_conditions_transfer_masks_are_disjoint_and_nonempty():
    idx = AD.Index(_toy_rows())
    b = idx.binary()
    conds = AD.build_conditions(b)
    for name, spec in conds.items():
        if spec["kind"] != "explicit":
            continue
        tr = np.where(spec["train_mask"])[0]
        te = np.where(spec["test_mask"])[0]
        assert len(set(tr) & set(te)) == 0, f"{name}: train/test overlap"


def test_build_conditions_cross_register_matches_register_field():
    idx = AD.Index(_toy_rows())
    b = idx.binary()
    conds = AD.build_conditions(b)
    tr = np.where(conds["cross-register"]["train_mask"])[0]
    te = np.where(conds["cross-register"]["test_mask"])[0]
    assert set(b.register[tr]) == {"expert"}
    assert set(b.register[te]) == {"novice"}
