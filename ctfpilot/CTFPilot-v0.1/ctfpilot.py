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
    # CTFPILOT_DB env override: selftest runs on an isolated DB so smoke runs
    # never pollute production memory (lessons/stats/recall).
    db = os.environ.get("CTFPILOT_DB") or path_under_project(cfg["memory"]["db_path"])
    return TrajStore(db)


def _derived_ok(cfg, chall_dir, flag):
    """Provenance gate: a flag that appears verbatim in the original files is
    a raw echo, not a derived solve. Returns (ok, rel_path_or_None)."""
    if not flag:
        return True, None
    if not bool(cfg.get("flagcheck", {}).get("require_derived", True)):
        return True, None
    from core.flagcheck import flag_in_raw
    hit, src = flag_in_raw(chall_dir, flag)
    return (not hit), src


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
    provenance = {"require_derived": bool(cfg.get("flagcheck", {}).get("require_derived", True)),
                  "status": "none", "raw_file": None}

    from fastpath.detectors import Attempt, run_fast_path

    def _reject_raw_echo(f):
        ok, src = _derived_ok(cfg, chall_dir, f)
        if ok:
            return False
        log("[flagcheck] raw-echo: flag appears verbatim in original file %s;"
            " not counting as a derived solve" % src)
        store.log_attempt(cid, "verify", "provenance", "raw-echo in %s" % src,
                          False, f, error_class="raw-echo")
        attempts.append(Attempt("provenance", "raw-echo in %s" % src, False))
        provenance.update(status="raw-echo", raw_file=src)
        # retract the echoing attempt so the auto-lesson/trace do not credit it
        for a in attempts:
            if a.flag == f:
                a.success = False
                a.flag = None
        return True

    if args.mode in ("fast", "auto"):
        log("[fast] running offline detectors...")
        flag = run_fast_path(chall_dir, routing["files"], checker, attempts)
        for a in attempts:
            store.log_attempt(cid, "fast", a.detector, a.detail, a.success, a.flag)
        if flag and _reject_raw_echo(flag):
            flag = None

    if flag is None and args.mode in ("deep", "auto"):
        log("[deep] entering hypothesis loop...")
        from deep.loop import run_deep_loop
        flag, deep_attempts = run_deep_loop(name, chall_dir, routing,
                                            routing["files"], checker,
                                            sandbox, llm, store, cid, cfg, log,
                                            brain=args.brain)
        attempts.extend(deep_attempts)
        if flag and _reject_raw_echo(flag):
            flag = None

    dt = time.time() - t0
    if flag:
        provenance["status"] = "derived"
        log("[result] SOLVED in %.1fs" % dt)
        log("[result] FLAG: %s" % flag)
        store.finish_challenge(cid, True, flag)
        # auto-lesson: what worked here (kept for counting via fixations;
        # excluded from recall because snippet stays empty)
        winner = next((a for a in attempts if a.success and a.flag), None)
        if winner:
            store.add_lesson(cid, routing["category"], winner.detector,
                             "worked on '%s' (%s)" % (name, winner.detail[:120]),
                             "")
            store.bump_success(routing["category"], winner.detector)
    else:
        log("[result] NOT SOLVED in %.1fs (%d attempts)" % (dt, len(attempts)))
        store.finish_challenge(cid, False, "")
    # trace on BOTH success and failure: failed traces are the raw material
    # for retrospection (失败路径/踩坑归因), previously they were never written.
    if args.write_trace:
        trace_path = os.path.join(chall_dir, "_ctfpilot_trace.json")
        _dump_trace(trace_path, name, routing, attempts, flag, provenance)
    return 0 if flag else 1


