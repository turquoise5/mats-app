"""Shared configuration, paths, and run logging for the MATS user-model project."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------

ROOT = Path(os.environ.get("MATS_ROOT", Path(__file__).resolve().parent.parent))

DATA = ROOT / "data"
RAW = DATA / "raw"
CONTRAST = DATA / "contrast"
CACHE = ROOT / "cache"
RESULTS = ROOT / "results"
FIGS = RESULTS / "figs"
RUNS_LOG = RESULTS / "runs.jsonl"
NOTES = ROOT / "notes.md"

for _d in (DATA, RAW, CONTRAST, CACHE, RESULTS, FIGS):
    _d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------------------
# Global settings
# --------------------------------------------------------------------------------------

SEED = 0

MODEL_ID = os.environ.get("MODEL_ID", "Qwen/Qwen3-8B")

# Generator model for synthetic data, via OpenRouter.
# VERIFY this ID is current at https://openrouter.ai/models before a long run.
GEN_MODEL = os.environ.get("GEN_MODEL", "anthropic/claude-sonnet-4.5")


# --------------------------------------------------------------------------------------
# Act 0 attribute taxonomy (subset of TalkTuner, arXiv 2406.07882)
# --------------------------------------------------------------------------------------

ATTRIBUTES = {
    "education": {
        "subcategories": ["some schooling", "high school", "college and above"],
        "elicit_prefix": "I think the education level of this user is",
        "description": "the highest level of formal education the user has completed",
    },
    "age": {
        "subcategories": ["child", "adolescent", "adult", "older adult"],
        "elicit_prefix": "I think the age group of this user is",
        "description": "the user's age group",
    },
}

N_PER_SUBCATEGORY = int(os.environ.get("N_PER_SUBCATEGORY", 150))

READ_POSITIONS = ("natural", "elicited")


# --------------------------------------------------------------------------------------
# Act 1 — knowledge-of-concept elicitation (contrast_v1.jsonl)
# --------------------------------------------------------------------------------------

CONTRAST_FILE = CONTRAST / "contrast_v1.jsonl"

# No Act 0 attribute fits the knowledge target -- it needs its own elicit prefix,
# formatted per concept (contrast_v1.jsonl rows carry the concept description in
# `concept`). act1_handover_probing.md §5 requires recording the exact string used.
ACT1_ELICIT_PREFIX = "I think this user's understanding of {concept} is"


# --------------------------------------------------------------------------------------
# Run logging (append-only)
# --------------------------------------------------------------------------------------

def config_hash(cfg: dict) -> str:
    """Stable short hash of a config dict, for tying results to their settings."""
    blob = json.dumps(cfg, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def log_run(act: str, experiment: str, config: dict, metrics: dict) -> dict:
    """Append one experiment record to results/runs.jsonl.

    Every experiment must call this. Results not in the log did not happen.
    """
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "act": act,
        "experiment": experiment,
        "model": MODEL_ID,
        "seed": SEED,
        "config": config,
        "config_hash": config_hash(config),
        "metrics": metrics,
    }
    with open(RUNS_LOG, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    print(f"[log_run] {act}/{experiment} -> {RUNS_LOG} ({record['config_hash']})")
    return record


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------

def read_jsonl(path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows) -> None:
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    print(f"[write_jsonl] wrote {len(rows)} rows -> {path}")


def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78)
