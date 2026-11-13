"""CTFPilot CLI.

Usage (run with the project python, UTF-8 mode):
  python -X utf8 ctfpilot.py solve <challenge_dir> [--mode auto|fast|deep] [--brain auto|llm|mock]
                                         [--benchmark] [--json]
  python -X utf8 ctfpilot.py bench-pull --pool <pool_dir> --dest <dest_dir> [--tag <tag>]
  python -X utf8 ctfpilot.py learn-writeup <writeup.md> --name <name>
  python -X utf8 ctfpilot.py recall --category crypto
  python -X utf8 ctfpilot.py stats
  python -X utf8 ctfpilot.py selftest
  python -X utf8 ctfpilot.py dashboard [--out file.html] [--json]
  python -X utf8 ctfpilot.py record-win --name N --category C --flag F
"""
import argparse
import json
import os
import random
import shutil
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
        # DB retraction: previously only the in-memory attempt list was fixed;
        # the stored row kept success=1, so the failure profile credited a
        # cheat echo. Taxonomy v2: retractions persist as semantic:raw-echo.
        retracted = store.retract_attempts_by_flag(cid, f)
        if retracted:
            log("[flagcheck] retracted %d crediting attempt(s) in db" % retracted)
        store.log_attempt(cid, "verify", "provenance", "raw-echo in %s" % src,
                          False, f, error_class="semantic:raw-echo")
        attempts.append(Attempt("provenance", "raw-echo in %s" % src, False))
        provenance.update(status="raw-echo", raw_file=src, retracted=retracted)
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
                                            brain=args.brain,
                                            benchmark=bool(getattr(args, "benchmark", False)))
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
    # Tag every attempt with its pipeline phase so downstream tooling
    # (DSH bridge, dashboard, replay) can render a per-step timeline.
    for a in attempts:
        if not getattr(a, "phase", ""):
            a.phase = "fast" if args.mode in ("fast", "auto") else ""
    # Machine-readable summary (bridge tools parse THIS, not the prose log).
    meta = {
        "category": routing["category"],
        "confidence": round(routing["confidence"], 2),
        "evidence": [str(x) for x in routing["evidence"][:8]],
        "steps": [
            {"phase": getattr(a, "phase", ""),
             "name": a.detector,
             "detail": (a.detail or "")[:240],
             "success": bool(a.success),
             "flag": a.flag or ""}
            for a in attempts
        ],
    }
    log("[meta] %s" % json.dumps(meta, ensure_ascii=False))
    rc = 0 if flag else 1
    # Benchmark/unattended output. The two flags are orthogonal; both print
    # AFTER every other log line ([meta] included) so a runner can tail the
    # stream. Return-code semantics stay untouched: 0=solved 1=not-solved
    # 2=bad args.
    if getattr(args, "benchmark", False):
        log("[benchmark] name=%s solved=%s flag=%s time=%.2f attempts=%d" %
            (name, "true" if flag else "false", flag if flag else "-", dt,
             len(attempts)))
    if getattr(args, "json", False):
        result_path = os.path.join(chall_dir, "_ctfpilot_result.json")
        payload = {
            "name": name,
            "category": routing["category"],
            "solved": bool(flag),
            "flag": flag or "",
            "time_seconds": round(dt, 3),
            "attempts": len(attempts),
            "mode": args.mode,
            "provenance": provenance,
        }
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log("[json] %s" % result_path)
    return rc


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


def do_bench_pull(args, cfg):
    """Benchmark source pull: randomly pick a pool challenge that is not yet
    under dest (dedup by subdirectory name), copy it over minus engine
    artifacts (__pycache__ / _ctfpilot* / .temp), and keep a cumulative
    ledger. Powers the unattended benchmark loop; return 3 when the pool is
    exhausted so a runner can distinguish it from a hard failure."""
    pool = os.path.abspath(args.pool)
    dest = os.path.abspath(args.dest)
    if not os.path.isdir(pool):
        log("[error] pool not a directory: %s" % pool)
        return 2
    os.makedirs(dest, exist_ok=True)
    pool_names = sorted(d for d in os.listdir(pool)
                        if os.path.isdir(os.path.join(pool, d)))
    used = set(d for d in os.listdir(dest)
               if os.path.isdir(os.path.join(dest, d)))
    fresh = [c for c in pool_names if c not in used]
    if not fresh:
        log("[bench-pull] pool exhausted")
        return 3
    pick = random.choice(fresh)
    target = os.path.join(dest, pick)
    shutil.copytree(os.path.join(pool, pick), target,
                    ignore=shutil.ignore_patterns("__pycache__", "_ctfpilot*",
                                                  ".temp"))
    remaining = sum(1 for c in pool_names
                    if not os.path.isdir(os.path.join(dest, c)))
    ledger_path = os.path.join(dest, "_bench_ledger.json")
    ledger = {"pulled": 0, "remaining": remaining, "history": [], "tag": ""}
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            ledger.update(saved)
    except (OSError, ValueError):
        pass
    ledger["pulled"] = int(ledger.get("pulled", 0)) + 1
    ledger["history"] = (list(ledger.get("history", [])) + [pick])[-200:]
    ledger["tag"] = args.tag or ""
    ledger["remaining"] = remaining
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)
    log("[bench-pull] chall=%s" % target)
    log("[bench-pull] ledger pulled=%d remaining=%d tag=%s" %
        (ledger["pulled"], ledger["remaining"], ledger["tag"] or "-"))
    return 0


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
    from knowledge import build_cases, save_cases, rebuild_lessons, merge_refined
    src = os.path.abspath(args.dir)
    if not os.path.isdir(src):
        log("[error] not a directory: %s" % src)
        return 2
    cases = build_cases(src)
    out = path_under_project(os.path.join("skills", "knowledge"))
    cases = merge_refined(cases, os.path.join(out, "cases.jsonl"))
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
    paths = save_cases(cases, out, extra_stats={"writeups_dir": src})
    log("[knowledge] wrote %s" % ", ".join(os.path.basename(p) for p in paths))
    store = get_stores(cfg)
    n = rebuild_lessons(store, cases)
    log("[knowledge] lessons upserted: %d (recall now signal-scored)" % n)
    return 0


