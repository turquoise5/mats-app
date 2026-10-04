"""Shared helpers: paths, prompt construction, API calls, JSONL I/O."""
import json
import os
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
TASKS = ROOT / "tasks"
PROMPTS = ROOT / "prompts"
RESULTS = ROOT / "results"

# OpenAI-compatible chat-completions endpoint. OpenRouter by default; override for Together etc.
API_BASE = os.environ.get("API_BASE", "https://openrouter.ai/api/v1")
API_KEY_ENV = os.environ.get("API_KEY_ENV", "OPENROUTER_API_KEY")


def task_ids():
    return sorted(p.name for p in TASKS.iterdir() if (p / "meta.json").exists())


def load_task(tid):
    d = TASKS / tid
    return {
        "id": tid,
        "spec": (d / "spec.md").read_text(),
        "stub": (d / "stub.py").read_text(),
        "tests": (d / "test_task.py").read_text(),
        **json.loads((d / "meta.json").read_text()),
    }


def build_messages(task, condition):
    para = (PROMPTS / f"condition_{condition}.txt").read_text().strip()
    system = (PROMPTS / "system_base.txt").read_text().replace("{CONDITION_PARAGRAPH}", para)
    user = (
        (PROMPTS / "task_template.txt").read_text()
        .replace("{SPEC}", task["spec"])
        .replace("{STUB}", task["stub"])
        .replace("{TESTS}", task["tests"])
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def chat(model, messages, max_tokens=8000, temperature=0.6, retries=4, **extra):
    """One chat-completions call. Returns (reasoning, content, raw_json)."""
    key = os.environ[API_KEY_ENV]
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature, "usage": {"include": True}, **extra}
    for attempt in range(retries + 1):
        try:
            r = requests.post(f"{API_BASE}/chat/completions", json=body, timeout=600,
                              headers={"Authorization": f"Bearer {key}"})
            if r.status_code == 200 and "choices" in r.json():
                raw = r.json()
                break
            err = f"HTTP {r.status_code}: {r.text[:300]}"
        except requests.RequestException as e:
            err = repr(e)
        if attempt == retries:
            raise RuntimeError(err)
        time.sleep(2 ** (attempt + 1))
    msg = raw["choices"][0]["message"]
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    # Some providers inline the trace as <think>...</think> in the content.
    m = re.search(r"<think>(.*?)</think>", content, re.S)
    if m and not reasoning:
        reasoning = m.group(1)
        content = content[m.end():]
    return reasoning.strip(), content.strip(), raw


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def append_jsonl(path, row):
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")
