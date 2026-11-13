"""Ablation runner：池内每题 × 每变体跑一次 solve，逐行写 JSONL 并汇总。

用法：
  python -X utf8 run_ablation.py --pool <池> --variants baseline-fast,baseline-auto
  python -X utf8 run_ablation.py --pool <池> --variants all --out results.jsonl

纪律：
  * 结果逐行 flush + fsync，断电最多丢最后一行；
  * 同一变体内题目按题名排序，保证可复现；
  * 每道题只跑一次即写入隔离台账，已被评测过的题默认拒绝再跑；
  * 默认每个 (题, 变体) 使用冻结知识库的独立副本，题目之间互不污染。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from harness import (Quarantine, challenge_digest, resolve_variants,  # noqa: E402
                     run_variant, summarize)


def _now():
    return time.strftime("%Y%m%d-%H%M%S")


def _list_challenges(pool):
    out = []
    for name in sorted(os.listdir(pool)):
        d = os.path.join(pool, name)
        if os.path.isdir(d) and not name.startswith("."):
            out.append((name, d))
    return out


def _copy_sqlite(src, dst):
    for suffix in ("", "-wal", "-shm"):
        s, d = src + suffix, dst + suffix
        if os.path.exists(s):
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
        elif os.path.exists(d):
            os.remove(d)


def _read_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _append_jsonl(path, row):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _print_summary(rows, timeout_s):
    stats = summarize(rows, timeout_s)
    print("")
    print("对抗时钟 adversarial_time = mean(time|solved) * solved_rate "
          "+ %s * (1 - solved_rate)" % timeout_s)
    print("%-16s %3s %7s %6s %12s %11s %8s %6s %9s" % (
        "variant", "n", "solved", "rate", "mean_solved", "adv_clock",
        "timeout", "unsup", "degraded"))
    print("-" * 88)
    for s in stats:
        print("%-16s %3d %7d %5.0f%% %11.2fs %10.2fs %8d %6d %9d" % (
            s["variant"], s["n"], s["solved"], s["solved_rate"] * 100,
            s["mean_solved_time"], s["adversarial_time"], s["timeouts"],
            s["unsupported"], s["degraded"]))
    print("")
    for s in stats:
        if s["solved_rate"] < 1.0:
            print("  %-16s 未解出 %d 题按 %ss/题计罚；平均已解出耗时仅统计 solved 行"
                  % (s["variant"], s["n"] - s["solved"], timeout_s))
    reasons = {}
    for r in rows:
        if r.get("variant_unsupported"):
            key = (r.get("variant"), r.get("unsupported_reason") or "unknown")
            reasons[key] = reasons.get(key, 0) + 1
    if reasons:
        print("")
        print("变体开关未生效（数字仅代表降级/基线的求解能力，不代表该开关的贡献）：")
        for (v, why), n in sorted(reasons.items()):
            print("  %-16s %-22s %d 题" % (v, why, n))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="run_ablation",
        description="CTFPilot 消融基准运行器（对抗时钟口径）")
    ap.add_argument("--pool", default=None,
                    help="题目池目录：每题一个子目录，子目录名=题名")
    ap.add_argument("--variants", default="all",
                    help="逗号分隔的变体名，或 all（默认）")
    ap.add_argument("--out", default=None,
                    help="结果 JSONL 路径（默认 arena/results/ablation-<时间>.jsonl）")
    ap.add_argument("--per-variant-timeout", type=int, default=900,
                    help="单题单变体超时秒数，也是未解出的计罚上限（默认 900）")
    ap.add_argument("--engine-root", default=os.path.normpath(
        os.path.join(HERE, "..", "CTFPilot-v0.1")), help="引擎目录（含 ctfpilot.py）")
    ap.add_argument("--python", default=sys.executable, help="解释器路径")
    ap.add_argument("--db", default=None,
                    help="冻结知识库快照路径（默认 arena/state/bench.sqlite）")
    ap.add_argument("--use-production-memory", action="store_true",
                    help="不注入 CTFPILOT_DB：直接读写生产记忆库（会污染，仅本地研究用）")
    ap.add_argument("--quarantine", default=os.path.join(HERE, "state", "quarantine.jsonl"),
                    help="隔离台账路径")
    ap.add_argument("--no-quarantine", action="store_true", help="关闭隔离检查（不推荐）")
    ap.add_argument("--allow-quarantined", action="store_true",
                    help="允许重跑已评测过的题（只用于排障，结果不得用于调参）")
    ap.add_argument("--keep-scratch", action="store_true", help="保留本次运行的临时记忆库")
    ap.add_argument("--list-variants", action="store_true", help="列出变体后退出")
    args = ap.parse_args(argv)

    if args.list_variants:
        for v in resolve_variants("all"):
            print("%-16s %s" % (v.name, v.desc))
            print("%-16s args=%s env=%s" % ("", " ".join(v.solve_args) or "(none)",
                                            v.env_overrides or "{}"))
        return 0

    try:
        variants = resolve_variants(args.variants)
    except ValueError as exc:
        print("[ablation] %s" % exc)
        return 2

    if not args.pool:
        ap.error("--pool is required")
        return 2
    pool = os.path.abspath(args.pool)
    engine_root = os.path.abspath(args.engine_root)
    if not os.path.isdir(pool):
        print("[ablation] pool not found: %s" % pool)
        return 2
    if not os.path.isfile(os.path.join(engine_root, "ctfpilot.py")):
        print("[ablation] engine not found: %s" % os.path.join(engine_root, "ctfpilot.py"))
        return 2
    challenges = _list_challenges(pool)
    if not challenges:
        print("[ablation] no challenge sub-directory in %s" % pool)
        return 2

    out = os.path.abspath(args.out) if args.out else os.path.join(
        HERE, "results", "ablation-%s.jsonl" % _now())
    os.makedirs(os.path.dirname(out), exist_ok=True)

    q = None if args.no_quarantine else Quarantine(args.quarantine)
    digests = {name: challenge_digest(d) for name, d in challenges} if q else {}
    if q is not None:
        blocked = [name for name, _d in challenges if q.has(digests[name])]
        if blocked and args.allow_quarantined:
            print("[ablation] WARNING: 重跑 %d 题已评测题（%s），结果仅用于排障"
                  % (len(blocked), args.quarantine))
        if blocked and not args.allow_quarantined:
            print("[ablation] REFUSED: %d 题已在隔离台账中（%s）" % (
                len(blocked), args.quarantine))
            for name in blocked:
                print("  - %s" % name)
            print("[ablation] 评测过的题不得再进训练/调参；确需重跑请加 --allow-quarantined")
            return 2

    # 冻结知识库快照：基准期内所有 baseline 运行共享同一份记忆，跑后丢弃写入。
    prod_db = os.path.join(engine_root, "memory", "trajectories.sqlite")
    snapshot = None
    scratch = None
    if not args.use_production_memory:
        snapshot = os.path.abspath(args.db or os.path.join(HERE, "state", "bench.sqlite"))
        if not args.db and os.path.exists(prod_db):
            _copy_sqlite(prod_db, snapshot)
        elif args.db and not os.path.exists(snapshot) and os.path.exists(prod_db):
            _copy_sqlite(prod_db, snapshot)
        if not os.path.exists(snapshot):
            snapshot = None
        state_dir = os.path.join(HERE, "state")
        os.makedirs(state_dir, exist_ok=True)
        scratch = tempfile.mkdtemp(prefix="run-", dir=state_dir)
        print("[ablation] 冻结知识库: %s" % (snapshot or "(empty; production db absent)"))
    else:
        print("[ablation] WARNING: 直连生产记忆库，评测会写入 lessons/stats")

    print("[ablation] pool=%s challenges=%d variants=%s timeout=%ss"
          % (pool, len(challenges), ",".join(v.name for v in variants),
             args.per_variant_timeout))
    print("[ablation] out=%s" % out)

    done = 0
    total = len(challenges) * len(variants)
    marked = set()
    try:
        for variant in variants:
            for name, cdir in challenges:
                if q is not None and name not in marked:
                    # 先记账再跑：即使进程崩溃，这题也不会流入训练输入
                    q.add(name, digests[name], pool)
                    marked.add(name)
                run_variant_obj = variant
                if args.use_production_memory:
                    mem_mode = "production"
                elif "CTFPILOT_DB" in variant.env_overrides:
                    mem_mode = "variant"
                else:
                    mem_mode = "snapshot"
                    db = os.path.join(scratch, "%s__%s.sqlite" % (variant.name, name))
                    if snapshot:
                        _copy_sqlite(snapshot, db)
                    run_variant_obj = variant.with_env({"CTFPILOT_DB": db})
                t0 = time.time()
                row = run_variant(run_variant_obj, cdir, engine_root,
                                  args.python, timeout_s=args.per_variant_timeout)
                row["pool"] = pool
                row["memory_mode"] = mem_mode
                row["run_seconds"] = round(time.time() - t0, 3)
                _append_jsonl(out, row)
                done += 1
                print("[%d/%d] %-16s %-28s solved=%-5s time=%-7s %s%s" % (
                    done, total, variant.name, name,
                    row["solved"],
                    ("%.2f" % row["time_seconds"]) if row["time_seconds"] is not None else "-",
                    row["status"],
                    (" unsupported(%s)" % row["unsupported_reason"])
                    if row["variant_unsupported"] else ""))
    finally:
        if scratch and not args.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        elif scratch:
            print("[ablation] scratch kept: %s" % scratch)

    rows = _read_jsonl(out)  # 从磁盘读回，确保汇总只依赖已落盘数据
    _print_summary(rows, args.per_variant_timeout)
    print("")
    print("[ablation] %d 行已落盘: %s" % (len(rows), out))
    bad = sum(1 for r in rows if r.get("status") in ("error", "crash", "unparsed"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())