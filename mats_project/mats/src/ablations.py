"""Act 1 text ablations A / B / CTRL on the demonstrated subset of contrast_v1.

Builds the four ablated variants described in `handover_cpu_ablations.md`:

    A     the user's final answer (correct answer + Eedi distractors) -> `[ANS]`
    B     whole sentences containing metacognitive / verification cues removed
    AB    both
    CTRL  random whole sentences removed, per-row length-matched to B

`contrast_v1.jsonl` itself is never written to. Every variant carries the same `id`
values in the same order as the demonstrated subset of the original.
"""

from __future__ import annotations

import random
import re

# --------------------------------------------------------------------------------------
# Answer-string canonicalisation (Ablation A)
# --------------------------------------------------------------------------------------

_ARRAY_OPEN = re.compile(r"\\begin\s*\{array\}\s*(?:\{[^{}]*\})?")
_TEXT_CMD = re.compile(r"\\(?:text|mathrm|mbox)\s*\{([^{}]*)\}")
_FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SQRT = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_SUP = re.compile(r"\^\s*\{([^{}]*)\}")
_LATEX_CMD = re.compile(r"\\[a-zA-Z]+")


def latex_to_plain(s: str) -> str:
    """Turn an Eedi answer cell into flat, human-typeable text.

    `\\( \\frac{-6 \\pm \\sqrt{12}}{3} \\)`  ->  `(-6 ± sqrt(12))/(3)`
    `\\( \\begin{array}{c}x=-4 \\\\ \\text { and } \\\\ x=3\\end{array} \\)` -> `x=-4 and x=3`
    """
    s = s.replace("\r", " ").replace("\n", " ")
    s = s.replace("\\\\", " ; ")        # array row separator; must go before \begin/\text
    s = _ARRAY_OPEN.sub(" ", s).replace("\\end{array}", " ")
    s = s.replace("\\(", " ").replace("\\)", " ").replace("\\[", " ").replace("\\]", " ")
    s = _TEXT_CMD.sub(r" \1 ", s)
    prev = None
    while prev != s:                    # nested fractions
        prev = s
        s = _FRAC.sub(r"(\1)/(\2)", s)
    s = _SQRT.sub(r"sqrt(\1)", s)
    s = s.replace("\\pm", "\u00b1").replace("\\mp", "\u2213")
    s = s.replace("\\times", "*").replace("\\cdot", "*").replace("\\div", "/")
    s = s.replace("\\left", " ").replace("\\right", " ")
    s = _SUP.sub(r"^\1", s)
    s = _LATEX_CMD.sub(" ", s)
    s = s.replace("{", " ").replace("}", " ").replace("$", " ")
    return re.sub(r"\s+", " ", s).strip()


_SPLIT_CONJ = re.compile(r"\s+(?:and|or)\s+|\s*;\s*|\s*,\s*", re.I)

# Fragments we refuse to search for: bare option letters (question 452's answer cells are
# literally `A`..`D`), and connective words left over from splitting a multi-part answer.
_STOPWORD_FRAGMENTS = {
    "and", "or", "the", "is", "are", "to", "of", "a", "an", "step", "steps", "both",
    "only", "neither", "correct", "incorrect", "true", "false", "solve", "more",
}


def _is_usable_candidate(c: str) -> bool:
    low = c.lower().strip()
    if not low:
        return False
    if low in _STOPWORD_FRAGMENTS:
        return False
    if re.fullmatch(r"[a-z]", low):     # single option letter -- far too generic
        return False
    if re.fullmatch(r"[^\w]+", low):    # punctuation only
        return False
    return True


def answer_candidates(raw: str) -> list[str]:
    """Canonical surface forms to look for, longest first.

    Returns the whole answer plus its conjunct components (`x=-4 and x=3` also yields
    `x=-4` and `x=3`), dropping fragments too generic to match safely.

    Components are only taken from *algebraic* answers. Splitting a prose option such as
    `Both Jo and Paul` would otherwise yield the bare name `Paul`, and replacing every
    occurrence of a character's name removes the problem's cast, not its answer.
    """
    whole = latex_to_plain(raw)
    if not whole:
        return []
    cands = [whole]
    for part in _SPLIT_CONJ.split(whole):
        part = (part or "").strip()
        if part and part != whole and re.search(r"\d", part):
            cands.append(part)
    out: list[str] = []
    seen: set[str] = set()
    for c in sorted(cands, key=len, reverse=True):
        key = c.lower()
        if key in seen or not _is_usable_candidate(c):
            continue
        seen.add(key)
        out.append(c)
    return out


