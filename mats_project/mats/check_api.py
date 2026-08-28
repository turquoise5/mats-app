#!/usr/bin/env python
"""Diagnose OpenRouter generation failures. One call, full raw response printed.

    python check_api.py                      # uses $GEN_MODEL
    python check_api.py openai/gpt-4.1-mini  # or name a model

Tests the same call gen_data makes, with and without response_format, and dumps
everything: finish_reason, content, refusal, reasoning, and any error payload.
"""

from __future__ import annotations

import json
import os
import sys

from openai import OpenAI

MODEL = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "GEN_MODEL", "anthropic/claude-sonnet-4.5"
)

key = os.environ.get("OPENROUTER_API_KEY")
if not key:
    sys.exit("OPENROUTER_API_KEY is not set")

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)

MESSAGES = [
    {"role": "system", "content": "You generate JSON. Return only JSON, no prose, no fences."},
    {"role": "user", "content": (
        'Write a 1-message conversation. Return JSON of the form '
        '{"turns": [{"role": "user", "content": "..."}]}'
    )},
]


def attempt(label: str, **extra):
    print("\n" + "=" * 78)
    print(f"ATTEMPT: {label}   model={MODEL}")
    print("=" * 78)
    try:
        resp = client.chat.completions.create(
            model=MODEL, messages=MESSAGES, temperature=1.0, max_tokens=400, **extra
        )
    except Exception as e:
        print(f"  RAISED {type(e).__name__}: {e}")
        body = getattr(e, "response", None)
        if body is not None:
            try:
                print(f"  body: {body.text[:1200]}")
            except Exception:
                pass
        return False

    # OpenRouter can return a 200 with an error payload instead of choices.
    err = getattr(resp, "error", None) or (
        resp.model_dump().get("error") if hasattr(resp, "model_dump") else None
    )
    if err:
        print(f"  ERROR PAYLOAD: {err}")

    if not getattr(resp, "choices", None):
        print("  NO CHOICES. Full response:")
        print(json.dumps(resp.model_dump(), indent=2, default=str)[:2000])
        return False

    ch = resp.choices[0]
    msg = ch.message
    print(f"  finish_reason : {ch.finish_reason!r}")
    print(f"  content       : {msg.content!r}")
    print(f"  refusal       : {getattr(msg, 'refusal', None)!r}")
    print(f"  reasoning     : {str(getattr(msg, 'reasoning', None))[:300]!r}")
    print(f"  tool_calls    : {getattr(msg, 'tool_calls', None)!r}")
    print(f"  usage         : {resp.usage}")
    print(f"  provider      : {getattr(resp, 'provider', None)!r}")

    if not msg.content:
        print("\n  >>> content is EMPTY. This is the JSONDecodeError at char 0.")
        print("  Full raw response:")
        print(json.dumps(resp.model_dump(), indent=2, default=str)[:2500])
        return False

    try:
        json.loads(msg.content)
        print("\n  >>> parsed as JSON cleanly.")
    except json.JSONDecodeError as e:
        print(f"\n  >>> content is non-empty but not bare JSON ({e}).")
        print("  gen_data._extract_json() handles fences and preamble.")
    return True


ok_plain = attempt("no response_format")
ok_json = attempt("response_format=json_object", response_format={"type": "json_object"})

print("\n" + "=" * 78)
print("VERDICT")
print("=" * 78)
if ok_json and ok_plain:
    print("Both work. The original failure was something else — check the model ID.")
elif ok_plain and not ok_json:
    print(f"{MODEL} does NOT support response_format=json_object on OpenRouter.")
    print("Fix: export USE_JSON_MODE=0   (gen_data will prompt for JSON instead)")
elif ok_json and not ok_plain:
    print("json_object works, plain does not. Keep USE_JSON_MODE=1.")
else:
    print("Neither works. Likely a bad model ID, no credits, or a key problem.")
    print("Check: https://openrouter.ai/models  and  https://openrouter.ai/credits")
    print("Try a known-good OpenAI-family model: python check_api.py openai/gpt-4.1-mini")