def do_eval_retrieval(args, cfg):
    """Retrieval regression (固化留出评测): query = each writeup's 题面描述,
    target = its own case card; compare against the saved baseline and fail
    if top1/top3 regress beyond a 5pp tolerance. 先测试后上线."""
    from knowledge import load_cases, eval_retrieval
    skill_dir = path_under_project("skills")
    cases = load_cases(skill_dir)
    if not cases:
        log("[error] no cases.jsonl -- run rebuild-knowledge first")
        return 2
    stats_path = path_under_project(os.path.join("skills", "knowledge", "stats.json"))
    wdir = args.writeups
    if not wdir and os.path.exists(stats_path):
        with open(stats_path, "r", encoding="utf-8") as f:
            wdir = json.load(f).get("writeups_dir")
    if not wdir or not os.path.isdir(wdir):
        log("[error] writeups dir not found -- pass it or rerun rebuild-knowledge")
        return 2
    res = eval_retrieval(cases, wdir)
    log("[eval-retrieval] writeups=%s cases=%d evaluable=%d" %
        (wdir, res["total"], res["evaluable"]))
    for k in ("signal", "recency"):
        m = res[k]
        log("  %-8s top1=%.1f%% top3=%.1f%% mrr=%.3f" %
            (k, m["top1"] * 100, m["top3"] * 100, m["mrr"]))
    base_path = path_under_project(os.path.join("skills", "knowledge", "eval-baseline.json"))
    ok = True
    if os.path.exists(base_path):
        with open(base_path, "r", encoding="utf-8") as f:
            b = json.load(f)
        s = res["signal"]
        if b.get("evaluable") and abs(res["evaluable"] - b["evaluable"]) > 3:
            log("  note     : evaluable set changed (%d vs %d) -- baseline comparison may shift" %
                (res["evaluable"], b["evaluable"]))
        ok = s["top1"] >= b["top1"] - 0.05 and s["top3"] >= b["top3"] - 0.05
        log("  baseline : top1=%.1f%% top3=%.1f%% mrr=%.3f (%s)" %
            (b["top1"] * 100, b["top3"] * 100, b["mrr"], b.get("date", "")))
        log("  drift    : top1%+.1fpp top3%+.1fpp mrr%+.3f" %
            ((s["top1"] - b["top1"]) * 100, (s["top3"] - b["top3"]) * 100,
             s["mrr"] - b["mrr"]))
        log("  RESULT   : %s" % ("PASS" if ok else "FAIL (retrieval regressed beyond 5pp tolerance)"))
    else:
        log("  (no baseline yet -- run with --save-baseline to set one)")
    if args.save_baseline:
        s = res["signal"]
        with open(base_path, "w", encoding="utf-8") as f:
            json.dump({"top1": s["top1"], "top3": s["top3"], "mrr": s["mrr"],
                      "evaluable": res["evaluable"],
                      "date": time.strftime("%Y-%m-%d %H:%M:%S")}, f,
                     ensure_ascii=False, indent=2)
        log("[eval-retrieval] baseline saved: %s" % base_path)
    return 0 if ok else 1


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


def build_dashboard_data(cfg):
    """Query the trajectory store and assemble the dashboard DATA blob."""
    import datetime as _dt
    import sqlite3
    db_path = os.environ.get("CTFPILOT_DB") or path_under_project(cfg["memory"]["db_path"])

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    stats_row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(solved) AS solved FROM challenges").fetchone()
    total = stats_row[0] or 0
    solved = stats_row[1] or 0

    bycat_rows = conn.execute("""
        SELECT category, COUNT(*) AS total, SUM(solved) AS solved
        FROM challenges GROUP BY category ORDER BY total DESC
    """).fetchall()

    chall_rows = conn.execute("""
        SELECT id, name, category, solved, flag, mode,
               datetime(created_at, 'unixepoch', 'localtime') AS ts_str
        FROM challenges ORDER BY id DESC LIMIT 200
    """).fetchall()

    lesson_rows = conn.execute("""
        SELECT category, technique, when_to_use, snippet,
               datetime(ts, 'unixepoch', 'localtime') AS ts_str
        FROM lessons ORDER BY id DESC LIMIT 50
    """).fetchall()

    det_rows = conn.execute("""
        SELECT phase, detector,
               COUNT(*) AS attempts, SUM(success) AS hits
        FROM attempts WHERE phase IN ('fast','deep-pre','exec','verify')
        GROUP BY phase, detector ORDER BY phase, hits DESC
    """).fetchall()

    checkpoint_count = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    lesson_count = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]

    mode_rows = conn.execute("""
        SELECT mode, COUNT(*) AS cnt FROM challenges
        GROUP BY mode ORDER BY cnt DESC
    """).fetchall()

    rate = round(solved * 100 / max(total, 1))

    bycat = [{"cat": r["category"], "total": r["total"],
              "solved": r["solved"] or 0} for r in bycat_rows]
    challs = [{"id": r["id"], "name": r["name"], "cat": r["category"],
               "mode": r["mode"] or "", "solved": bool(r["solved"]),
               "flag": r["flag"] or "", "time": r["ts_str"] or ""
              } for r in chall_rows]
    lessons = [{"cat": r["category"], "tech": r["technique"],
               "src": r["when_to_use"], "snippet": (r["snippet"] or "")[:300],
               "time": r["ts_str"] or ""
              } for r in lesson_rows]
    detectors = [{"phase": r["phase"], "detector": r["detector"],
                  "attempts": r["attempts"], "hits": r["hits"] or 0
                 } for r in det_rows]
    modes = [{"mode": r["mode"] or "unknown", "count": r["cnt"]} for r in mode_rows]

    data_blob = {
        "meta": {"generated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M")},
        "stats": {"total": total, "solved": solved, "rate": rate,
                  "checkpoints": checkpoint_count, "lessons": lesson_count,
                  "attempts": conn.execute(
                      "SELECT COUNT(*) FROM attempts").fetchone()[0],
                  "distinct": len(set(c["name"] for c in challs)),
                  "challs": len(challs)},
        "bycat": bycat, "modes": modes, "challs": challs,
        "lessons": lessons, "detectors": detectors,
    }
    conn.close()
    return data_blob