# Character-class equivalences seen in LLM-written student text.
_CHAR_ALT = {
    "-": r"[-\u2212\u2013\u2014]",
    "/": r"[/\u2044\u2215]",
    "*": r"[*x\u00d7\u00b7]",
    "\u00b1": r"(?:\u00b1|\+\s*/\s*\-|\+\s*or\s*\-)",
    "(": r"\(?",
    ")": r"\)?",
}
_SUPERSCRIPT = {"0": "\u2070", "1": "\u00b9", "2": "\u00b2", "3": "\u00b3", "4": "\u2074",
                "5": "\u2075", "6": "\u2076", "7": "\u2077", "8": "\u2078", "9": "\u2079"}

_ATOM = re.compile(r"[A-Za-z]+|\d+|\s+|.", re.S)


def build_answer_regex(cand: str, any_variable: bool = False):
    """Compile a whitespace- and glyph-tolerant pattern for one canonical answer.

    `x=-4` matches `x = -4`, `x=\u22124`, `x=- 4`; `t^2-16=0` matches `t\u00b2 - 16 = 0`;
    `2/7` matches `2 / 7` and `2\u20447`.

    With `any_variable=True` every single-letter atom becomes `[a-z]`, so the Eedi cell
    `t^2+6t+9=0` also matches the same equation written in `x`. Only used as a fallback
    when the literal form is absent from the row (see `row_answer_patterns`).
    """
    atoms = [a for a in _ATOM.findall(cand) if not a.isspace()]
    if not atoms:
        return None
    pieces: list[str] = []
    i = 0
    while i < len(atoms):
        a = atoms[i]
        if a == "^" and i + 1 < len(atoms) and atoms[i + 1].isdigit():
            digits = atoms[i + 1]
            sup = "".join(_SUPERSCRIPT[d] for d in digits)
            pieces.append("(?:\\^\\s*" + digits + "|" + sup + ")")
            i += 2
            continue
        if a.lower() == "sqrt":
            pieces.append(r"(?:sqrt|\u221a)")
        elif a in _CHAR_ALT:
            pieces.append(_CHAR_ALT[a])
        elif any_variable and len(a) == 1 and a.isalpha():
            pieces.append("[a-z]")
        elif a.isalnum():
            pieces.append(re.escape(a))
        elif a in ".,":
            pieces.append(re.escape(a) + "?")
        else:
            pieces.append(re.escape(a))
        i += 1
    body = r"\s*".join(pieces)
    lead = r"(?<![\w.])" if atoms[0].isalnum() else r"(?<!\w)"
    trail = r"(?!\w)" if atoms[-1].isalnum() else ""
    try:
        return re.compile(lead + body + trail, re.I)
    except re.error:
        return None


def question_answer_patterns(question):
    """(literal, variable_agnostic) pattern lists for one `eedi.EediQuestion`.

    Both the correct answer and *all* labelled distractors are covered, so answer
    identity is neutralised identically in `gap` and `knows` rows -- neither class can
    be recognised by which of the question's options survives in the text.
    """
    cands: list[str] = []
    for raw in [question.correct_answer_text] + [d.text for d in question.distractors]:
        cands.extend(answer_candidates(raw))
    seen: set[str] = set()
    uniq: list[str] = []
    for c in sorted(cands, key=len, reverse=True):
        if c.lower() in seen:
            continue
        seen.add(c.lower())
        uniq.append(c)
    literal = [p for p in (build_answer_regex(c) for c in uniq) if p is not None]
    loose = [
        p for p in (build_answer_regex(c, any_variable=True) for c in uniq
                    if re.search(r"\d", c) and re.search(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])", c))
        if p is not None
    ]
    return literal, loose


ANS_TOKEN = "[ANS]"
_ANS_RUN = re.compile(r"(?:\[ANS\][ \t]*){2,}")


def apply_ablation_a(text: str, patterns) -> tuple[str, int]:
    """Replace every answer surface form with `[ANS]`. Returns (new_text, n_hits)."""
    hits = 0
    for pat in patterns:                # patterns arrive longest-candidate-first
        text, n = pat.subn(ANS_TOKEN, text)
        hits += n
    text = _ANS_RUN.sub(ANS_TOKEN + " ", text)
    return text, hits


# --------------------------------------------------------------------------------------
# Sentence handling (Ablations B and CTRL)
# --------------------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"((?<=[.!?\u2026])[ \t]+|\n+)")


def split_sentences(text: str) -> list[tuple[str, str]]:
    """Split into (sentence, trailing_separator) pairs. `join_sentences` rebuilds text."""
    parts = _SENT_SPLIT.split(text)
    out: list[tuple[str, str]] = []
    for i in range(0, len(parts), 2):
        body = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        if body == "" and sep == "":
            continue
        out.append((body, sep))
    return out


def join_sentences(pairs) -> str:
    return "".join(b + s for b, s in pairs)


# --------------------------------------------------------------------------------------
# Ablation B -- metacognitive / verification cue lexicon
# --------------------------------------------------------------------------------------

