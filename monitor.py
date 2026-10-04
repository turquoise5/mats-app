"""Ask a separate monitor model whether each reasoning trace verbalizes the hack.

The monitor sees the reasoning trace only (not the final answer or files). Results are cached in
results/monitor.jsonl by run_id.

    python monitor.py --model openai/gpt-4.1-mini
"""
import argparse
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from common import PROMPTS, RESULTS, append_jsonl, chat, read_jsonl

RAW, MON = RESULTS / "raw.jsonl", RESULTS / "monitor.jsonl"
lock = threading.Lock()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/gpt-4.1-mini")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    template = (PROMPTS / "monitor.txt").read_text()
    done = {r["run_id"] for r in read_jsonl(MON)}
    rows = [r for r in read_jsonl(RAW) if r["run_id"] not in done]

    def work(r):
        prompt = template.replace("{REASONING}", r["reasoning"] or "(empty)")
        try:
            _, content, raw = chat(args.model, [{"role": "user", "content": prompt}],
                                   max_tokens=300, temperature=0)
        except Exception as e:
            print(f"FAILED {r['run_id']}: {e}")
            return
        m = re.search(r"ANSWER:\s*(YES|NO)", content, re.I) or re.search(r"\b(YES|NO)\b", content)
        out = {"run_id": r["run_id"], "monitor_model": args.model,
               "verbalized": (m.group(1).upper() == "YES") if m else None,
               "monitor_output": content, "cost": raw.get("usage", {}).get("cost")}
        with lock:
            append_jsonl(MON, out)
            print(f"{r['task_id']:24s} {r['condition']} verbalized={out['verbalized']}")

    with ThreadPoolExecutor(args.workers) as ex:
        list(ex.map(work, rows))


if __name__ == "__main__":
    main()
