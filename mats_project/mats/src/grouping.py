"""Content-corrected split groups for the Act 1 demonstrated subset.

`eedi_question_id` is an imperfect proxy for what a row actually contains. Some Eedi
questions put the content in the *answer options* rather than the stem ("One of these
equations has exactly one solution. Which is it?"), and the generator that produced
`contrast_v1.jsonl` was given the stem, not the options -- so it invented its own option
set. Question 1158 and question 552 have byte-identical stems, and their demonstrated
rows converge on the same equations.

Grouping a split on `eedi_question_id` therefore lets content straddle the train/test
boundary. This module merges question ids whose demonstrated rows share equations, and
returns a group label to split on instead.
"""

from __future__ import annotations

import re

_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹",
                     "0123456789")
_TERM = r"(?:\d+|(?<![A-Za-z])[A-Za-z](?![A-Za-z])|[+\-*/^().√±])"
# Equations never span a newline, and a chain (a = b = c) is split into adjacent pairs.
_EQ = re.compile(
    r"(?<![A-Za-z0-9])%s(?:[ \t]*%s)*(?:[ \t]*=[ \t]*%s(?:[ \t]*%s)*)+" % ((_TERM,) * 4)
)


def _prep(s: str) -> str:
    s = re.sub(r"([A-Za-z0-9\)])([⁰¹²³⁴-⁹]+)",
               lambda m: m.group(1) + "^" + m.group(2).translate(_SUP), s)
    return re.sub(r"[−–—]", "-", s)


def _canon(side: str) -> str:
    side = re.sub(r"\s+", "", side)
    side = re.sub(r"[A-Za-z]", "v", side)          # variable-agnostic
    return side.replace("(", "").replace(")", "").strip(".+*/^,;:")


def equations(text: str) -> set[str]:
    """Normalised equations in `text`, insensitive to variable name, spacing, parens,
    and which side of the `=` each half sits on."""
    out: set[str] = set()
    for m in _EQ.findall(_prep(text)):
        parts = [_canon(p) for p in m.split("=")]
        for lhs, rhs in zip(parts, parts[1:]):
            if not lhs or not rhs:
                continue
            if not re.search(r"\d", lhs + rhs) or len(lhs) + len(rhs) < 3:
                continue
            out.add("=".join(sorted([lhs, rhs])))
    return out


class _UnionFind:
    def __init__(self, keys):
        self.parent = {k: k for k in keys}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def merge_questions(rows, text_of, min_shared: int = 5):
    """Merge `eedi_question_id`s whose rows share >= `min_shared` equations.

    Returns (group_of_qid, linked_pairs) where `group_of_qid` maps every question id to
    a stable group label and `linked_pairs` records the merges with their evidence.
    """
    eqs_by_q: dict[str, set[str]] = {}
    for r in rows:
        qid = str(r["eedi_question_id"])
        eqs_by_q.setdefault(qid, set()).update(equations(text_of(r)))

    qids = sorted(eqs_by_q)
    uf = _UnionFind(qids)
    linked = []
    for i, a in enumerate(qids):
        for b in qids[i + 1:]:
            shared = eqs_by_q[a] & eqs_by_q[b]
            if len(shared) >= min_shared:
                uf.union(a, b)
                linked.append({"a": a, "b": b, "n_shared_equations": len(shared),
                               "examples": sorted(shared)[:5]})

    # Name each group after its lowest member id so labels are stable across runs.
    members: dict[str, list[str]] = {}
    for q in qids:
        members.setdefault(uf.find(q), []).append(q)
    group_of_qid = {q: "+".join(sorted(ms, key=int))
                    for ms in members.values() for q in ms}
    return group_of_qid, linked
