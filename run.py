"""Run the model under test on every (task, condition, sample) cell and log raw responses.

Responses are cached by run_id in results/raw.jsonl, so re-running only fills in missing cells.

    python run.py --tag pilot --conditions P --n 3
    python run.py --tag main --conditions P M --n 25
"""
import argparse
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from common import RESULTS, append_jsonl, build_messages, chat, load_task, read_jsonl, task_ids

RAW = RESULTS / "raw.jsonl"
lock = threading.Lock()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="deepseek/deepseek-r1")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--conditions", nargs="+", default=["P", "M"])
    ap.add_argument("--tasks", nargs="+", default=None)
    ap.add_argument("--n", type=int, default=3, help="samples per task per condition")
    ap.add_argument("--provider", default="Novita", help="pin one OpenRouter provider so all runs are served alike")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--budget", type=float, default=5.0, help="stop if logged spend (USD) exceeds this")
    args = ap.parse_args()

    RESULTS.mkdir(exist_ok=True)
    done = {r["run_id"] for r in read_jsonl(RAW)}
    jobs = [(t, c, i) for t in (args.tasks or task_ids()) for c in args.conditions for i in range(args.n)]
    random.Random(0).shuffle(jobs)  # randomize execution order across conditions and tasks
    jobs = [j for j in jobs if f"{args.tag}|{args.model}|{j[0]}|{j[1]}|{j[2]}" not in done]
    spend = [sum(r.get("cost") or 0 for r in read_jsonl(RAW))]
    print(f"{len(jobs)} runs to do; spend so far ${spend[0]:.3f}")

    def work(job):
        tid, cond, i = job
        if spend[0] > args.budget:
            return
        msgs = build_messages(load_task(tid), cond)
        # Occasionally the provider returns the final answer inside the reasoning field and leaves the
        # answer empty; that would leak the answer to the monitor, so such responses are re-requested.
        attempts = []
        for _ in range(3):
            try:
                reasoning, content, raw = chat(args.model, msgs,
                                               provider={"order": [args.provider], "allow_fallbacks": False})
            except Exception as e:
                print(f"FAILED {job}: {e}")
                return
            attempts.append(raw.get("usage", {}).get("cost") or 0)
            if content.strip():
                break
        else:
            print(f"EMPTY ANSWER x3 {job}; skipped")
            return
        usage = raw.get("usage", {})
        row = {
            "run_id": f"{args.tag}|{args.model}|{tid}|{cond}|{i}",
            "tag": args.tag, "model": args.model, "served_model": raw.get("model"),
            "provider": raw.get("provider"), "task_id": tid, "condition": cond, "sample": i,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "messages": msgs, "reasoning": reasoning, "content": content,
            "finish_reason": raw["choices"][0].get("finish_reason"),
            "usage": usage, "cost": sum(attempts), "attempts": len(attempts),
        }
        with lock:
            append_jsonl(RAW, row)
            spend[0] += row["cost"] or 0
            print(f"{tid:24s} {cond} #{i}  reasoning={len(reasoning):6d}ch  spend=${spend[0]:.3f}")

    with ThreadPoolExecutor(args.workers) as ex:
        list(ex.map(work, jobs))
    if spend[0] > args.budget:
        print(f"STOPPED: spend ${spend[0]:.2f} exceeded budget ${args.budget}")


if __name__ == "__main__":
    main()
