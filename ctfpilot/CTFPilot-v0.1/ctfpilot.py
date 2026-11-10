"""CTFPilot CLI.

Usage (run with the project python, UTF-8 mode):
  python -X utf8 ctfpilot.py solve <challenge_dir> [--mode auto|fast|deep] [--brain auto|llm|mock]
  python -X utf8 ctfpilot.py learn-writeup <writeup.md> --name <name>
  python -X utf8 ctfpilot.py recall --category crypto
  python -X utf8 ctfpilot.py stats
  python -X utf8 ctfpilot.py selftest
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import load_config, compiled_flag_patterns, path_under_project, PROJECT_ROOT
from core.flagcheck import FlagChecker
from core.router import route
from core.sandbox import Sandbox
from core.llm import LLMRouter
from memory.trajstore import TrajStore
from learn import writeup_learner


def log(msg):
    print(msg, flush=True)


def get_stores(cfg):
    db = path_under_project(cfg["memory"]["db_path"])
    return TrajStore(db)


def do_solve(args, cfg):
    chall_dir = os.path.abspath(args.dir)
    if not os.path.isdir(chall_dir):
        log("[error] not a directory: %s" % chall_dir)
        return 2
    checker = FlagChecker(compiled_flag_patterns(cfg), extra_regex=args.flag_regex)
    sandbox = Sandbox(cfg, chall_dir)
    llm = LLMRouter(cfg, log=log)
    store = get_stores(cfg)

    t0 = time.time()
    log("[triage] scanning %s" % chall_dir)
    routing = route(chall_dir)
    log("[triage] category=%s confidence=%.2f" % (routing["category"], routing["confidence"]))
    for ev in routing["evidence"][:6]:
        log("   " + ev)

    name = args.name or os.path.basename(chall_dir.rstrip("\\/"))
    cid = store.new_challenge(name, routing["category"], chall_dir,
                              args.mode, routing["summary"][:500])

    flag = None
    attempts = []

    from fastpath.detectors import Attempt, run_fast_path

    if args.mode in ("fast", "auto"):
        log("[fast] running offline detectors...")
        flag = run_fast_path(chall_dir, routing["files"], checker, attempts)
        for a in attempts:
            store.log_attempt(cid, "fast", a.detector, a.detail, a.success, a.flag)

    if flag is None and args.mode in ("deep", "auto"):
        log("[deep] entering hypothesis loop...")
        from deep.loop import run_deep_loop
        flag, deep_attempts = run_deep_loop(name, chall_dir, routing,
                                            routing["files"], checker,
                                            sandbox, llm, store, cid, cfg, log,
                                            brain=args.brain)
        attempts.extend(deep_attempts)

    dt = time.time() - t0
    if flag:
        log("[result] SOLVED in %.1fs" % dt)
        log("[result] FLAG: %s" % flag)
        store.finish_challenge(cid, True, flag)
        # auto-lesson: what worked here
        winner = next((a for a in attempts if a.success and a.flag), None)
        if winner:
            store.add_lesson(cid, routing["category"], winner.detector,
                             "worked on '%s' (%s)" % (name, winner.detail[:120]),
                             "")
        if args.write_trace:
            trace_path = os.path.join(chall_dir, "_ctfpilot_trace.json")
            _dump_trace(trace_path, name, routing, attempts, flag)
    else:
        log("[result] NOT SOLVED in %.1fs (%d attempts)" % (dt, len(attempts)))
        store.finish_challenge(cid, False, "")
    return 0 if flag else 1


def _dump_trace(path, name, routing, attempts, flag):
    trace = {
        "challenge": name,
        "category": routing["category"],
        "flag": flag,
        "attempts": [vars(a) for a in attempts],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trace, f, ensure_ascii=False, indent=2)
    log("[trace] wrote %s" % path)


def do_learn_writeup(args, cfg):
    text_path = os.path.abspath(args.file)
    if not os.path.exists(text_path):
        log("[error] no such file: %s" % text_path)
        return 2
    with open(text_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    parsed = writeup_learner.parse_writeup(text)
    name = args.name or os.path.splitext(os.path.basename(text_path))[0]
    out_dir = path_under_project(os.path.join("skills", "learned"))
    cards = writeup_learner.save_skill_card(name, parsed, out_dir)

    store = get_stores(cfg)
    cid = store.new_challenge("writeup:" + name, parsed["category"],
                              text_path, "learn", text[:500])
    steps = parsed["steps"] or [t for _c, t in parsed["techniques"]]
    if steps:
        store.add_checkpoints(cid, "writeup:" + name, steps[:15])
    for cat, tech in parsed["techniques"]:
        store.add_lesson(cid, cat, tech, "from writeup '%s'" % name, "")

    log("[learn] category=%s" % parsed["category"])
    log("[learn] techniques: %s" % (", ".join(t for _c, t in parsed["techniques"]) or "(none)"))
    log("[learn] checkpoints stored: %d" % len(steps[:15]))
    log("[learn] skill cards: %s" % ", ".join(os.path.basename(c) for c in cards))
    return 0


def do_recall(args, cfg):
    store = get_stores(cfg)
    lessons = store.recall_lessons(args.category or "misc", 20)
    print("== lessons for %s ==" % (args.category or "misc"))
    for l in lessons:
        print("- %s | %s" % (l["technique"], l["when_to_use"]))
    rows = store.conn.execute(
        "SELECT id,name,category,solved FROM challenges ORDER BY id DESC LIMIT 20").fetchall()
    print("== recent challenges ==")
    for r in rows:
        print("- [%s] %-4s %-30s solved=%s" % (r[0], r[2], r[1], bool(r[3])))
    return 0


def do_stats(args, cfg):
    store = get_stores(cfg)
    log("[stats] %s" % json.dumps(store.stats(), ensure_ascii=False))
    return 0


def do_selftest(args, cfg):
    """Run the bundled smoke-test challenges end to end."""
    base = os.path.join(PROJECT_ROOT, "tests", "challs")
    results = []
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            continue
        log("")
        log("==== selftest: %s ====" % name)
        rc = do_solve(_NS(dir=d, mode="auto", brain="mock", name=name,
                          flag_regex=None, write_trace=False), cfg)
        results.append((name, rc == 0))
    log("")
    log("==== selftest summary ====")
    ok = sum(1 for _n, s in results if s)
    for n, s in results:
        log("  %-24s %s" % (n, "PASS" if s else "FAIL"))
    log("passed %d/%d" % (ok, len(results)))
    return 0 if ok == len(results) and results else 1


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def main():
    ap = argparse.ArgumentParser(prog="ctfpilot")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("solve")
    p.add_argument("dir")
    p.add_argument("--mode", choices=["auto", "fast", "deep"], default="auto")
    p.add_argument("--brain", choices=["auto", "llm", "mock"], default="auto")
    p.add_argument("--name")
    p.add_argument("--flag-regex")
    p.add_argument("--write-trace", action="store_true")

    p = sub.add_parser("learn-writeup")
    p.add_argument("file")
    p.add_argument("--name")

    p = sub.add_parser("recall")
    p.add_argument("--category")

    sub.add_parser("stats")
    sub.add_parser("selftest")

    args = ap.parse_args()
    cfg = load_config()
    if args.cmd == "solve":
        sys.exit(do_solve(args, cfg))
    elif args.cmd == "learn-writeup":
        sys.exit(do_learn_writeup(args, cfg))
    elif args.cmd == "recall":
        sys.exit(do_recall(args, cfg))
    elif args.cmd == "stats":
        sys.exit(do_stats(args, cfg))
    elif args.cmd == "selftest":
        sys.exit(do_selftest(args, cfg))


if __name__ == "__main__":
    main()
