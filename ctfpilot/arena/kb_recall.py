# -*- coding: utf-8 -*-
"""kb_recall —— 记忆查询工具（stdio，等价 MCP 工具）。

用法：
  python kb_recall.py --category web --query "ejs render 原型污染" --top 3
输出：JSON 数组，每项含 name / category / signals / chain / pitfalls / transferable。
只读 cases.jsonl，不写任何库、不动 hit_count（实验用途，避免污染生产计数）。
"""
import argparse, json, os, sys

ENGINE = os.path.join(os.environ["USERPROFILE"],
                      r".local\share\TeleAgent\TeleAgent的工作空间\CTFPilot-v0.1")
sys.path.insert(0, ENGINE)

from knowledge import load_cases, tokenize, rank_cases  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="misc",
                    choices=["misc", "web", "pwn", "crypto", "reverse", "forensics"])
    ap.add_argument("--query", required=True)
    ap.add_argument("--top", type=int, default=3)
    args = ap.parse_args()

    cases = load_cases(os.path.join(ENGINE, "skills"))
    if not cases:
        print("[]")
        return 1
    q = tokenize(args.query)
    top = rank_cases(cases, args.category, q, args.top)
    out = [{
        "name": c.get("name"),
        "category": c.get("category"),
        "solved": c.get("solved"),
        "signals": c.get("signals", "")[:400],
        "chain": c.get("chain", "")[:600],
        "pitfalls": c.get("pitfalls", "")[:300],
        "transferable": c.get("transferable", "")[:300],
    } for c in top]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())