def do_dashboard(args, cfg):
    """Generate a self-contained interactive HTML dashboard from SQLite data."""
    data_blob = build_dashboard_data(cfg)
    if getattr(args, "as_json", False):
        sys.stdout.write(json.dumps(data_blob, ensure_ascii=False))
        return 0
    out_html = getattr(args, "out", None) or os.path.join(
        PROJECT_ROOT, "CTFPilot-解题看板.html")
    html = DASHBOARD_TEMPLATE.replace("__DATA_JSON__",
                                      json.dumps(data_blob, ensure_ascii=False))
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    log("[dashboard] wrote %s (%d bytes)" % (out_html, len(html)))
    return 0


def do_record_win(args, cfg):
    """Record a user-confirmed solve into the trajectory store.

    Called after the human confirmation gate has accepted a proposed flag:
    inserts the challenge already-solved so the ledger, stats and dashboard
    reflect the confirmed win exactly like an engine solve would.
    """
    name = args.name
    category = args.category
    flag = args.flag
    if not name or not flag:
        log("[error] record-win requires --name and --flag")
        return 2
    if category is None:
        log("[error] record-win requires --category")
        return 2
    store = get_stores(cfg)
    chall_dir = os.path.abspath(args.dir) if args.dir else name
    cid = store.new_challenge(name, category, chall_dir,
                              args.mode or "manual",
                              (args.summary or "confirmed via human gate")[:500])
    store.log_attempt(cid, "fast", "confirm-gate", "user-confirmed submission", True, flag)
    store.finish_challenge(cid, True, flag)
    store.add_lesson(cid, category, "confirm-gate",
                     "worked on '%s' (human-confirmed flag)" % name, "")
    store.bump_success(category, "confirm-gate")
    log("[result] RECORD-WIN: %s (%s) — flag %s" % (name, category, flag))
    return 0


DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CTFPilot 解题看板</title>
<style>
:root{--bg:#f4f8fc;--card:#fff;--line:#e2ecf7;--blue:#2b7de9;--blue2:#155fce;
--ink:#152238;--muted:#5d7089;--green:#12996b;--green-s:#e7f8f1;--amber:#b97800;
--amber-s:#fff6e5;--red:#d34a4a;--purple:#7a5cd6;--radius:14px;
--shadow:0 1px 2px rgba(16,42,80,.04),0 8px 24px rgba(16,42,80,.05)}
*{box-sizing:border-box}
body{margin:0;font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
background:radial-gradient(1000px 480px at 85% -10%,#e6f1ff 0%,transparent 60%),var(--bg);
color:var(--ink);font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased}
.mono{font-family:"Cascadia Code","Consolas",monospace}
.wrap{max-width:1280px;margin:0 auto;padding:20px 24px}
h1,h2,h3,h4{margin:0}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow)}
.btn{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);
background:#fff;color:var(--ink);border-radius:8px;padding:6px 10px;font-size:12px;
cursor:pointer;transition:.18s;font-family:inherit}
.btn:hover{border-color:#bcd6f7;background:#f8fbff}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:11px;color:#3f5a7d;
background:#f3f8ff;border:1px solid #dceafb;border-radius:999px;padding:3px 9px}
.hero{padding:22px 26px;display:flex;gap:24px;align-items:stretch;position:relative;overflow:hidden;
background:linear-gradient(112deg,rgba(255,255,255,.96) 0%,rgba(245,250,255,.96) 58%,rgba(233,242,255,.92) 100%)}
.hero:after{content:"";position:absolute;right:-90px;top:-110px;width:320px;height:320px;border-radius:50%;
background:radial-gradient(circle,rgba(43,125,233,.12),transparent 68%);pointer-events:none}
.hero-main{flex:1;min-width:0;position:relative;z-index:1}
.eyebrow{font-size:10.5px;letter-spacing:1.6px;font-weight:700;color:var(--blue2);text-transform:uppercase}
.hero h1{font-size:25px;letter-spacing:.3px;margin:6px 0 5px}
.hero p{margin:0;color:var(--muted);font-size:13px;max-width:640px}
.hero-side{display:grid;grid-template-columns:1fr;gap:6px;width:200px;flex:none;position:relative;z-index:1}
.hstat{border:1px solid #dfeafa;background:rgba(255,255,255,.72);border-radius:10px;padding:7px 11px;
display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.hstat span{font-size:11px;color:var(--muted)}
.hstat b{font-size:12.5px;color:var(--blue2)}
.kpi-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-top:14px}
.kpi{padding:12px 13px 11px;position:relative;overflow:hidden;transition:.18s}
.kpi:hover{transform:translateY(-2px);border-color:#cfe1f8}
.kpi-label{font-size:11.5px;color:var(--muted)}
.kpi-num{font-size:23px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
.kpi-sub{font-size:10.8px;color:#7c8da4;margin-top:1px}
.kpi-ic{position:absolute;right:9px;top:9px;width:24px;height:24px;border-radius:7px;display:grid;place-items:center}
.kpi-ic svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.kpi-ic.b{background:#e9f2ff;color:var(--blue2)}
.kpi-ic.g{background:var(--green-s);color:var(--green)}
.kpi-ic.a{background:var(--amber-s);color:var(--amber)}
.kpi-ic.p{background:#f0ecff;color:var(--purple)}
.tabs{display:flex;gap:3px;margin:20px 0 14px;border-bottom:1px solid var(--line);overflow-x:auto}
.tab{padding:9px 13px;font-size:12.5px;color:var(--muted);white-space:nowrap;border-bottom:2px solid transparent;cursor:pointer;transition:.15s}
.tab:hover{color:var(--blue2)}
.tab.active{color:var(--blue2);font-weight:600;border-bottom-color:var(--blue)}
.grid-2{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:14px}
.panel{padding:15px 16px 14px;min-width:0}
.panel-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:9px;gap:8px}
.panel-head h3{font-size:13px;display:flex;align-items:center;gap:7px}
.panel-head h3 svg{width:15px;height:15px;stroke:var(--blue);fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.note{font-size:10.8px;color:#7c8da4}
.donut-wrap{position:relative;width:150px;height:150px;margin:2px auto 4px}
.donut svg{width:100%;height:100%;transform:rotate(-90deg)}
.donut circle{fill:none;stroke-width:12}
.donut .track{stroke:#edf3fb}
.donut .ring{stroke:url(#ringGrad);stroke-linecap:round;transition:stroke-dashoffset 1s cubic-bezier(.22,.8,.3,1)}
.donut-center{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none}
.donut-center strong{font-size:24px;line-height:1.1}
.donut-center span{font-size:10.5px;color:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:10px 14px;justify-content:center;font-size:11.2px;color:var(--muted)}
.legend i{width:7px;height:7px;border-radius:2px;display:inline-block;margin-right:4px;vertical-align:1px}
.bars{display:flex;flex-direction:column;gap:10px;margin-top:3px}
.bar-row{display:grid;grid-template-columns:56px 1fr 70px;align-items:center;gap:9px}
.bar-name{font-size:11.6px;color:#3b506e}
.bar-track{height:9px;border-radius:999px;background:#f2f7fd;overflow:hidden;position:relative}
.bar-total,.bar-fill{position:absolute;left:0;top:0;height:100%;border-radius:999px}
.bar-total{background:#dcebfb}
.bar-fill{background:linear-gradient(90deg,#8fc0fa,#2b7de9);width:0;transition:width .9s cubic-bezier(.22,.8,.3,1)}
.bar-fill.green{background:linear-gradient(90deg,#7be0b8,#12996b)}
.bar-fill.amber{background:linear-gradient(90deg,#ffd489,#d39200)}
.bar-fill.purple{background:linear-gradient(90deg,#c3b4f2,#7a5cd6)}
.bar-fill.red{background:linear-gradient(90deg,#f4adad,#d34a4a)}
.bar-val{font-size:11px;color:var(--muted);text-align:right;font-variant-numeric:tabular-nums}
.stack{display:flex;height:12px;border-radius:999px;overflow:hidden;background:#eef4fb;margin:9px 0 10px}
.stack span{display:block;height:100%}
.stack-legend{display:flex;flex-wrap:wrap;gap:8px 12px;font-size:11px;color:var(--muted)}
.stack-legend i{width:7px;height:7px;border-radius:2px;display:inline-block;margin-right:4px}
.tbl-wrap{overflow:auto;max-height:480px;border:1px solid var(--line);border-radius:12px;background:#fff;box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse;font-size:12px;min-width:640px}
thead th{position:sticky;top:0;background:#f7fbff;color:#58708f;font-weight:600;text-align:left;
padding:8px 11px;border-bottom:1px solid var(--line);font-size:11px;z-index:2}
tbody td{padding:7px 11px;border-bottom:1px solid #edf3fa;vertical-align:middle;color:#33465f}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:#fafdff}
.name-cell{font-family:"Cascadia Code","Consolas",monospace;font-size:11.3px;color:#33465f;
max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block}
.right{text-align:right;font-variant-numeric:tabular-nums}
.muted{color:var(--muted);font-size:11px}
.pill{display:inline-flex;align-items:center;gap:4px;font-size:10.3px;padding:2px 7px;border-radius:999px;line-height:1.5;white-space:nowrap}
.pill.ok{background:var(--green-s);color:#0b7a53}
.pill.no{background:#f2f5f9;color:#7a8ba1}
.badge{font-size:10.2px;padding:2px 6px;border-radius:5px;line-height:1.4}
.badge.auto{background:#e9f2ff;color:var(--blue2)}
.badge.deep{background:#f0ecff;color:#5b3fc0}
.badge.fast{background:#e7f8f1;color:#0b7a53}
.badge.learn{background:var(--amber-s);color:var(--amber)}
.badge.gray{background:#f4f7fb;color:var(--muted)}
.cat{font-size:9.8px;padding:2px 6px;border-radius:5px;line-height:1.3}
.cat.misc{background:#e9f2ff;color:var(--blue2)}
.cat.reverse{background:var(--green-s);color:#0b7a53}
.cat.crypto{background:var(--amber-s);color:var(--amber)}
.cat.forensics{background:#f0ecff;color:#5b3fc0}
.cat.pwn{background:#fdeeee;color:#b33535}
.cat.web{background:#fff0f0;color:#c44}
.cat.osint{background:#f0fff0;color:#3a8a3a}
.toolbar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;padding:11px 13px;
border-bottom:1px solid var(--line);background:#fbfdff}
.search{display:flex;align-items:center;gap:7px;background:#fff;border:1px solid var(--line);
border-radius:8px;padding:5px 9px;min-width:200px;flex:1;max-width:320px}
.search input{border:none;outline:none;font-size:12px;font-family:inherit;width:100%;background:transparent}
.filters{display:flex;gap:5px;flex-wrap:wrap}
.fbtn{font-size:11px;padding:4px 8px;border-radius:7px;border:1px solid var(--line);
background:#fff;color:var(--muted);cursor:pointer;font-family:inherit}
.fbtn:hover{border-color:#c5dcf7;color:var(--blue2)}
.fbtn.on{background:#e9f2ff;border-color:#c9e0fb;color:var(--blue2);font-weight:600}
.lesson{display:grid;grid-template-columns:56px 1fr;gap:10px;padding:7px 3px;border-bottom:1px solid #edf3fa;font-size:11.8px}
.lesson:last-child{border-bottom:none}
.lesson .src{font-size:10.6px;color:#7c8da4;margin-top:1px}
.det-row{display:grid;grid-template-columns:60px 1fr 64px 56px;gap:8px;align-items:center;padding:6px 2px;border-bottom:1px solid #edf3fa;font-size:11.5px}
.det-row:last-child{border-bottom:none}
.det-row .nm{font-size:11px;color:#33465f}
.det-bar{height:5px;border-radius:99px;background:#eef4fb;overflow:hidden}
.det-fill{height:100%;border-radius:99px;background:linear-gradient(90deg,#9cc9f9,#2b7de9)}
.modal{position:fixed;inset:0;z-index:100;display:none;align-items:center;justify-content:center;padding:16px;background:rgba(16,35,60,.42)}
.modal.open{display:flex}
.modal-card{width:min(720px,100%);max-height:84vh;overflow:auto;background:rgba(255,255,255,.96);
backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,.85);border-radius:18px;
box-shadow:0 30px 80px rgba(10,30,60,.28);padding:18px 20px;animation:pop .2s ease}
@keyframes pop{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.modal-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.icon-btn{border:1px solid var(--line);background:#fff;border-radius:8px;width:28px;height:28px;
display:grid;place-items:center;cursor:pointer;color:var(--muted)}
.icon-btn:hover{color:var(--ink);border-color:#c5dcf7}
.flag-box{margin-top:10px;background:#f4f9ff;border:1px dashed #cfe2fb;border-radius:9px;
padding:8px 10px;font-family:"Cascadia Code","Consolas",monospace;font-size:11.5px;color:#1b4d87;word-break:break-all}
.toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,14px);background:#152238;
color:#fff;font-size:12px;padding:7px 13px;border-radius:8px;opacity:0;pointer-events:none;
transition:.22s;z-index:120;box-shadow:0 10px 30px rgba(10,30,60,.25)}
.toast.show{opacity:1;transform:translate(-50%,0)}
footer{border-top:1px solid var(--line);background:rgba(255,255,255,.6);padding:14px 0 20px;
color:#7c8da4;font-size:11px;margin-top:24px}
@media(max-width:960px){.kpi-grid{grid-template-columns:repeat(3,1fr)}.grid-2{grid-template-columns:1fr}.hero{flex-direction:column}.hero-side{width:100%}}
@media(max-width:600px){.kpi-grid{grid-template-columns:repeat(2,1fr)}.wrap{padding:12px}.hero h1{font-size:21px}}
@media print{.tabs,.toolbar,.modal{display:none!important}.tbl-wrap{max-height:none;overflow:visible}}
</style>
</head>
<body>

<svg style="position:absolute;width:0;height:0" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
<defs><linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
<stop offset="0%" stop-color="#6fb0f8"/><stop offset="100%" stop-color="#155fce"/>
</linearGradient></defs>
</svg>

<div class="wrap">

  <div class="hero card">
    <div class="hero-main">
      <div class="eyebrow">CTF SOLVING DASHBOARD</div>
      <h1>CTFPilot 解题看板</h1>
      <p>每道题的 triage → fast → deep 全过程实时入库。题型分布、解题率、经验库一目了然。</p>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:12px">
        <span class="chip">#ctfpilot</span>
        <span class="chip" style="background:var(--green-s);border-color:#c9efdf;color:#0b7a53">轨迹记忆 v2</span>
        <span class="chip"><span class="cat misc">misc</span><span class="cat reverse">reverse</span><span class="cat crypto">crypto</span><span class="cat forensics">forensics</span><span class="cat pwn">pwn</span></span>
      </div>
    </div>
    <div class="hero-side">
      <div class="hstat"><span>总题数</span><b id="hsTotal">--</b></div>
      <div class="hstat"><span>解出</span><b id="hsSolved">--</b></div>
      <div class="hstat"><span>解题率</span><b id="hsRate">--</b></div>
      <div class="hstat"><span>经验</span><b id="hsLessons">--</b></div>
    </div>
  </div>

  <div class="kpi-grid" id="kpiGrid"></div>

  <div class="tabs" id="tabs">
    <div class="tab active" data-target="overview">概览</div>
    <div class="tab" data-target="ledger">运行台账</div>
    <div class="tab" data-target="detectors">检测器矩阵</div>
    <div class="tab" data-target="knowledge">知识库</div>
  </div>

  <section id="overview">
    <div class="grid-2">
      <div class="card panel">
        <div class="panel-head"><h3><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg>解题率</h3><span class="note">累计口径</span></div>
        <div class="donut-wrap"><svg class="donut" viewBox="0 0 120 120">
          <circle class="track" cx="60" cy="60" r="48"/>
          <circle class="ring" id="donutRing" cx="60" cy="60" r="48"/>
        </svg>
        <div class="donut-center"><strong id="rateText">--</strong><span>解出率</span></div></div>
        <div class="legend">
          <span><i style="background:#2b7de9"></i>成功 <b id="lgSolved">--</b></span>
          <span><i style="background:#dfe9f5"></i>未解 <b id="lgRest">--</b></span>
        </div>
        <div style="display:flex;gap:8px;margin-top:10px">
          <div style="flex:1;border:1px solid var(--line);border-radius:9px;padding:7px 10px;display:flex;justify-content:space-between;font-size:11px;color:var(--muted)"><span>去重题目</span><b id="distinctNum">--</b></div>
          <div style="flex:1;border:1px solid var(--line);border-radius:9px;padding:7px 10px;display:flex;justify-content:space-between;font-size:11px;color:var(--muted)"><span>运行模式</span><b id="modeCount">--</b></div>
        </div>
      </div>
      <div class="card panel">
        <div class="panel-head"><h3><svg viewBox="0 0 24 24"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>题型分布</h3><span class="note">成功 / 总</span></div>
        <div class="bars" id="catBars"></div>
      </div>
    </div>
    <div class="grid-2">
      <div class="card panel">
        <div class="panel-head"><h3><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3"/><path d="M13 15h4"/></svg>运行模式构成</h3><span class="note">按记录数</span></div>
        <div class="stack" id="modeStack"></div>
        <div class="stack-legend" id="modeLegend"></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px">
          <div style="border:1px solid var(--line);border-radius:9px;padding:7px 10px;display:flex;justify-content:space-between;font-size:11px;color:var(--muted)"><span>检查点</span><b id="cpNum">--</b></div>
          <div style="border:1px solid var(--line);border-radius:9px;padding:7px 10px;display:flex;justify-content:space-between;font-size:11px;color:var(--muted)"><span>题目记录</span><b id="chNum">--</b></div>
        </div>
      </div>
      <div class="card panel">
        <div class="panel-head"><h3><svg viewBox="0 0 24 24"><path d="M4 19a2 2 0 0 1 2-2h13"/><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v16H6.5A2.5 2.5 0 0 0 4 21.5"/></svg>最近活动</h3><span class="note">最新 8 条</span></div>
        <div id="recentList"></div>
      </div>
    </div>
  </section>

  <section id="ledger" style="display:none">
    <div class="card">
      <div class="toolbar">
        <label class="search"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#7c8da4" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m16.5 16.5 4 4"/></svg>
          <input id="q" type="search" placeholder="搜索题目…" autocomplete="off">
        </label>
        <div class="filters" id="catFilters"></div>
      </div>
      <div class="tbl-wrap">
        <table>
          <thead><tr><th>题目</th><th>题型</th><th>结果</th><th>模式</th><th class="right">flag</th><th>最近</th></tr></thead>
          <tbody id="ledgerBody"></tbody>
        </table>
      </div>
      <div style="padding:8px 13px;border-top:1px solid var(--line);font-size:10.8px;color:#7c8da4;background:#fbfdff">
        <span id="ledgerCount">--</span>
      </div>
    </div>
  </section>

  <section id="detectors" style="display:none">
    <div class="card" style="margin-bottom:12px">
      <div class="panel-head"><h3><svg viewBox="0 0 24 24"><path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/></svg>快通道检测器</h3><span class="note">fast phase</span></div>
      <div id="fastDetectors"></div>
    </div>
    <div class="card">
      <div class="panel-head"><h3><svg viewBox="0 0 24 24"><path d="M9.5 4A3.5 3.5 0 0 0 6 7.5 3.5 3.5 0 0 0 4 14a3 3 0 0 0 2 5 3.5 3.5 0 0 0 6.5 1.5"/><path d="M14.5 4A3.5 3.5 0 0 1 18 7.5 3.5 3.5 0 0 1 20 14a3 3 0 0 1-2 5 3.5 3.5 0 0 1-6.5 1.5"/><path d="M12 8h2.5M12 12h3M12 16h2"/></svg>深通道策略梯</h3><span class="note">exec / plan / verify phase</span></div>
      <div id="deepDetectors"></div>
    </div>
  </section>

  <section id="knowledge" style="display:none">
    <div class="card" style="margin-bottom:12px">
      <div class="panel-head"><h3><svg viewBox="0 0 24 24"><path d="M4 19a2 2 0 0 1 2-2h13"/><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v16H6.5A2.5 2.5 0 0 0 4 21.5"/></svg>经验库</h3><span class="note" id="lessonSummary">--</span></div>
      <div id="lessonList"></div>
    </div>
  </section>

  <footer>
    CTFPilot 解题看板 · 数据快照 <b id="footTime">--</b> · 仅用于授权比赛与本地靶场
  </footer>
</div>

<div class="modal" id="detailModal">
  <div class="modal-card">
    <div class="modal-head"><h3 style="font-size:15px">记录详情</h3><button class="icon-btn" onclick="closeModal('detailModal')">✕</button></div>
    <p style="font-size:12px;color:var(--muted);margin:4px 0 12px" id="detailSub">--</p>
    <dl style="display:grid;grid-template-columns:88px 1fr;gap:7px 12px;font-size:12px;margin:0" id="detailGrid"></dl>
    <div class="flag-box" id="detailFlag">--</div>
  </div>
</div>
<div class="toast" id="toast">已复制</div>

<script>
const DATA = __DATA_JSON__;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => Number(n).toLocaleString('zh-CN');

$('#footTime').textContent = DATA.meta.generated;
$('#hsTotal').textContent = fmt(DATA.stats.total);
$('#hsSolved').textContent = fmt(DATA.stats.solved);
$('#hsRate').textContent = DATA.stats.rate + '%';
$('#hsLessons').textContent = fmt(DATA.stats.lessons);

const kpis = [
  {l:'题库记录',n:DATA.stats.total,s:'去重 ' + DATA.stats.distinct + ' 道',t:'b'},
  {l:'成功解出',n:DATA.stats.solved,s:'记录口径 ' + DATA.stats.rate + '%',t:'g'},
  {l:'检查点',n:DATA.stats.checkpoints,s:'writeup 部分得分',t:'p'},
  {l:'经验入库',n:DATA.stats.lessons,s:'可 recall 检索',t:'a'},
  {l:'记录总数',n:DATA.stats.challs,s:'含重复运行',t:'b'},
  {l:'覆盖题型',n:(new Set(DATA.bycat.map(c=>c.cat))).size,s:'分类数',t:'p'},
];
$('#kpiGrid').innerHTML = kpis.map((k,i)=>`<div class="card kpi">
  <span class="kpi-ic ${k.t}"><svg viewBox="0 0 24 24"><path d="M12 3 4 6v6c0 4.5 3.2 8.2 7.5 9.5 4.3-1.3 7.5-5 7.5-9.5V6L12 3z"/></svg></span>
  <div class="kpi-label">${esc(k.l)}</div><div class="kpi-num">${fmt(k.n)}</div><div class="kpi-sub">${esc(k.s)}</div></div>`).join('');
$$('.kpi-num').forEach(el => {
  const target = Number(el.textContent.replace(/,/g,'')), dur = 650, t0 = performance.now();
  (function step(t){const p=Math.min(1,(t-t0)/dur),e=1-Math.pow(1-p,3);
    el.textContent=fmt(Math.round(target*e));if(p<1)requestAnimationFrame(step);
  })(t0);
});

const C = 2 * Math.PI * 48;
$('#rateText').textContent = DATA.stats.rate + '%';
$('#lgSolved').textContent = fmt(DATA.stats.solved);
$('#lgRest').textContent = fmt(DATA.stats.total - DATA.stats.solved);
$('#distinctNum').textContent = DATA.stats.distinct;
$('#modeCount').textContent = DATA.modes.length + ' 种';
$('#cpNum').textContent = DATA.stats.checkpoints;
$('#chNum').textContent = DATA.stats.challs;
const ring = $('#donutRing');
ring.style.strokeDasharray = C.toFixed(2);
ring.style.strokeDashoffset = C.toFixed(2);
setTimeout(()=>{ring.style.strokeDashoffset = (C*(1-DATA.stats.rate/100)).toFixed(2)},150);

const toneMap = {misc:'',reverse:'green',crypto:'amber',forensics:'purple',pwn:'red',web:'red',osint:'green'};
const maxCat = Math.max(...DATA.bycat.map(x=>x.total),1);
$('#catBars').innerHTML = DATA.bycat.map(c=>{
  const wT = Math.max(3,Math.round(c.total/maxCat*100));
  const wS = c.total?Math.round((c.solved||0)/maxCat*100):0;
  return `<div class="bar-row"><div class="bar-name">${esc(c.cat)}</div>
    <div class="bar-track"><span class="bar-total" style="width:${wT}%"></span><span class="bar-fill ${toneMap[c.cat]||''}" style="width:${wS}%"></span></div>
    <div class="bar-val"><b>${(c.solved||0)}</b>/${c.total}</div></div>`;
}).join('');

const mTone = {auto:'#2b7de9',deep:'#5b9cf3',fast:'#9cc4f8',learn:'#c9defa'};
const totM = DATA.modes.reduce((a,b)=>a+b.count,1);
$('#modeStack').innerHTML = DATA.modes.map(m=>`<span style="width:${(m.count/totM*100).toFixed(1)}%;background:${mTone[m.mode]||'#dbe8f7'}" title="${m.mode} ${m.count}"></span>`).join('');
$('#modeLegend').innerHTML = DATA.modes.map(m=>`<span><i style="background:${mTone[m.mode]||'#dbe8f7'}"></i>${esc(m.mode)} <b>${m.count}</b></span>`).join('');

const recent = DATA.challs.slice(0,8);
$('#recentList').innerHTML = recent.map(c=>`
  <div class="lesson">
    <span class="cat ${c.cat}">${esc(c.cat)}</span>
    <div><div style="font-size:12px;color:#33465f">${esc(c.name)}</div>
    <div class="src">${c.solved?'✓ 已解出':'未解'} · ${esc(c.time||'')}</div></div>
  </div>`).join('') || '<div style="padding:16px;text-align:center;color:#7c8da4;font-size:12px">暂无记录</div>';

const secs = $$('section');
const tabsEl = $$('#tabs .tab');
function showTab(id){
  secs.forEach(s=>s.style.display = s.id===id?'block':'none');
  tabsEl.forEach(t=>t.classList.toggle('active',t.dataset.target===id));
}
tabsEl.forEach(t=>t.addEventListener('click',()=>showTab(t.dataset.target)));

const state={cat:'all',q:''};
const cats=['all'].concat([...new Set(DATA.bycat.map(c=>c.cat))]);
$('#catFilters').innerHTML = cats.map(c=>`<button class="fbtn ${c==='all'?'on':''}" data-cat="${c}">${c==='all'?'全部':c}</button>`).join('');
$$('#catFilters .fbtn').forEach(b=>b.addEventListener('click',()=>{
  state.cat=b.dataset.cat;$$('#catFilters .fbtn').forEach(x=>x.classList.toggle('on',x===b));renderLedger();
}));
$('#q').addEventListener('input',e=>{state.q=e.target.value.trim().toLowerCase();renderLedger();});

function renderLedger(){
  let rows = DATA.challs.slice();
  if(state.cat!=='all') rows=rows.filter(r=>r.cat===state.cat);
  if(state.q) rows=rows.filter(r=>(r.name+r.cat).toLowerCase().includes(state.q));
  $('#ledgerBody').innerHTML = rows.map(r=>`
    <tr data-name="${esc(r.name)}">
      <td><span class="name-cell">${esc(r.name)}</span></td>
      <td><span class="cat ${r.cat}">${esc(r.cat)}</span></td>
      <td><span class="pill ${r.solved?'ok':'no'}">${r.solved?'已解出':'未解'}</span></td>
      <td><span class="badge ${r.mode||'gray'}">${esc(r.mode||'')}</span></td>
      <td class="right muted">${r.flag?`<span style="font-family:Cascadia Code,Consolas,monospace;font-size:11px;color:#1b4d87">${esc(r.flag)}</span>`:'—'}</td>
      <td class="muted">${esc(r.time||'')}</td>
    </tr>`).join('') || '<tr><td colspan="6" style="text-align:center;padding:26px;color:#7c8da4">无匹配记录</td></tr>';
  $('#ledgerCount').textContent = `显示 ${rows.length} / ${DATA.challs.length} 条`;
  $$('#ledgerBody tr[data-name]').forEach(tr=>tr.addEventListener('click',()=>openDetail(tr.dataset.name)));
}
renderLedger();

function openDetail(name){
  const rs = DATA.challs.filter(c=>c.name===name);
  if(!rs.length) return;
  const latest = rs[0], solved = rs.some(c=>c.solved);
  const flag = rs.find(c=>c.solved&&c.flag)?.flag || '';
  $('#detailSub').textContent = latest.name;
  $('#detailGrid').innerHTML = `
    <dt style="color:var(--muted)">题型</dt><dd><span class="cat ${latest.cat}">${esc(latest.cat)}</span></dd>
    <dt style="color:var(--muted)">结果</dt><dd>${solved?'<span class="pill ok">已解出</span>':'<span class="pill no">未解</span>'}</dd>
    <dt style="color:var(--muted)">运行次数</dt><dd>${rs.length} 次</dd>
    <dt style="color:var(--muted)">模式</dt><dd><span class="badge ${latest.mode||'gray'}">${esc(latest.mode||'')}</span></dd>
    <dt style="color:var(--muted)">最近</dt><dd>${esc(latest.time||'')}</dd>
    <dt style="color:var(--muted)">编号</dt><dd class="mono">${rs.map(x=>'#'+x.id).join(' · ')}</dd>`;
  $('#detailFlag').textContent = flag || '本题没有记录到 flag';
  $('#detailModal').classList.add('open');
  document.body.style.overflow='hidden';
}
function closeModal(id){document.getElementById(id).classList.remove('open');document.body.style.overflow='';}
document.getElementById('detailModal').addEventListener('click',e=>{if(e.target.id==='detailModal')closeModal('detailModal')});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal('detailModal')});

function detRowsHtml(dets){
  if(!dets.length) return '<div style="padding:14px;text-align:center;color:#7c8da4;font-size:12px">暂无数据</div>';
  return dets.map(d=>{
    const pct = d.attempts ? Math.round((d.hits||0)/d.attempts*100) : 0;
    return `<div class="det-row">
      <span class="cat ${d.phase==='fast'?'misc':'reverse'}">${esc(d.phase)}</span>
      <div><div class="nm">${esc(d.detector)}</div><div class="det-bar" style="margin-top:4px"><div class="det-fill" style="width:${Math.max(4,pct)}%"></div></div></div>
      <div class="muted" style="text-align:right">${(d.hits||0)} / ${d.attempts}</div>
      <div style="text-align:right"><b style="color:${pct>=40?'#12996b':pct>=15?'#b97800':'#7a8ba1'}">${pct}%</b></div>
    </div>`;
  }).join('');
}
$('#fastDetectors').innerHTML = detRowsHtml(DATA.detectors.filter(d=>d.phase==='fast'));
$('#deepDetectors').innerHTML = detRowsHtml(DATA.detectors.filter(d=>d.phase!=='fast'));

$('#lessonSummary').textContent = `${DATA.lessons.length} 条经验 · 覆盖 ${new Set(DATA.lessons.map(l=>l.cat)).size} 类题型`;
$('#lessonList').innerHTML = DATA.lessons.map(l=>`
  <div class="lesson">
    <span class="cat ${l.cat}">${esc(l.cat)}</span>
    <div><div style="color:#33465f">${esc(l.tech)}</div>
    <div class="src">${esc(l.src||'')} · ${esc((l.snippet||'').slice(0,80))}</div></div>
  </div>`).join('') || '<div style="padding:16px;text-align:center;color:#7c8da4;font-size:12px">暂无经验</div>';

const io = new IntersectionObserver(es=>{es.forEach(en=>{if(en.isIntersecting){
  tabsEl.forEach(t=>t.classList.toggle('active',t.dataset.target===en.target.id));
}})},{rootMargin:'-100px 0px -60% 0px',threshold:0});
secs.forEach(s=>io.observe(s));
</script>
</body>
</html>"""


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
    p.add_argument("--benchmark", action="store_true",
                   help="unattended mode: no user interaction; print a "
                        "machine-parsable [benchmark] result line")
    p.add_argument("--json", action="store_true",
                   help="write _ctfpilot_result.json into the challenge dir "
                        "and print [json] <abs path>")

    p = sub.add_parser("bench-pull")
    p.add_argument("--pool", required=True,
                   help="challenge pool dir (one challenge per subdir)")
    p.add_argument("--dest", required=True,
                   help="destination dir (pulled challenges + ledger land here)")
    p.add_argument("--tag", default="",
                   help="batch tag recorded in the ledger")

    p = sub.add_parser("learn-writeup")
    p.add_argument("file")
    p.add_argument("--name")

    p = sub.add_parser("recall")
    p.add_argument("--category")
    p.add_argument("--query", help="signal keywords, e.g. 'NTRU lattice LLL'")

    p = sub.add_parser("rebuild-knowledge")
    p.add_argument("dir", help="writeups directory (repo ctfpilot/writeups)")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("eval-retrieval")
    p.add_argument("writeups", nargs="?",
                   help="writeups dir (default: dir used by last rebuild-knowledge)")
    p.add_argument("--save-baseline", action="store_true")

    p = sub.add_parser("fixations")
    p.add_argument("--min", type=int, default=3)

    sub.add_parser("stats")
    sub.add_parser("selftest")

    p = sub.add_parser("dashboard")
    p.add_argument("--out", help="output HTML file path (default: CTFPilot-看板.html)")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="print the dashboard DATA blob as JSON instead of writing HTML")

    p = sub.add_parser("record-win")
    p.add_argument("--name", required=True, help="challenge name")
    p.add_argument("--category", required=True, help="category")
    p.add_argument("--flag", required=True, help="the confirmed flag")
    p.add_argument("--dir", default="", help="optional challenge directory")
    p.add_argument("--mode", default="manual", help="mode label for the ledger row")
    p.add_argument("--summary", default="", help="optional one-line summary")


    args = ap.parse_args()
    cfg = load_config()
    if args.cmd == "solve":
        sys.exit(do_solve(args, cfg))
    elif args.cmd == "bench-pull":
        sys.exit(do_bench_pull(args, cfg))
    elif args.cmd == "learn-writeup":
        sys.exit(do_learn_writeup(args, cfg))
    elif args.cmd == "recall":
        sys.exit(do_recall(args, cfg))
    elif args.cmd == "rebuild-knowledge":
        sys.exit(do_rebuild_knowledge(args, cfg))
    elif args.cmd == "eval-retrieval":
        sys.exit(do_eval_retrieval(args, cfg))
    elif args.cmd == "fixations":
        sys.exit(do_fixations(args, cfg))
    elif args.cmd == "stats":
        sys.exit(do_stats(args, cfg))
    elif args.cmd == "selftest":
        sys.exit(do_selftest(args, cfg))
    elif args.cmd == "dashboard":
        sys.exit(do_dashboard(args, cfg))
    elif args.cmd == "record-win":
        sys.exit(do_record_win(args, cfg))


if __name__ == "__main__":
    main()