def _dump_trace(path, name, routing, attempts, flag, provenance=None):
    trace = {
        "challenge": name,
        "category": routing["category"],
        "flag": flag,
        "provenance": provenance or {},
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
    # lesson carries the structured fields as evidence, so recall is actually
    # useful (signals / pitfalls / transferable pattern / failure paths).
    f_ = parsed.get("fields", {})
    tags = parsed.get("tags", {})
    evidence = " | ".join(filter(None, [
        ("信号: " + f_["识别信号"]) if f_.get("识别信号") else "",
        ("坑: " + tags["陷阱类型"]) if tags.get("陷阱类型") else "",
        ("迁移: " + tags["可迁移模式"]) if tags.get("可迁移模式") else "",
        ("失败: " + f_["失败原因"]) if f_.get("失败原因") else "",
        ("限制: " + f_["适用限制"]) if f_.get("适用限制") else "",
        ("链: " + f_["核心技术链"])[:200] if f_.get("核心技术链") else "",
    ]))
    if not evidence:
        # writeups without template fields still carry their step outline so
        # recall keeps them (otherwise the empty-snippet filter would drop them)
        evidence = "from writeup '%s'; steps: %s" % (
            name, "; ".join(parsed["steps"][:3])[:200])
    tags_json = json.dumps(tags, ensure_ascii=False) if tags else ""
    for cat, tech in parsed["techniques"]:
        wtu = parsed.get("signals") or "challenge looks like %s; recall from writeup '%s'" % (cat, name)
        store.add_lesson(cid, cat, tech, wtu, evidence, tags=tags_json)

    log("[learn] category=%s" % parsed["category"])
    log("[learn] techniques: %s" % (", ".join(t for _c, t in parsed["techniques"]) or "(none)"))
    log("[learn] checkpoints stored: %d" % len(steps[:15]))
    log("[learn] tags: %s" % (", ".join("%s=%s" % kv for kv in tags.items()) or "(none)"))
    log("[learn] skill cards: %s" % ", ".join(os.path.basename(c) for c in cards))
    return 0


def do_recall(args, cfg):
    store = get_stores(cfg)
    cat = args.category or "misc"
    qtokens = None
    if getattr(args, "query", None):
        try:
            from knowledge import tokenize
            qtokens = tokenize(args.query)
        except ImportError:
            pass
    lessons = store.recall_lessons(cat, 20, query_tokens=qtokens)
    print("== lessons for %s%s ==" %
          (cat, (" (query: %s)" % args.query) if getattr(args, "query", None) else ""))
    if not lessons:
        print("  (no writeup-quality lessons yet; bare auto solve records are "
              "excluded from recall -- count them with `fixations`)")
    for l in lessons:
        extra = ""
        if l.get("snippet"):
            extra += " | evidence: %s" % l["snippet"][:120]
        if l.get("tags"):
            extra += " | tags: %s" % str(l["tags"])[:120]
        print("- %s | %s%s" % (l["technique"], l["when_to_use"], extra))
    rows = store.conn.execute(
        "SELECT id,name,category,solved FROM challenges ORDER BY id DESC LIMIT 20").fetchall()
    print("== recent challenges ==")
    for r in rows:
        print("- [%s] %-4s %-30s solved=%s" % (r[0], r[2], r[1], bool(r[3])))
    return 0


def do_rebuild_knowledge(args, cfg):
    """SOTA ingestion: writeups -> case cards (cases.jsonl) + lesson upsert.
    Idempotent: stable case ids mean re-runs refresh instead of duplicating."""
    from knowledge import build_cases, save_cases, rebuild_lessons
    src = os.path.abspath(args.dir)
    if not os.path.isdir(src):
        log("[error] not a directory: %s" % src)
        return 2
    cases = build_cases(src)
    cats = {}
    for c in cases:
        cats[c["category"]] = cats.get(c["category"], 0) + 1
    rich = sum(1 for c in cases if c.get("signals"))
    log("[knowledge] cases=%d solved=%d with-signals=%d (%.0f%%)" %
        (len(cases), sum(1 for c in cases if c.get("solved")), rich,
         100.0 * rich / max(1, len(cases))))
    log("[knowledge] categories: %s" %
        ", ".join("%s=%d" % kv for kv in sorted(cats.items())))
    if args.dry_run:
        log("[knowledge] dry run, nothing written")
        return 0
    out = path_under_project(os.path.join("skills", "knowledge"))
    paths = save_cases(cases, out)
    log("[knowledge] wrote %s" % ", ".join(os.path.basename(p) for p in paths))
    store = get_stores(cfg)
    n = rebuild_lessons(store, cases)
    log("[knowledge] lessons upserted: %d (recall now signal-scored)" % n)
    return 0


def do_fixations(args, cfg):
    """Data-driven trigger for the 固化环: which techniques have been recalled
    or solved-with often enough to deserve compilation into a fast detector."""
    store = get_stores(cfg)
    rows = store.fixation_candidates(args.min)
    log("[fixations] techniques with hits+successes >= %d" % args.min)
    if not rows:
        log("  (none yet)")
    for r in rows:
        log("- [%s] %s hits=%d solved=%d rows=%d" %
            (r["category"], r["technique"], r["hits"], r["successes"], r["rows"]))
    return 0


def do_stats(args, cfg):
    store = get_stores(cfg)
    log("[stats] %s" % json.dumps(store.stats(), ensure_ascii=False))
    return 0


def do_selftest(args, cfg):
    """Run the bundled smoke-test challenges end to end.

    Memory isolation: selftest runs on .temp/selftest.sqlite so smoke runs
    never write lessons/challenges into the production trajectory store
    (that pollution previously drowned out real writeup lessons in recall)."""
    tmp_db = os.path.join(PROJECT_ROOT, ".temp", "selftest.sqlite")
    os.makedirs(os.path.dirname(tmp_db), exist_ok=True)
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    os.environ["CTFPILOT_DB"] = tmp_db
    log("[selftest] isolated memory db: %s" % tmp_db)
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
    p.add_argument("--query", help="signal keywords, e.g. 'NTRU lattice LLL'")

    p = sub.add_parser("rebuild-knowledge")
    p.add_argument("dir", help="writeups directory (repo ctfpilot/writeups)")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("fixations")
    p.add_argument("--min", type=int, default=3)

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
    elif args.cmd == "rebuild-knowledge":
        sys.exit(do_rebuild_knowledge(args, cfg))
    elif args.cmd == "fixations":
        sys.exit(do_fixations(args, cfg))
    elif args.cmd == "stats":
        sys.exit(do_stats(args, cfg))
    elif args.cmd == "selftest":
        sys.exit(do_selftest(args, cfg))


if __name__ == "__main__":
    main()
