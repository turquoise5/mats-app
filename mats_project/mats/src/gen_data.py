"""Generate the TalkTuner replication conversation set via OpenRouter.

Small deliberately: 150 conversations per subcategory, two attributes. Enough to show the
effect replicates, not enough to beat the original. This step needs no GPU — run it
before starting the pod.
"""

from __future__ import annotations

import json
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI
from tqdm import tqdm

from . import config as C

USE_JSON_MODE = os.environ.get("USE_JSON_MODE", "1") not in ("0", "false", "False")

_client = None


def client() -> OpenAI:
    global _client
    if _client is None:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("Set OPENROUTER_API_KEY.")
        _client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
    return _client


TOPICS = [
    "cooking", "a job application", "a health question", "money and budgeting",
    "a computer problem", "travel plans", "a school or work assignment",
    "a hobby", "a relationship question", "understanding some news",
    "fixing something around the house", "a legal or bureaucratic question",
    "exercise and fitness", "buying something expensive", "a pet",
    "learning a new skill", "a maths or science question", "writing something",
]

SYSTEM = (
    "You generate realistic conversations between a person and an AI assistant, for use "
    "as research data. Respond with a single JSON object and nothing else — no prose "
    "before or after it, no markdown code fences. Begin your reply with the character {."
)

TEMPLATE = """Write a short conversation between a user and an AI assistant.

The user's {description} is: {subcategory}.

Constraints:
- Topic: {topic}
- {turn_spec}
- The conversation must END with a user message.
- {disclosure}
- Do not mention this instruction, and do not have the assistant comment on the user's
  {attribute}.
- The user's messages should read like a real person typing, not like a writing sample.

Return JSON exactly of the form:
{{"turns": [{{"role": "user", "content": "..."}}, {{"role": "assistant", "content": "..."}}, {{"role": "user", "content": "..."}}]}}"""

EXPLICIT = (
    "The user should state or clearly reveal their {description} directly in one of "
    "their messages."
)
IMPLICIT = (
    "The user must NOT state their {description}. It should only be inferable from "
    "vocabulary, concerns, phrasing, and what they take for granted."
)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _shingles(text: str, n: int = 5) -> set:
    words = _normalise(text).split()
    return {" ".join(words[i : i + n]) for i in range(max(1, len(words) - n + 1))}


def _extract_json(text: str | None) -> dict:
    """Parse JSON out of a model response that may be fenced or have preamble.

    Not every model honours response_format. Anthropic models in particular have no
    global JSON mode — they use forced tool calls instead — so via OpenRouter a
    json_object request can come back empty or as fenced markdown. Rather than depend
    on the provider, we ask for JSON in the prompt and parse defensively.
    """
    if not text or not text.strip():
        raise ValueError("model returned empty content")

    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)

    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass

    # Fall back to the first balanced {...} block.
    start = t.find("{")
    if start == -1:
        raise ValueError(f"no JSON object found in: {t[:200]!r}")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(t[start:], start):
        if esc:
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(t[start : i + 1])
    raise ValueError(f"unbalanced JSON in: {t[:200]!r}")


def _describe_failure(resp) -> str:
    """Everything useful about a bad response, for the log."""
    bits = []
    err = getattr(resp, "error", None)
    if err:
        bits.append(f"error={err}")
    if not getattr(resp, "choices", None):
        bits.append("no choices returned")
        return "; ".join(bits)
    ch = resp.choices[0]
    bits.append(f"finish_reason={ch.finish_reason!r}")
    bits.append(f"content={ch.message.content!r}")
    for attr in ("refusal", "reasoning"):
        val = getattr(ch.message, attr, None)
        if val:
            bits.append(f"{attr}={str(val)[:160]!r}")
    bits.append(f"provider={getattr(resp, 'provider', None)!r}")
    return "; ".join(bits)


