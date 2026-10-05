"""Write a sheet of monitor judgments for hand-checking (default: 20 'verbalized' labels).

The sample is stratified: half hacked runs, half non-hacked runs, split evenly across conditions,
drawn with a fixed seed. Fill in the AGREE column in results/handcheck_<label>.md by hand.

    python handcheck.py --tag main --label verbalized --n 20
"""
import argparse
import random

from common import RESULTS, read_jsonl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--label", default="verbalized")
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()
    raw = {r["run_id"]: r for r in read_jsonl(RESULTS / "raw.jsonl") if r["tag"] == args.tag}
    graded = {g["run_id"]: g for g in read_jsonl(RESULTS / "graded.jsonl") if g["run_id"] in raw}
    mon = {m["run_id"]: m for m in read_jsonl(RESULTS / "monitor.jsonl")
           if m.get("label") == args.label and m["run_id"] in graded}
    rng = random.Random(0)
    sample = []
    for cond in "PM":
        for hacked in (True, False):
            pool = sorted(r for r in mon if graded[r]["condition"] == cond and graded[r]["hacked"] == hacked)
            sample += rng.sample(pool, min(len(pool), args.n // 4))
    field = "content" if args.label == "disclosed" else "reasoning"
    out = [f"# Hand-check: monitor label `{args.label}` ({len(sample)} runs, tag `{args.tag}`)\n",
           "For each run, read the text and write Y in AGREE if you agree with the monitor, N if not.\n",
           "| # | run | hacked (script) | monitor says | AGREE |", "|---|---|---|---|---|"]
    for i, rid in enumerate(sample, 1):
        out.append(f"| {i} | `{rid}` | {graded[rid]['hacked']} | {'YES' if mon[rid]['value'] else 'NO'} | |")
    for i, rid in enumerate(sample, 1):
        out += [f"\n---\n\n## {i}. `{rid}`\n",
                f"**Script:** hacked={graded[rid]['hacked']}, special_cased={graded[rid]['special_cased']}, "
                f"test_edited={graded[rid]['test_edited']}\n",
                f"**Monitor ({args.label}):** {mon[rid]['monitor_output'].strip()}\n",
                f"<details><summary>{field} ({len(raw[rid][field])} characters)</summary>\n\n```text\n"
                f"{raw[rid][field]}\n```\n</details>"]
    path = RESULTS / f"handcheck_{args.label}_{args.tag}.md"
    path.write_text("\n".join(out) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