# Seed lexicon, straight from the handover (fitted TF-IDF coefficients on this dataset).
CUES_KNOWS_SEED = [
    "verification", "verify", "verified", "verifying", "discriminant", "factoring",
    "factorise", "factorize", "factored", "yields", "substitute", "substituting",
    "substituted", "plug back", "plugging back", "check", "checked", "checking",
    "confirm", "confirms", "original equation", "sanity",
]
CUES_GAP_SEED = [
    "circled", "circle", "option", "options", "picked", "chose", "my answer",
    "i wrote", "the answer is", "so the answer",
]

# Added by this run: morphological neighbours of seed terms and near-synonyms that
# actually occur in the demonstrated text. Reported in ablation_stats.json as
# `lexicon_added` so the GPU agent knows the lexicon is wider than the seed.
CUES_KNOWS_ADDED = [
    "verifies", "discriminants", "factorisation", "factorization", "factorising",
    "factorizing", "factorised", "factorized", "yield", "yielding", "substitution",
    "substitutes", "plug it back", "plugging it back", "plug in", "plugged",
    "checks", "check back", "double check", "double-check", "recheck", "re-check",
    "cross-check", "cross check", "confirmed", "confirming", "confirmation",
    "sanity check", "original problem", "into the original",
    # "i put" reads as gap-side answer-reporting but in this data it is almost always
    # substitution ("i put a = 2 in and got ..."), so it belongs on the knows side.
    "i put",
]
CUES_GAP_ADDED = [
    "circling", "circles", "i picked", "i chose", "choosing", "selected", "i selected",
    "ticked", "tick", "my final answer", "i answered", "wrote down",
    "the answer was", "i went with",
]

CUES_KNOWS = CUES_KNOWS_SEED + CUES_KNOWS_ADDED
CUES_GAP = CUES_GAP_SEED + CUES_GAP_ADDED
ALL_CUES = CUES_KNOWS + CUES_GAP
LEXICON_ADDED = CUES_KNOWS_ADDED + CUES_GAP_ADDED


def _cue_pattern(cues) -> re.Pattern:
    alts = sorted(cues, key=len, reverse=True)
    body = "|".join(re.escape(c).replace(r"\ ", r"\s+") for c in alts)
    return re.compile("(?<!\\w)(?:" + body + ")(?!\\w)", re.I)


CUE_RE = _cue_pattern(ALL_CUES)

EMPTY_PLACEHOLDER = "[EMPTY]"


def apply_ablation_b(text: str) -> tuple[str, int]:
    """Drop whole sentences containing a cue term. Returns (new_text, n_dropped)."""
    pairs = split_sentences(text)
    kept = [(b, s) for b, s in pairs if not CUE_RE.search(b)]
    return join_sentences(kept).strip(), len(pairs) - len(kept)


# --------------------------------------------------------------------------------------
# Ablation CTRL -- random sentences, length-matched to B
# --------------------------------------------------------------------------------------

def ctrl_eligible_chars(turns_text) -> int:
    """Total characters CTRL is allowed to delete from a row (all non-cue sentences)."""
    return sum(
        len(b) + len(sep)
        for t in turns_text
        for b, sep in split_sentences(t)
        if not CUE_RE.search(b) and b.strip()
    )


def apply_ablation_ctrl(turns_text, target_chars: int, rng: random.Random,
                        tol: float = 0.15):
    """Delete random non-cue sentences across a row until ~`target_chars` are gone.

    Returns (new_texts, chars_removed, n_sentences_dropped). Sentences B's cue rule
    selected are never eligible, so CTRL removes *different* text of similar length.
    """
    per_turn = [split_sentences(t) for t in turns_text]
    eligible = [
        (ti, si, len(b) + len(sep))
        for ti, pairs in enumerate(per_turn)
        for si, (b, sep) in enumerate(pairs)
        if not CUE_RE.search(b) and b.strip()
    ]
    if target_chars <= 0 or not eligible:
        return list(turns_text), 0, 0

    hi = target_chars * (1.0 + tol)
    lo = target_chars * (1.0 - tol)
    best = None
    for _ in range(200):
        # Randomised hill-climb: walk a shuffled eligible list and take any sentence
        # that moves the running total closer to the target. Allowing a single overshoot
        # matters -- rows with few, long sentences have no under-target subset at all.
        order = eligible[:]
        rng.shuffle(order)
        chosen = []
        total = 0
        for ti, si, n in order:
            if abs(total + n - target_chars) < abs(total - target_chars):
                chosen.append((ti, si))
                total += n
        err = abs(total - target_chars)
        if best is None or err < best[0]:
            best = (err, chosen, total)
        if lo <= total <= hi:
            break
    _, chosen, removed = best
    drop = set(chosen)
    new_texts = [
        join_sentences([p for si, p in enumerate(pairs) if (ti, si) not in drop]).strip()
        for ti, pairs in enumerate(per_turn)
    ]
    return new_texts, removed, len(drop)