def _one(attribute: str, subcategory: str, idx: int, seed: int) -> dict | None:
    meta = C.ATTRIBUTES[attribute]
    rng = random.Random((seed, attribute, subcategory, idx).__hash__() & 0xFFFFFFFF)
    topic = rng.choice(TOPICS)
    explicit = idx % 2 == 0
    n_turns = rng.choice([1, 3, 3, 5])  # user-first, user-last
    turn_spec = (
        f"Exactly {n_turns} messages total, alternating user/assistant, starting with user."
    )
    disclosure = (EXPLICIT if explicit else IMPLICIT).format(
        description=meta["description"]
    )

    prompt = TEMPLATE.format(
        description=meta["description"],
        subcategory=subcategory,
        topic=topic,
        turn_spec=turn_spec,
        disclosure=disclosure,
        attribute=attribute,
    )

    kwargs = {}
    if USE_JSON_MODE:
        kwargs["response_format"] = {"type": "json_object"}

    resp = None
    try:
        resp = client().chat.completions.create(
            model=C.GEN_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=1.0,
            max_tokens=900,
            **kwargs,
        )
        turns = _extract_json(resp.choices[0].message.content)["turns"]
    except Exception as e:  # noqa: BLE001 - log the evidence, drop the sample
        detail = _describe_failure(resp) if resp is not None else "no response object"
        print(f"  [gen] FAILED {attribute}/{subcategory}/{idx}: "
              f"{type(e).__name__}: {e} | {detail}")
        return None

    if not turns or turns[-1].get("role") != "user":
        return None
    if any(t.get("role") not in ("user", "assistant") or not t.get("content") for t in turns):
        return None

    return {
        "id": f"{attribute}__{subcategory.replace(' ', '_')}__{idx}",
        "attribute": attribute,
        "subcategory": subcategory,
        "turns": turns,
        "explicit": explicit,
        "topic": topic,
        "seed": seed,
    }


def dedupe(rows: list[dict], jaccard_threshold: float = 0.5) -> tuple[list[dict], dict]:
    """Drop exact and near-duplicate conversations (by first user turn).

    Duplicates straddling a train/val split are the most common source of an inflated
    probe score, so this runs before anything else touches the data.
    """
    kept, seen_exact, seen_shingles = [], set(), []
    n_exact = n_near = 0

    for r in rows:
        first = r["turns"][0]["content"]
        key = _normalise(first)
        if key in seen_exact:
            n_exact += 1
            continue
        sh = _shingles(first)
        if any(
            len(sh & prev) / max(1, len(sh | prev)) > jaccard_threshold
            for prev in seen_shingles
        ):
            n_near += 1
            continue
        seen_exact.add(key)
        seen_shingles.append(sh)
        kept.append(r)

    stats = {"n_in": len(rows), "n_out": len(kept), "n_exact": n_exact, "n_near": n_near}
    print(f"  [dedupe] {stats}")
    return kept, stats


def generate_all(n_per_subcategory: int | None = None, workers: int = 12) -> list[dict]:
    n = n_per_subcategory or C.N_PER_SUBCATEGORY
    jobs = [
        (attr, sub, i)
        for attr, meta in C.ATTRIBUTES.items()
        for sub in meta["subcategories"]
        for i in range(n)
    ]
    print(f"[gen] {len(jobs)} conversations via {C.GEN_MODEL}")

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, a, s, i, C.SEED): (a, s, i) for a, s, i in jobs}
        for fut in tqdm(as_completed(futs), total=len(futs)):
            r = fut.result()
            if r is not None:
                rows.append(r)

    print(f"[gen] {len(rows)}/{len(jobs)} succeeded")
    if not rows:
        raise RuntimeError(
            "Every generation call failed. Do not proceed — run `python check_api.py` "
            "to see the raw response. Most likely: the model does not support "
            "response_format=json_object (try USE_JSON_MODE=0), the model ID is wrong, "
            "or the account is out of credits."
        )
    if len(rows) < 0.5 * len(jobs):
        print(f"  [gen] WARNING: over half the calls failed. Inspect the errors above "
              f"before spending money on the full run.")
    rows, dedupe_stats = dedupe(rows)

    counts = {}
    for r in rows:
        counts.setdefault(r["attribute"], {}).setdefault(r["subcategory"], 0)
        counts[r["attribute"]][r["subcategory"]] += 1
    print(f"[gen] label counts after dedupe: {json.dumps(counts, indent=2)}")

    for attr, subs in counts.items():
        lo, hi = min(subs.values()), max(subs.values())
        if lo < 0.6 * hi:
            print(
                f"  [gen] WARNING: {attr} is imbalanced ({subs}). Probe accuracy will be "
                "hard to interpret; consider regenerating the thin subcategories."
            )

    out = C.RAW / "talktuner_repro.jsonl"
    C.write_jsonl(out, rows)

    print("\n[gen] two random samples:")
    for r in random.Random(C.SEED).sample(rows, min(2, len(rows))):
        print(f"\n--- {r['id']} (explicit={r['explicit']}) ---")
        for t in r["turns"]:
            print(f"  {t['role']}: {t['content'][:220]}")

    C.log_run(
        act="0",
        experiment="generate_talktuner_repro",
        config={"gen_model": C.GEN_MODEL, "n_per_subcategory": n, "topics": len(TOPICS)},
        metrics={"n_requested": len(jobs), "counts": counts, **dedupe_stats},
    )
    return rows
