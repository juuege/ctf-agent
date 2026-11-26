"""CTFPilot CLI.

Usage (run with the project python, UTF-8 mode):
  python -X utf8 ctfpilot.py solve <challenge_dir> [--mode auto|fast|deep] [--brain auto|llm|mock]
                                         [--benchmark] [--json]
  python -X utf8 ctfpilot.py bench-pull --source <local:dir|gh-org:CTF-Archives|gh:owner/repo> --dest <dir> [--tag t]
                                          (legacy: --pool <dir> = --source local:<dir>)
  python -X utf8 ctfpilot.py dedup-diff [--force]                    # 判重自愈只读预览：would-be 重建 diff（零写入；evolve-20261117-17）
  python -X utf8 ctfpilot.py learn-writeup <writeup.md> --name <name>
  python -X utf8 ctfpilot.py recall --category crypto
  python -X utf8 ctfpilot.py stats
  python -X utf8 ctfpilot.py selftest
  python -X utf8 ctfpilot.py dashboard [--out file.html] [--json]
  python -X utf8 ctfpilot.py record-win --name N --category C --flag F
  python -X utf8 ctfpilot.py account-model <dir> [--flag F --time-s T]    # 模型通道结果入账（侧车 _ctfpilot_model_result.json）
  python -X utf8 ctfpilot.py adjudicate <dir> --true-status S --reason R   # 裁决块 JSON 安全写入（--set-solved 0|1 --set-flag F --field k=v；evolve-20261117-05: repr 转义史鉴）
  python -X utf8 ctfpilot.py adjudicate-db --db-row N --true-status S --reason R  # DB 行裁决面：WAL checkpoint+备份+单事务+verify（evolve-20261117-18；rsi-086 手术纪律工具化）
  python -X utf8 ctfpilot.py rsi [cycle|list|approve PID|reject PID]   # 自改进环：提案→人审→采纳台账
  python -X utf8 ctfpilot.py arena [register|stats|pull|record]        # 竞技题库：分级/计分榜（不污染生产轨迹）
  python -X utf8 ctfpilot.py research [add TOPIC|list|evidence|ingest] # 研究队列：草稿模板+证据指针入库路由（离线诚实）
  python -X utf8 ctfpilot.py eval-suite [--save-baseline]             # 全指标回归门：能力/效率/覆盖/健康/知识/检索六维
  python -X utf8 ctfpilot.py redrill [status|diag|...]               # k-fold 交叉重刷（redrill.py；diag=运维诊断快照）
"""
import argparse
import json
import os
import random
import shutil
import sys
import time

import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import load_config, compiled_flag_patterns, path_under_project, PROJECT_ROOT
from core.flagcheck import FlagChecker
from core.router import route
from core.sandbox import Sandbox
from core.llm import LLMRouter
from memory.trajstore import TrajStore
from learn import writeup_learner

import metrics as M
import rsi as R
import arena as A
import research as RS


def log(msg):
    print(msg, flush=True)


def get_stores(cfg):
    # CTFPILOT_DB env override: selftest runs on an isolated DB so smoke runs
    # never pollute production memory (lessons/stats/recall).
    db = os.environ.get("CTFPILOT_DB") or path_under_project(cfg["memory"]["db_path"])
    return TrajStore(db)


# [evolve-15 + evolve-32] fixture directories: a challenge whose dir lives
# under one of these markers is a synthesized holdout/A-B/selftest/replay
# item, never a production benchmark row. evolve-32 adds the scratch root
# (.temp): engine-internal replays and profile copies under .temp wrote rows
# 939-948 into PRODUCTION during rsi-099 because this guard did not see them.
_SYNTHETIC_DIR_MARKERS = (os.path.join("arena", "tasks") + os.sep,
                          os.path.join("tests", "challs") + os.sep,
                          ".temp" + os.sep)


def _synthetic_db_guard(cfg, chall_dir):
    """[evolve-15 + evolve-32] Synthetic-source DB guard: solving a challenge
    that lives under arena/tasks (holdout/A-B fixtures), tests/challs
    (selftest smoke fixtures) or a .temp scratch root (engine-internal
    replays/profile copies) without CTFPILOT_DB set would write the run into
    the PRODUCTION store -- that is exactly how live rows 882/884/885/886,
    the RED replay id=903 (evolve-15) and the rsi-099 replay rows 939-948
    (evolve-32) got there and diluted solved_rate with synthesized outcomes.
    Route such runs to the isolated <db>.holdout store instead; an explicit
    CTFPILOT_DB always wins, so the selftest (.temp/selftest.sqlite) and arena
    holdout flows that already isolate themselves are untouched."""
    if os.environ.get("CTFPILOT_DB"):
        return
    d = os.path.normcase(os.path.abspath(chall_dir))
    if not any(os.path.normcase(m) in d for m in _SYNTHETIC_DIR_MARKERS):
        return
    iso = path_under_project(cfg["memory"]["db_path"]) + ".holdout"
    os.environ["CTFPILOT_DB"] = iso
    log("[store] synthetic-source guard: %s -> isolated store (production "
        "untouched) [evolve-15/32]" % iso)


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


def _replay_guard_suppress(store, cid, name, chall_dir, flag, log, provenance):
    """[evolve-20261117-43 / rsi-114] production replay guard (rsi-111
    lesson, live row 964). A SOLVED verdict whose (name, dir_path, flag)
    triple already has an EARLIER CREDITED row is a replay duplicate, not
    a fresh outcome: suppress THIS row's credit in place (solved=0, flag
    cleared, one audited attempt) -- the exact adjudicate-db surgery that
    had to be run by hand on row 964, applied automatically at write
    time. Everything else passes through untouched: a DIFFERENT flag
    (post-fix re-solve / corrected credit), an adjudicated-away prior
    (its solved is no longer 1), a different directory (republished
    mirror), an unsolved rerun (never reaches here), or a first solve.
    The guard must never mask a real solve: any failure path reads as
    no-prior (fail-open). Returns True when this row was suppressed."""
    try:
        prior = store.find_replay_prior(name, chall_dir, flag, cid)
    except Exception:
        return False
    if prior is None:
        return False
    try:
        store.log_attempt(cid, "verify", "replay-guard",
                          "same-(name,dir,flag) replay of row id=%s; "
                          "duplicate credit suppressed" % prior,
                          False, flag,
                          error_class="semantic:replay-duplicate")
        store.finish_challenge(cid, False, "")
    except Exception:
        return False
    if provenance is not None:
        provenance["status"] = "replay-suppressed"
        provenance["replay_prior"] = prior
    log("[db] replay-guard: SOLVE replays already-credited row id=%s "
        "(same name+dir+flag); this row's credit suppressed (honest "
        "duplicate, not double-counted)" % prior)
    return True


def do_solve(args, cfg):
    chall_dir = os.path.abspath(args.dir)
    if not os.path.isdir(chall_dir):
        log("[error] not a directory: %s" % chall_dir)
        return 2
    checker = FlagChecker(compiled_flag_patterns(cfg), extra_regex=args.flag_regex)
    sandbox = Sandbox(cfg, chall_dir)
    llm = LLMRouter(cfg, log=log)
    _synthetic_db_guard(cfg, chall_dir)  # [evolve-15]
    store = get_stores(cfg)

    t0 = time.time()
    log("[triage] scanning %s" % chall_dir)
    routing = route(chall_dir)
    log("[triage] category=%s confidence=%.2f" % (routing["category"], routing["confidence"]))
    for ev in routing["evidence"][:6]:
        log("   " + ev)

    name = args.name or os.path.basename(chall_dir.rstrip("\\/"))
    try:
        _prior = store.prior_attempt(name)
    except Exception:
        _prior = None
    if _prior:
        log("[solve] note: this challenge was processed before "
            "(id=%s mode=%s solved=%s) -- continuing; if this replays a known "
            "challenge, bench-pull dedup should have caught it upstream"
            % (_prior["id"], _prior["mode"], _prior["solved"]))
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
        # fast_done: mode=auto already ran the identical fastpath chain on
        # the same chall_dir/files above; the deep Strategy-0 quick pass then
        # skips its rerun (duplicate work + duplicate db rows). mode=deep
        # keeps the quick pass as its only fastpath carrier.
        flag, deep_attempts = run_deep_loop(name, chall_dir, routing,
                                            routing["files"], checker,
                                            sandbox, llm, store, cid, cfg, log,
                                            brain=args.brain,
                                            benchmark=bool(getattr(args, "benchmark", False)),
                                            fast_done=(args.mode == "auto"),
                                            reject_flag=_reject_raw_echo)
        attempts.extend(deep_attempts)
        if flag and _reject_raw_echo(flag):
            flag = None

    dt = time.time() - t0
    if flag:
        provenance["status"] = "derived"
        log("[result] SOLVED in %.1fs" % dt)
        log("[result] FLAG: %s" % flag)
        # [evolve-20261117-43 / rsi-114] production replay guard: a
        # same-(name,dir,flag) re-solve of an already-credited row is a
        # duplicate credit (rsi-111 lesson, live row 964) -- suppress it
        # in place instead of letting a second solved row through. Any
        # other outcome (different flag / adjudicated prior / mirror dir
        # / first solve) passes through untouched; a suppressed replay
        # also skips the auto-lesson + bump_success so replay volume can
        # never inflate technique stats either.
        if (not getattr(args, "allow_replay", False)
                and _replay_guard_suppress(store, cid, name, chall_dir,
                                           flag, log, provenance)):
            # replay is not a fresh outcome: rc, the benchmark line, the
            # trace and result JSON all follow this (the engine DID find
            # the flag -- stdout keeps the SOLVED/FLAG lines above).
            # --allow-replay is the explicit informed exit: self-certified
            # replays (selftest fixtures, deliberate reproduction runs)
            # skip the guard and keep the exact incumbent write behavior.
            flag = None
        if flag:
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


def _pull_exclusion(ledger):
    """evolve-20261116-20 (dup 7th hole): the candidate-picker exclusion
    set carries REAL pulls only. Persisted skipped_keys used to be loaded
    into it, permanently locking every past skip against the audit-only
    contract -- after an FP_EXCLUDE fix or an index rebuild a misfired
    skip could then never self-heal (live lock evidence: 8 pure-lock
    keys made three repos silently 'exhausted' with no skip reason).
    True dups keep getting re-caught by dedup itself on every pull, so
    re-evaluating old skips costs only local index lookups."""
    return set(ledger.get("keys") or [])


def _merge_skips(persisted, in_call):
    """Append this call's skips to the persisted audit-only list
    (sorted union, capped at 500, never fed back into the picker)."""
    return sorted((set(persisted or []) | set(in_call)))[-500:]


def _pull_log(dest, msg):
    """rsi-123: append one timestamped line to dest/_bench_pull.log.
    Survives outer-tool timeout kills that discard stdout -- after a
    truncated pull the file shows exactly how far the chain got
    (source, attempt, result, budget), so ledger repair needs no
    guessing. Best-effort: log IO failures never break the pull."""
    try:
        import datetime as _dt
        with open(os.path.join(dest, "_bench_pull.log"), "a",
                  encoding="utf-8") as f:
            f.write("%s %s\n" % (_dt.datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def do_bench_pull(args, cfg):
    """Benchmark source pull: randomly pick a challenge that is not yet
    under dest, materialize it, and keep a cumulative ledger. Powers the
    unattended benchmark loop; return 3 when the source is exhausted so a
    runner can distinguish it from a hard failure.

    Sources (--source, or legacy --pool for a local dir):
      local:<pool_dir>   copy a pool challenge minus engine artifacts
                         (__pycache__ / _ctfpilot* / .temp); dedup by
                         subdir name. Returns 3 when the pool is exhausted.
      gh-org:<org>       random year-named repo of a GitHub org (e.g.
                         CTF-Archives), then a random challenge dir inside;
                         downloaded over the stable api.github.com channel.
      gh:<owner>/<repo>  one specific GitHub repository (same walk).
    GitHub pulls dedup on ledger keys (owner/repo::path) so sources can be
    mixed freely; the org listing is cached 24h to keep API usage low."""
    dest = os.path.abspath(args.dest)
    os.makedirs(dest, exist_ok=True)
    ledger_path = os.path.join(dest, "_bench_ledger.json")
    ledger = {"pulled": 0, "remaining": -1, "history": [], "tag": "", "keys": []}
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            ledger.update(saved)
    except (OSError, ValueError):
        pass
    source = args.source or ("local:" + args.pool if args.pool else "")
    if not source:
        log("[error] need --source <local:dir|gh-org:<org>|gh:<owner>/<repo>> "
            "or legacy --pool <dir>")
        return 2
    if source.startswith("gh:") or source.startswith("gh-org:"):
        from core.ghsource import gh_pull, SourceError, _sanitized
        from core import dedup as _dedup
        _pull_log(dest, "start source=%s pulled=%d keys=%d"
                  % (source, ledger.get("pulled", 0),
                     len(ledger.get("keys") or [])))
        _index, _ipath = _dedup.load_index()
        used = _pull_exclusion(ledger)
        # evolve-20261116-20: skips live in-call only -- the picker
        # exclusion (used) must never absorb the persisted skip history,
        # or a misfire locks its candidate forever against the audit-only
        # contract (pre-fix live probe: 3 repos 'exhausted' with no skip
        # reason). Past skips get re-evaluated by precheck / the
        # post-download guards on every pull (local index lookups, zero
        # downloads) and merge back into the audit list at save time.
        skipped = set()

        def _precheck(pkey, ptitle, pentries):
            """Pre-download duplicate guard (evolve-20261116-16): the same
            two checks the post-download guards run, decided from the API
            listing alone -- name layer on key+title, then the git-blob
            layer on (name, sha, size) pairs. A hit rejects the pick
            BEFORE any content download (live precedent: the 2026-11-16
            ISCC burn downloaded 734 files / 7 minutes for a candidate the
            post-download guards then rejected as a duplicate)."""
            _d, _w = _dedup.is_dup(
                _index, "%s %s" % (pkey, _sanitized(ptitle)),
                whole_repo=pkey.endswith("::."))
            if not _d:
                _d, _w = _dedup.is_dup_blobs(
                    _index, ((e.get("name", ""), e.get("sha", ""),
                              e.get("size", 0)) for e in pentries))
            if _d:
                skipped.add(pkey)   # audit-only, same semantics as the
                                    # post-download skip path
            return _w if _d else ""

        chall_dir = None
        nfiles = nskip = 0
        key = ""
        _t_attempt0 = time.time()
        for _attempt in range(8):
            try:
                key, chall_dir, nfiles, nskip = gh_pull(
                    source, dest, used | skipped,
                    cache_dir=os.path.join(dest, "_gh_cache"),
                    precheck=_precheck)
            except SourceError as e:
                _pull_log(dest, "fail attempt=%d source=%s error=%s"
                          % (_attempt, source, e))
                log("[bench-pull] source error: %s" % e)
                return 4
            if not key:
                _pull_log(dest, "exhausted attempt=%d source=%s reason=%s"
                          % (_attempt, source, chall_dir))
                log("[bench-pull] source exhausted: %s" % chall_dir)
                return 3
            # cross-source guard: the ledger only knows its own pulls; the
            # old timer's done.json (other TA account), the trajectory db and
            # learned cards hold the rest of the 'already done' set.
            # whole-repo candidates ('::.' keys) get exact-only name dedup
            # (evolve-20261116-11: a contest-root core is a substring of
            # its own single-challenge cores by construction, so the
            # fuzzy containment layer can only misfire there).
            _dup, _why = _dedup.is_dup(
                _index, "%s %s" % (key, os.path.basename(chall_dir)),
                whole_repo=key.endswith("::."))
            if _dup:
                _pull_log(dest, "dup-name attempt=%d key=%s why=%s"
                          % (_attempt, key, _why))
                log("[bench-pull] skip=duplicate(%s) key=%s" % (_why, key))
                shutil.rmtree(chall_dir, ignore_errors=True)
                skipped.add(key)   # same candidate is never picked twice
                                    # in this call; ledger keys record only
                                    # real materializations
                continue
            # attach-fingerprint guard (dup 4th hole): same challenge
            # re-packaged under an unrelated name passes name dedup but
            # shares content-file md5s with an already-materialized dir.
            _dup2, _why2 = _dedup.is_dup_dir(_index, chall_dir)
            if _dup2:
                _pull_log(dest, "dup-fp attempt=%d key=%s why=%s"
                          % (_attempt, key, _why2))
                log("[bench-pull] skip=duplicate(%s) key=%s" % (_why2, key))
                shutil.rmtree(chall_dir, ignore_errors=True)
                skipped.add(key)
                continue
            _dedup.note_pull_fp(_index, _ipath, chall_dir)
            _dedup.note_pull_blobs(_index, _ipath, chall_dir)
            _pull_log(dest, "ok attempt=%d key=%s files=%d"
                      % (_attempt, key, nfiles))
            break
        if chall_dir is None:
            _pull_log(dest, "exhausted-all-dup source=%s"
                      % source)
            log("[bench-pull] source exhausted: every candidate was a duplicate")
            return 3
        ledger["keys"] = sorted(used | {key})
        ledger["skipped_keys"] = _merge_skips(ledger.get("skipped_keys"),
                                              skipped)
        ledger["pulled"] = int(ledger.get("pulled", 0)) + 1
        ledger["history"] = (list(ledger.get("history", [])) +
                             [os.path.basename(chall_dir)])[-200:]
        ledger["tag"] = args.tag or ""
        with open(ledger_path, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)
        _pull_log(dest, "ledger-written pulled=%d key=%s attempt_s=%.1f"
                  % (ledger["pulled"], key, time.time() - _t_attempt0))
        log("[bench-pull] chall=%s" % chall_dir)
        log("[bench-pull] files=%d skipped=%d" % (nfiles, nskip))
        log("[bench-pull] ledger pulled=%d tag=%s source=%s" %
            (ledger["pulled"], ledger["tag"] or "-", source))
        return 0
    pool = source[len("local:"):] if source.startswith("local:") else (args.pool or "")
    pool = os.path.abspath(pool)
    if not os.path.isdir(pool):
        log("[error] pool not a directory: %s" % pool)
        return 2
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


def _knowledge_source_dir():
    """Production writeups source for knowledge builds [evolve-20261116-17]:
    the dir recorded by the last rebuild-knowledge (stats.json), falling
    back to the engine's default writeups/ tree. Returns (dir, exists)."""
    try:
        with open(path_under_project(
                os.path.join("skills", "knowledge", "stats.json")),
                "r", encoding="utf-8") as f:
            src = json.load(f).get("writeups_dir")
        if src and os.path.isdir(src):
            return src, True
    except (OSError, ValueError):
        pass
    fb = path_under_project("writeups")
    return fb, os.path.isdir(fb)


def _rebuild_knowledge_after_learn(cfg, text_path):
    """[evolve-20261116-17] learn-writeup used to end with the card saved and
    the DB row committed while cases.jsonl / stats.json kept the previous
    build -- rounds then reported case counts that were never on disk (dfjk
    2026-11-16 19:40: claimed 139, build stayed at 19:21 until the 21:21
    rebuild). A real learn now auto-refreshes the KB through the SAME
    production path as the CLI rebuild command (idempotent, stable case
    ids, curation overlay keeps applying), so the new card is immediately
    retrievable. Failure branches never roll back the learn (cards/DB/dedup
    are already committed); they surface an explicit "rebuild pending"
    notice instead of a silent stale claim. Returns 0 when the KB reflects
    the learn, non-zero when rebuild-knowledge must still be run by hand."""
    # [redrill] campaign 冻结: 交叉重刷考核期间不触发生产 rebuild
    # (rebuild 会全量 upsert writeups 目录 -> 孤儿卡 + 考批外泄面);
    # campaign finalize 后由收尾流程统一 rebuild 一次。
    try:
        import redrill as _rd
        _rst = _rd.active_state()
        if _rst:
            log("[learn] rebuild DEFERRED: redrill campaign %s is active "
                "(closed-book integrity keeps the KB frozen)" %
                _rst["campaign_id"])
            return 0
    except ImportError:
        pass
    src, exists = _knowledge_source_dir()
    if not exists:
        log("[learn] note: knowledge rebuild pending -- no writeups source "
            "dir; run rebuild-knowledge <writeups-dir>")
        return 2
    tp = os.path.normcase(os.path.abspath(text_path))
    sp = os.path.normcase(os.path.abspath(src))
    if not tp.startswith(sp + os.sep):
        log("[learn] note: writeup is outside the knowledge source tree "
            "(%s) -- card saved to skills/learned and the DB, but "
            "cases.jsonl will NOT see it; copy it under the tree and rerun "
            "rebuild-knowledge" % src)
        return 3
    try:
        rc = do_rebuild_knowledge(
            argparse.Namespace(dir=src, exclude=None, dry_run=False), cfg)
    except Exception as e:  # follow-up only: learn is already committed
        log("[learn] note: knowledge rebuild failed (%s) -- rebuild "
            "pending, run rebuild-knowledge %s" % (e, src))
        return 4
    if rc == 0:
        log("[learn] knowledge rebuilt: new card immediately retrievable "
            "[evolve-20261116-17]")
    else:
        log("[learn] note: rebuild-knowledge rc=%d -- knowledge rebuild "
            "pending, run rebuild-knowledge %s" % (rc, src))
    return rc


def _recall_closed_book(_rd, st, store, cat, args, qtokens):
    """闭卷 recall: 过滤→打分→top20; 不打 recent challenges (防真名泄漏);
    不打 hit_count (考批统计不入生产热度; 见 do_recall 常规路径注释)。"""
    mx, excl, fpats = _rd.recall_filter(st)
    rows = store.conn.execute(
        "SELECT l.id, l.technique, l.when_to_use, l.snippet, l.tags, l.ts, "
        "l.challenge_id, c.name FROM lessons l "
        "LEFT JOIN challenges c ON c.id=l.challenge_id "
        "WHERE l.category=? AND (l.snippet<>'' OR l.tags<>'')", (cat,)).fetchall()
    vis, why = [], {}
    for r in rows:
        okv, w = _rd.lesson_visible_closed_book(
            (r[0], r[6], r[7], r[2], r[3], r[4]), mx, excl, fpats)
        if okv:
            vis.append(r)
        else:
            why[w] = why.get(w, 0) + 1
    def _score(r):
        txt = "%s %s %s" % (r[2] or "", r[3] or "", r[4] or "")
        if not qtokens:
            return 0
        from knowledge import tokenize
        return len(qtokens & tokenize(txt))
    vis.sort(key=lambda r: (-_score(r), -r[5]))
    seen, out = set(), []
    for r in vis:
        if r[1] in seen:
            continue
        seen.add(r[1])
        extra = ""
        if r[3]:
            extra += " | evidence: %s" % r[3][:120]
        if r[4]:
            extra += " | tags: %s" % str(r[4])[:120]
        out.append("- %s | %s%s" % (r[1], r[2], extra))
        if len(out) >= 20:
            break
    print("== lessons for %s%s (redrill closed-book: fold %d/%d) ==" %
          (cat, (" (query: %s)" % args.query) if getattr(args, "query", None) else "",
           (st.get("active_fold") or 0) + 1, st.get("fold_count", 0)))
    if not out:
        print("  (closed-book view: no visible writeup-quality lessons)")
    for line in out:
        print(line)
    if why:
        print("== closed-book filter: %s ==" %
              ", ".join("%s=%d" % kv for kv in sorted(why.items())))
    return 0


def _redrill_exam_names(st):
    names = set()
    for f in st.get("folds", []):
        for g in f["groups"]:
            names.update(g["names"])
    return names


def do_learn_writeup(args, cfg):
    text_path = os.path.abspath(args.file)
    if not os.path.exists(text_path):
        log("[error] no such file: %s" % text_path)
        return 2
    with open(text_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    parsed = writeup_learner.parse_writeup(text)
    name = args.name or os.path.splitext(os.path.basename(text_path))[0]

    # [redrill] 入库守门: 考批期间, 考批题的 writeup 只有带引擎 verify
    # MATCH 证据才可入库（防再演式 writeup 污染生产知识库）。
    try:
        import redrill as _rd
        _rst = _rd.active_state()
        if _rst and name in _redrill_exam_names(_rst):
            _grp = _None_grab = None
            for _f in _rst["folds"]:
                for _g in _f["groups"]:
                    if name in _g["names"]:
                        _grp = _g
            if not (_grp and _grp.get("first_match_ts")):
                log("[learn][REJECTED] '%s' belongs to the active redrill "
                    "exam set and has no engine verify evidence -- a recited "
                    "writeup cannot enter the production KB" % name)
                return 2
            log("[learn] redrill slot %s is verify-backed; learn allowed "
                "(production rebuild deferred while campaign is active)"
                % _grp["slot"])
    except ImportError:
        pass

    from core import dedup as _dedup
    _index, _ipath = _dedup.load_index()
    _md5 = _dedup.md5_text(text)
    if (_index.get("writeups_done") or {}).get(name) == _md5:
        log("[learn] skip=duplicate: writeup '%s' already learned "
            "(identical md5 %s..); no new cards, no new db row" % (name, _md5[:8]))
        return 0
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
    _dedup.note_learned(_index, _ipath, name, _md5)
    log("[learn] noted in dedup index: future identical writeups skip")
    _rebuild_knowledge_after_learn(cfg, text_path)
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
    # [redrill] k-fold 闭卷视图: campaign 活跃期间, 考批题解/孤儿卡/
    # 冻结点后新卡一律不可见 (SOTA: ExpeL 四折验证的经验池隔离)
    _rd = None
    try:
        import redrill as _rd
    except ImportError:
        pass
    if _rd is not None:
        _st = _rd.active_state()
        if _st:
            return _recall_closed_book(_rd, _st, store, cat, args, qtokens)
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


def _load_exclude_paths(path):
    """Read a holdout JSON (lab format: {items: [{path: ...}, ...]} or a
    plain list) into a normalized set of writeup source paths."""
    try:
        with open(os.path.abspath(path), "r", encoding="utf-8") as f:
            hold = json.load(f)
    except (OSError, ValueError) as e:
        log("[error] cannot read --exclude file: %s" % e)
        return None
    items = hold.get("items", []) if isinstance(hold, dict) else hold
    paths = set()
    for it in items or []:
        p = (it.get("path") if isinstance(it, dict) else str(it)) or ""
        p = p.replace("\\", "/").lstrip("./")
        if p:
            paths.add(p)
    return paths


def do_rebuild_knowledge(args, cfg):
    """SOTA ingestion: writeups -> case cards (cases.jsonl) + lesson upsert.
    Idempotent: stable case ids mean re-runs refresh instead of duplicating.

    Holdout isolation (--exclude <holdout.json>): the listed writeups are
    held OUT of this build -- neither indexed as cards nor upserted as
    lessons. The isolated build lands in skills/knowledge-holdout (the
    production skills/knowledge is never clobbered) and lessons go to an
    isolated db (<db_path>.holdout, or $CTFPILOT_DB when already set), so
    production recall keeps its knowledge. Use for eval/solve-holdout
    runs; production rebuilds run WITHOUT --exclude."""
    from knowledge import build_cases, save_cases, rebuild_lessons, merge_refined
    src = os.path.abspath(args.dir)
    if not os.path.isdir(src):
        log("[error] not a directory: %s" % src)
        return 2
    cases = build_cases(src)
    # curation (refined signals / adjudicated categories) lives in the
    # PRODUCTION cases.jsonl overlay -- holdout builds must inherit it too,
    # then drop the excluded items, or they would regress to raw parse state.
    overlay = path_under_project(os.path.join("skills", "knowledge",
                                              "cases.jsonl"))
    cases = merge_refined(cases, overlay)
    excluded = 0
    if args.exclude:
        hold_paths = _load_exclude_paths(args.exclude)
        if hold_paths is None:
            return 2
        before = len(cases)
        cases = [c for c in cases if c["source"].lstrip("./") not in hold_paths]
        excluded = before - len(cases)
        log("[knowledge] holdout build: excluded=%d retained=%d" %
            (excluded, len(cases)))
    out = path_under_project(os.path.join(
        "skills", "knowledge-holdout" if args.exclude else "knowledge"))
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
    paths = save_cases(cases, out, extra_stats={
        "writeups_dir": src, "mode": "holdout" if args.exclude else "production",
        "excluded": excluded})
    log("[knowledge] wrote %s" % ", ".join(os.path.basename(p) for p in paths))
    if args.exclude:
        # isolated lessons db: production recall must keep holdout knowledge
        os.environ.setdefault(
            "CTFPILOT_DB",
            path_under_project(cfg["memory"]["db_path"]) + ".holdout")
        log("[knowledge] isolated lessons db: %s (production store untouched)" %
            os.environ["CTFPILOT_DB"])
    store = get_stores(cfg)
    n, dead, twins = rebuild_lessons(store, cases)
    log("[knowledge] lessons upserted: %d (recall now signal-scored); "
        "pruned dead-shadows=%d twins-merged=%d [evolve-20261117-07]" %
        (n, dead, twins))
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

    # 台账按「题」去重：同名题每次 solve 都插新行（见 TrajStore.new_challenge），
    # 每题的当前状态取 id 最大（最新）那一行；轨迹事件本身不删，只在展示层聚合。
    # 口径：mode='learn' 行是学 writeup 的导入记录，不是真实解题——KPI/分类/
    # 模式统计只计真实解题（存在任一非 learn 行的题即算解题题）；同名若只有
    # learn 行则 kind='learn'，侧栏打标「学习导入」且默认隐藏。
    latest_cte = """
WITH latest AS (
    SELECT name, MAX(id) AS id FROM challenges GROUP BY name
),
ledger AS (
    SELECT c.id, c.name, c.category, c.solved, c.flag, c.mode, c.summary,
           datetime(c.created_at, 'unixepoch', 'localtime') AS ts_str
    FROM challenges c JOIN latest l ON l.id = c.id
),
rowkind AS (
    SELECT d.name,
           CASE WHEN EXISTS (
               SELECT 1 FROM challenges x
               WHERE x.name = d.name AND COALESCE(x.mode, '') <> 'learn'
           ) THEN 'solve' ELSE 'learn' END AS kind
    FROM (SELECT DISTINCT name FROM ledger) d
)
"""
    stats_row = conn.execute(latest_cte + """
        SELECT COUNT(*) AS total, SUM(solved) AS solved
        FROM ledger ld JOIN rowkind k ON k.name = ld.name
        WHERE k.kind = 'solve'
    """).fetchone()
    total = stats_row[0] or 0
    solved = stats_row[1] or 0

    bycat_rows = conn.execute(latest_cte + """
        SELECT category, COUNT(*) AS total, SUM(solved) AS solved
        FROM ledger ld JOIN rowkind k ON k.name = ld.name
        WHERE k.kind = 'solve' GROUP BY category ORDER BY total DESC
    """).fetchall()

    chall_rows = conn.execute(latest_cte + """
        SELECT ld.id, ld.name, ld.category, ld.solved, ld.flag, ld.mode, ld.ts_str, k.kind
        FROM ledger ld JOIN rowkind k ON k.name = ld.name
        ORDER BY ld.id DESC LIMIT 200
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

    # 失败画像：只有真实解题（solve 行）才进入归因口径；'learn' 导入行的失败
    # 不反映真实做题难度。语义类（semantic:*）才是可行动信号，过程类只作背景。
    error_rows = conn.execute(latest_cte + """
        SELECT a.error_class, COUNT(*) AS cnt, SUM(a.success) AS ok
        FROM attempts a JOIN ledger ld ON ld.id = a.challenge_id
        JOIN rowkind k ON k.name = ld.name
        WHERE k.kind = 'solve' AND COALESCE(a.error_class, '')
              NOT IN ('', 'ok')
        GROUP BY a.error_class ORDER BY cnt DESC LIMIT 12
    """).fetchall()

    mode_rows = conn.execute(latest_cte + """
        SELECT mode, COUNT(*) AS cnt FROM ledger ld
        JOIN rowkind k ON k.name = ld.name
        WHERE k.kind = 'solve' GROUP BY mode ORDER BY cnt DESC
    """).fetchall()

    rate = round(solved * 100 / max(total, 1))

    bycat = [{"cat": r["category"], "total": r["total"],
              "solved": r["solved"] or 0} for r in bycat_rows]
    challs = [{"id": r["id"], "name": r["name"], "cat": r["category"],
               "mode": r["mode"] or "", "solved": bool(r["solved"]),
               "flag": r["flag"] or "", "time": r["ts_str"] or "",
               "kind": r["kind"]
              } for r in chall_rows]
    lessons = [{"cat": r["category"], "tech": r["technique"],
               "src": r["when_to_use"], "snippet": (r["snippet"] or "")[:300],
               "time": r["ts_str"] or ""
              } for r in lesson_rows]
    detectors = [{"phase": r["phase"], "detector": r["detector"],
                  "attempts": r["attempts"], "hits": r["hits"] or 0
                 } for r in det_rows]
    errors = [{"class": ("semantic:" + r["error_class"])
               if r["error_class"] == "raw-echo"
               else (r["error_class"] or "unknown"),
               "count": r["cnt"], "ok": r["ok"] or 0
              } for r in error_rows]
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
        "lessons": lessons, "detectors": detectors, "errors": errors,
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


def do_dedup_diff(args, cfg):
    """[evolve-20261117-17] Read-only preview of the dedup-index
    self-heal (core.dedup.self_heal_diff): what a load_index() rebuild
    WOULD change right now -- per-section added/purged/kept counts,
    schema drift, carried writeups_done -- without writing a single
    byte. Closes the evolve-20261117-14 finding (b) papercut: schema
    rounds previously mutated the production index first and
    hand-diffed copies aside (the rsi-081 manual-diff tax)."""
    from core import dedup as D
    rep = D.self_heal_diff(force=bool(getattr(args, "force", False)))
    log("[dedup-diff] %s" % json.dumps(rep, ensure_ascii=False, sort_keys=True))
    return 0


def do_account_model(args, cfg):
    """Account a model-channel (agent) solve into the trajectory store.

    The engine result file only covers the fast/deep channels. When the
    model channel solves a challenge (or burns a measurable dead end such
    as a 180s z3 timeout), that evidence must still land in the countable
    trajectory, or RSI/arena score the challenge as a full-penalty
    timeout (evolve-20261116-04).

    Source of truth: _ctfpilot_model_result.json in the challenge dir
    (fields: solved, flag, time_seconds, cost_tokens, category, summary,
    dead_ends [{tool, time_seconds, cost_tokens, note}]). CLI flags override
    the sidecar; the merged record is written back so feed/arena always read
    the same file.

    Dedup (no duplicate rows, per the 2026-11-16 pull-side lesson):
    same name already solved with the same flag -> rc=3 skip; an unsolved
    model round is skipped whenever any prior row for the name exists
    (the engine row already carries the failure profile).
    """
    d = getattr(args, "dir", None)
    if not d or not os.path.isdir(d):
        log("[error] account-model needs an existing challenge dir")
        return 2
    name = getattr(args, "name", None) or os.path.basename(
        os.path.abspath(d.rstrip("\\/")))
    sidecar_path = os.path.join(d, "_ctfpilot_model_result.json")
    sc = {}
    if os.path.exists(sidecar_path):
        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                sc = json.load(f)
            if not isinstance(sc, dict):
                sc = {}
        except (OSError, ValueError):
            log("[warn] sidecar unreadable, using CLI args only")
            sc = {}
    flag = getattr(args, "flag", None) or str(sc.get("flag") or "")
    solved = bool(flag) or bool(sc.get("solved"))
    if solved and not flag:
        log("[error] account-model: solved requires a flag")
        return 2
    t_val = getattr(args, "time_s", None)
    if t_val is None:
        t_val = sc.get("time_seconds")
    try:
        time_s = float(t_val or 0.0)
    except (TypeError, ValueError):
        time_s = 0.0
    c_val = getattr(args, "cost_tokens", None)
    if c_val is None:
        c_val = sc.get("cost_tokens")
    try:
        cost_tokens = int(c_val or 0)
    except (TypeError, ValueError):
        cost_tokens = 0
    category = getattr(args, "category", None) or sc.get("category") or "misc"
    summary = getattr(args, "summary", None) or sc.get("summary") or ""
    dead_ends = []
    for de in (getattr(args, "dead_end", None) or []):
        parts = str(de).split(":", 2)
        try:
            dt = float(parts[1]) if len(parts) > 1 else 0.0
        except ValueError:
            dt = 0.0
        dead_ends.append({"tool": parts[0] or "unknown", "time_seconds": dt,
                          "note": parts[2] if len(parts) > 2 else ""})
    for de in (sc.get("dead_ends") or []):
        if isinstance(de, dict) and de.get("tool"):
            try:
                dt = float(de.get("time_seconds") or 0.0)
            except (TypeError, ValueError):
                dt = 0.0
            try:
                dc = int(de.get("cost_tokens") or 0)
            except (TypeError, ValueError):
                dc = 0
            dead_ends.append({"tool": str(de.get("tool")), "time_seconds": dt,
                              "cost_tokens": dc,
                              "note": str(de.get("note") or "")})
    if not solved and not dead_ends:
        log("[error] account-model: nothing to account (no flag, no dead ends)")
        return 2
    store = get_stores(cfg)
    prior = store.prior_attempt(name)
    if solved and prior and prior.get("solved") and prior.get("flag") == flag:
        log("[account-model] skip: %s already solved+accounted with same flag" % name)
        return 3
    if not solved and prior:
        log("[account-model] skip: unsolved model round for %s, prior row exists" % name)
        return 3
    dead_txt = ", ".join("%s %gs" % (x["tool"], x["time_seconds"]) for x in dead_ends)
    row_summary = "model-channel %s in %.1fs%s" % (
        "solved" if solved else "unsolved", time_s,
        ("; dead-ends: " + dead_txt) if dead_txt else "")
    if summary:
        row_summary = "%s | %s" % (row_summary, summary)
    cid = store.new_challenge(name, category, os.path.abspath(d),
                              "model-channel", row_summary[:500])
    if solved:
        store.log_attempt(cid, "model", "model-channel",
                          "model-channel solved in %.1fs: %s" % (time_s, summary),
                          True, flag, cost_tokens=cost_tokens)
        store.finish_challenge(cid, True, flag)
        store.add_lesson(cid, category, "model-channel",
                         "worked on '%s' (model-channel solved, %.1fs)" % (name, time_s), "")
        store.bump_success(category, "model-channel")
    else:
        store.finish_challenge(cid, False, "")
    for de in dead_ends:
        store.log_attempt(cid, "model", de["tool"],
                          "dead-end after %.1fs: %s" % (de["time_seconds"], de["note"]),
                          False, error_class="semantic:dead-end",
                          cost_tokens=de.get("cost_tokens", 0))
    merged = {"name": name, "category": category, "solved": bool(solved),
              "flag": flag, "time_seconds": time_s, "cost_tokens": cost_tokens,
              "mode": "model-channel",
              "summary": summary, "dead_ends": dead_ends,
              "accounted_at": time.time()}
    try:
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    log("[account-model] name=%s solved=%s time=%.1f cost=%d dead_ends=%d" %
        (name, str(bool(solved)).lower(), time_s, cost_tokens, len(dead_ends)))
    return 0


def do_adjudicate(args, cfg):
    """Write a JSON-safe adjudication block into a challenge's result file.

    [evolve-20261117-05] The rsi-066 false-SOLVED retraction hand-rolled the
    block with a Python repr-style escape (\U0000FFFD), which json.loads
    rejects -- the whole evidence file became unreadable (RED: .temp/
    largernote-pre-evolve04-evidence/_ctfpilot_result.json). This
    subcommand is the single durable write path: every value goes through
    the stdlib JSON encoder (U+FFFD -> \ufffd), the pre-adjudication file
    is backed up byte-faithfully, the write is verified by re-parsing
    (rsi-071 finding: verify after commit), and an already-corrupt result
    file is refused, never silently munged.
    """
    d = getattr(args, "dir", None)
    if not d or not os.path.isdir(d):
        log("[error] adjudicate needs an existing challenge dir")
        return 2
    rpath = os.path.join(d, "_ctfpilot_result.json")
    if not os.path.exists(rpath):
        log("[error] adjudicate: %s not found (solve --json writes it)" % rpath)
        return 2
    try:
        with open(rpath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        log("[error] adjudicate: result file is not valid JSON (%s); repair "
            "it manually first -- this tool never touches a corrupt file "
            "(evolve-20261117-05)" % e)
        return 2
    if not isinstance(data, dict):
        log("[error] adjudicate: result file must hold a JSON object")
        return 2
    true_status = getattr(args, "true_status", None) or ""
    reason = getattr(args, "reason", None) or ""
    if not true_status or not reason:
        log("[error] adjudicate: --true-status and --reason are required")
        return 2
    set_solved = getattr(args, "set_solved", None)
    set_flag = getattr(args, "set_flag", None)
    if set_solved is not None and set_solved not in ("0", "1"):
        log("[error] adjudicate: --set-solved expects 0 or 1")
        return 2
    block = data.get("adjudication")
    if not isinstance(block, dict):
        block = {}
    # retraction semantics per the live precedent: flipping solved 1->0
    # retracts; --set-solved 1 is an explicit confirmation
    block["retracted"] = set_solved != "1"
    block["adjudicated_by"] = (getattr(args, "by", None) or
                               "ctfpilot adjudicate %s" %
                               time.strftime("%Y-%m-%d %H:%M:%S"))
    block["true_status"] = true_status
    block["reason"] = reason
    # originals: auto-captured from the pre-mutation top level unless
    # explicitly overridden (the rsi-066 precedent passed them explicitly)
    orig_solved = getattr(args, "original_solved", None)
    orig_flag = getattr(args, "original_flag", None)
    if set_solved is not None or set_flag is not None:
        if orig_solved is None:
            orig_solved = "1" if data.get("solved") else "0"
        if orig_flag is None:
            orig_flag = "" if data.get("flag") is None else str(data.get("flag"))
    if orig_solved is not None:
        try:
            block["original_solved"] = int(orig_solved)
        except (TypeError, ValueError):
            block["original_solved"] = 0
    if orig_flag is not None:
        block["original_flag"] = str(orig_flag)
    for kv in (getattr(args, "field", None) or []):
        if "=" not in str(kv):
            log("[error] adjudicate: --field expects key=value, got %r" % kv)
            return 2
        k, v = str(kv).split("=", 1)
        block[k] = v
    if set_solved is not None:
        data["solved"] = bool(int(set_solved))
    if set_flag is not None:
        data["flag"] = str(set_flag)
    data["adjudication"] = block
    # byte-faithful backup of the pre-adjudication state (the _ctfpilot*
    # prefix keeps it out of future bench pulls)
    bpath = os.path.join(d, "_ctfpilot_result.pre-adjudicate-%s.json" %
                         time.strftime("%Y%m%d-%H%M%S"))
    shutil.copyfile(rpath, bpath)
    # the single json.dumps write path: repr-style escapes can never appear
    with open(rpath, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=True, indent=2) + "\n")
    # verify after commit (rsi-071 finding (e): never claim success without
    # re-parsing the written bytes)
    try:
        with open(rpath, "r", encoding="utf-8") as f:
            json.load(f)
    except ValueError as e:
        log("[error] adjudicate: readback failed (%s); pre-adjudication "
            "backup kept at %s" % (e, bpath))
        return 2
    log("[adjudicate] %s true_status=%s backup=%s" %
        (rpath, true_status, os.path.basename(bpath)))
    return 0


def _trailing_adjudication_defect(summary):
    """Return (line, error) when the summary's LAST non-empty line opens an
    adjudication block that does not parse; (None, None) when the tail is
    safe to append to.

    [evolve-20261117-23] Parity with the dir face: do_adjudicate refuses a
    corrupt result file instead of munging it, but adjudicate-db used to
    append a fresh block after a truncated `{"adjudication"` fragment and
    report success -- the audit trail then stayed unparseable as a whole.
    Only a trailing line that *opens* a block is judged here; prose that
    merely mentions the marker, or an older (pretty-printed) block whose
    last line is `}`, is not a block opener and passes unchanged.
    """
    lines = [ln for ln in str(summary or "").splitlines() if ln.strip()]
    if not lines:
        return None, None
    last = lines[-1].strip()
    if not last.startswith('{"adjudication"'):
        return None, None
    try:
        parsed = json.loads(last)
    except ValueError as e:
        return last, "json parse failed: %s" % e
    if not isinstance(parsed, dict) or \
            not isinstance(parsed.get("adjudication"), dict):
        return last, "adjudication value is not a JSON object"
    return None, None


def do_adjudicate_db(args, cfg):
    """Adjudicate a production DB row that no result file can reach.

    [evolve-20261117-18] rsi-086 hand-executed the row-496 selftest-leak
    surgery (dir_path = workspace root, no _ctfpilot_result.json there --
    do_adjudicate's very first checks refuse it) by replicating the
    discipline manually. This subcommand makes that discipline a durable
    tool face: WAL checkpoint(TRUNCATE) -> byte-faithful whole-file backup
    next to the DB (memory/, persistent -- rsi-084 retention lesson) ->
    single-transaction UPDATE of exactly the one challenges row ->
    verify-after-commit re-reading the row and re-parsing the appended
    block (rsi-071 finding (e)). The summary block format is identical
    to the dir face and the rsi-086 hand surgery: a trailing
    {"adjudication": {...}} JSON object, original values inline, nothing
    deleted. Repeated adjudication appends a new block -- the audit
    trail only ever grows. [evolve-20261117-23] Before any checkpoint/
    backup/UPDATE the summary's last non-empty line is checked: if it
    OPENS an adjudication block but does not parse (or the value is not
    an object), the face refuses rc=2 exactly like the dir face refuses
    a corrupt result file -- a corrupt fragment is never buried under a
    fresh block. attempts/lessons rows are never touched
    (修复问题不删存量数据).
    """
    import sqlite3
    import hashlib
    row_id = getattr(args, "db_row", None)
    if row_id is None:
        log("[error] adjudicate-db needs --db-row <id>")
        return 2
    true_status = getattr(args, "true_status", None) or ""
    reason = getattr(args, "reason", None) or ""
    if not true_status or not reason:
        log("[error] adjudicate-db: --true-status and --reason are required")
        return 2
    set_solved = getattr(args, "set_solved", None)
    if set_solved is not None and set_solved not in ("0", "1"):
        log("[error] adjudicate-db: --set-solved expects 0 or 1")
        return 2
    db_path = os.environ.get("CTFPILOT_DB") or \
        path_under_project(cfg["memory"]["db_path"])
    if not os.path.exists(db_path):
        log("[error] adjudicate-db: db not found (%s)" % db_path)
        return 2
    # autocommit mode: the surgery below drives its own explicit single
    # transaction (BEGIN IMMEDIATE .. COMMIT) -- Python's implicit
    # transaction wrapping would nest with it
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        row = conn.execute(
            "SELECT id, solved, flag, summary FROM challenges WHERE id=?",
            (row_id,)).fetchone()
        if row is None:
            log("[error] adjudicate-db: no challenges row with id=%s" % row_id)
            return 2
        _rid, orig_solved, orig_flag, summary = row
        summary = "" if summary is None else str(summary)
        # (0) pre-op summary sanity [evolve-20261117-23]: the dir face
        #     refuses a corrupt result file; parity means refusing a
        #     summary whose trailing block is already unparseable BEFORE
        #     any checkpoint/backup/UPDATE -- never bury a corrupt audit
        #     fragment under a fresh valid block.
        _bad_tail, _bad_err = _trailing_adjudication_defect(summary)
        if _bad_tail is not None:
            log("[error] adjudicate-db: summary's trailing adjudication "
                "block is corrupt (%s); refusing before backup/UPDATE -- "
                "repair row %s's summary manually first"
                % (_bad_err, row_id))
            return 2
        # (1) WAL checkpoint(TRUNCATE): the backup must be a complete,
        #     self-consistent snapshot, not a main-file-plus-WAL pair
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        # (2) byte-faithful whole-file backup next to the DB, persistent
        #     (rsi-084: snapshots parked in .temp/ do not survive sweeps)
        bname = "trajectories.pre-adjuddb-%s-%s.sqlite" % (
            row_id, time.strftime("%Y%m%d-%H%M%S"))
        bpath = os.path.join(os.path.dirname(os.path.abspath(db_path)), bname)
        shutil.copyfile(db_path, bpath)
        with open(bpath, "rb") as f:
            bsha = hashlib.sha256(f.read()).hexdigest()
        # block content: same shape as the dir face; every value through
        # the stdlib JSON encoder (repr-style escapes can never appear --
        # the rsi-066 / evolve-20261117-05 lesson)
        block = {}
        for kv in (getattr(args, "field", None) or []):
            if "=" not in str(kv):
                log("[error] adjudicate-db: --field expects key=value, "
                    "got %r" % kv)
                return 2
            k, v = str(kv).split("=", 1)
            block[k] = v
        block["true_status"] = true_status
        block["reason"] = reason
        block["original_solved"] = int(orig_solved or 0)
        block["original_flag"] = "" if orig_flag is None else str(orig_flag)
        block["adjudicated_by"] = (getattr(args, "by", None) or
                                   "ctfpilot adjudicate-db %s" %
                                   time.strftime("%Y-%m-%d %H:%M:%S"))
        # compact one-line encoding: byte-format matches the rsi-086 hand
        # surgery block already in production (verify rfinds this shape)
        suffix = json.dumps({"adjudication": block}, ensure_ascii=False)
        new_summary = (summary.rstrip() + "\n" + suffix) if summary.strip() \
            else suffix
        # (3) single-transaction UPDATE of exactly this row
        conn.execute("BEGIN IMMEDIATE")
        if set_solved is not None:
            conn.execute("UPDATE challenges SET solved=? WHERE id=?",
                         (int(set_solved), row_id))
        if getattr(args, "set_flag", None) is not None:
            conn.execute("UPDATE challenges SET flag=? WHERE id=?",
                         (str(args.set_flag), row_id))
        conn.execute("UPDATE challenges SET summary=? WHERE id=?",
                     (new_summary, row_id))
        conn.execute("COMMIT")
        # (4) verify after commit: re-read the row, re-parse the block
        row2 = conn.execute(
            "SELECT solved, flag, summary FROM challenges WHERE id=?",
            (row_id,)).fetchone()
        if row2 is None:
            log("[error] adjudicate-db: row %s vanished after commit; "
                "pre-surgery backup kept at %s" % (row_id, bpath))
            return 2
        if set_solved is not None and int(row2[0]) != int(set_solved):
            log("[error] adjudicate-db: verify failed (solved); backup=%s"
                % bpath)
            return 2
        if getattr(args, "set_flag", None) is not None and \
                str(row2[1] or "") != str(args.set_flag):
            log("[error] adjudicate-db: verify failed (flag); backup=%s"
                % bpath)
            return 2
        tail = str(row2[2] or "")
        mpos = tail.rfind('{"adjudication"')
        if mpos < 0:
            log("[error] adjudicate-db: verify failed (no block); "
                "backup=%s" % bpath)
            return 2
        try:
            parsed = json.loads(tail[mpos:])
            if parsed.get("adjudication", {}).get("true_status") \
                    != true_status:
                raise ValueError("true_status mismatch")
        except ValueError as e:
            log("[error] adjudicate-db: verify failed (block parse: %s); "
                "backup=%s" % (e, bpath))
            return 2
        log("[adjudicate-db] row=%s true_status=%s solved=%s flag_set=%s "
            "backup=%s sha256=%s" %
            (row_id, true_status, row2[0],
             "yes" if getattr(args, "set_flag", None) is not None else "no",
             bname, bsha))
        return 0
    finally:
        conn.close()


def do_challenge_detail(args, cfg):
    """One challenge's full ledger record: metadata + attempt timeline + its lessons.
    JSON to stdout so the sidebar detail page can render it verbatim (no logs mixed in)."""
    import sqlite3
    import datetime as _dt
    db_path = os.environ.get("CTFPILOT_DB") or path_under_project(cfg["memory"]["db_path"])
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    row = None
    if getattr(args, "id", None):
        row = conn.execute("SELECT * FROM challenges WHERE id=?",
                           (int(args.id),)).fetchone()
    if row is None and getattr(args, "name", None):
        cand = conn.execute("SELECT * FROM challenges WHERE name=? ORDER BY id DESC",
                            (str(args.name),)).fetchall()
        if cand:
            row = cand[0]
    if row is None:
        log("[error] challenge not found (pass --id N or --name NAME)")
        return 2

    cid = row["id"]
    att_rows = conn.execute("""
        SELECT phase, detector, detail, success, flag, error_class,
               datetime(ts,'unixepoch','localtime') AS ts_str
        FROM attempts WHERE challenge_id=? ORDER BY ts ASC""",
                            (cid,)).fetchall()
    les_rows = conn.execute("""
        SELECT technique, when_to_use, snippet, tags,
               datetime(ts,'unixepoch','localtime') AS ts_str
        FROM lessons WHERE challenge_id=? ORDER BY ts ASC""",
                             (cid,)).fetchall()

    ca = row["created_at"]
    created_str = ""
    try:
        created_str = _dt.datetime.fromtimestamp(float(ca)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        created_str = str(ca) if ca else ""

    record = {
        "id": cid,
        "name": row["name"],
        "category": row["category"],
        "mode": row["mode"] or "",
        "solved": bool(row["solved"]),
        "flag": row["flag"] or "",
        "summary": row["summary"] or "",
        "created_at": created_str,
        "attempts": [
            {"phase": r["phase"] or "", "detector": r["detector"] or "",
             "detail": r["detail"] or "", "success": bool(r["success"]),
             "flag": r["flag"] or "", "error_class": r["error_class"] or "",
             "time": r["ts_str"] or ""}
            for r in att_rows
        ],
        "lessons": [
            {"tech": r["technique"] or "", "src": r["when_to_use"] or "",
             "snippet": (r["snippet"] or "")[:300], "tags": r["tags"] or "",
             "time": r["ts_str"] or ""}
            for r in les_rows
        ],
        "attempt_count": len(att_rows),
    }
    sys.stdout.write(json.dumps(record, ensure_ascii=False))
    return 0


def do_eval_suite(args, cfg):
    """Full-metrics regression gate (精进评价指标本身).

    Runs the bundled smoke set on an ISOLATED db (never production memory),
    snapshots every metric dimension, compares against the saved baseline,
    appends the snapshot to the trend log, and prints PASS/FAIL per check.
    Exit 0 = no regression; 1 = regressed."""
    tmp_db = os.path.join(PROJECT_ROOT, ".temp", "evalsuite.sqlite")
    os.makedirs(os.path.dirname(tmp_db), exist_ok=True)
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    os.environ["CTFPILOT_DB"] = tmp_db
    log("[eval-suite] isolated memory db: %s" % tmp_db)
    base = os.path.join(PROJECT_ROOT, "tests", "challs")
    results = []
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            continue
        log("==== eval-suite: %s ====" % name)
        rc = do_solve(_NS(dir=d, mode="auto", brain="mock", name=name,
                          flag_regex=None, write_trace=False), cfg)
        results.append((name, rc == 0))
    ok = sum(1 for _n, s in results if s)
    smoke = {"total": len(results), "pass_rate": round(ok / max(len(results), 1), 4)}
    log("[eval-suite] smoke set: %d/%d solved" % (ok, len(results)))

    # retrieval metrics from the PRODUCTION knowledge base (held-out eval)
    kstats_path = path_under_project(
        os.path.join("skills", "knowledge", "stats.json"))
    knowledge = {}
    try:
        with open(kstats_path, "r", encoding="utf-8") as f:
            knowledge = json.load(f)
    except (OSError, ValueError):
        pass
    retrieval = None
    try:
        from knowledge import load_cases, eval_retrieval
        cases = load_cases(path_under_project("skills"))
        wdir = knowledge.get("writeups_dir") or ""
        if cases and wdir and os.path.isdir(wdir):
            res = eval_retrieval(cases, wdir)
            retrieval = {"top1": res["signal"]["top1"],
                         "top3": res["signal"]["top3"],
                         "mrr": res["signal"]["mrr"]}
    except Exception as e:
        log("[eval-suite] retrieval eval skipped: %s" % e)

    snap = M.compute_metrics(tmp_db, knowledge=knowledge, smoke=smoke)
    if retrieval:
        snap["retrieval"] = retrieval

    # snapshot the production store too: knowledge floor uses production data
    prod_db = os.environ.get("CTFPILOT_DB") or path_under_project(
        cfg["memory"]["db_path"])
    snap_prod = M.compute_metrics(prod_db, knowledge=knowledge)

    base_path = path_under_project(
        os.path.join("skills", "knowledge", "metrics-baseline.json"))
    verdict = {"passed": True, "checks": []}
    if os.path.exists(base_path):
        with open(base_path, "r", encoding="utf-8") as f:
            bl = json.load(f)
        verdict = M.gate_metrics(snap, bl)
        log("baseline : %s" % time.strftime("%Y-%m-%d %H:%M:%S",
                                            time.localtime(bl.get("_saved_ts") or 0)) if bl.get("_saved_ts") else "")
    else:
        log("(no metrics baseline yet -- use --save-baseline to record one)")
    for c in verdict["checks"]:
        mark = "ok" if c["ok"] else "REGRESSION"
        log("  %-22s base=%s now=%s delta=%s %s" %
            (c["metric"], c.get("base"), c.get("now"), c.get("delta"),
             mark if not c["ok"] else ""))
    if args.save_baseline:
        out = dict(snap)
        out["_saved_ts"] = int(time.time())
        out["smoke_total"] = smoke["total"]
        with open(base_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        log("[eval-suite] baseline saved: %s" % base_path)
    else:
        hist = path_under_project(
            os.path.join("skills", "knowledge", "metrics-history.jsonl"))
        M.snapshot_history_append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                   "smoke": smoke, "snapshot": snap,
                                   "verdict": verdict}, hist)
    log("[eval-suite] RESULT: %s" % ("PASS" if verdict["passed"] else
        "FAIL (gate regressed)"))
    return 0 if verdict["passed"] else 1


def do_rsi(args, cfg):
    """RSI self-improvement loop. Engine proposes; human + eval-suite admit."""
    action = getattr(args, "action", None) or "cycle"
    if action == "cycle":
        return 0 if R.cycle(cfg=cfg) is not None else 1
    if action == "list":
        rows = R.list_proposals(status=getattr(args, "status", None),
                                limit=int(getattr(args, "limit", 50) or 50))
        log("== rsi proposals (%d) ==" % len(rows))
        for p in rows:
            log("- [%s] %s | %s -> %s | %s" %
                (p.get("id"), p.get("type"), p.get("target"),
                 p.get("status"), str(p.get("why"))[:120]))
        return 0
    pid = getattr(args, "pid", None)
    if not pid:
        log("[error] rsi %s needs a proposal id (see: rsi list)" % action)
        return 2
    note = getattr(args, "note", "") or ""
    # [evolve-20261117-10] adoption now carries chain payload explicitly:
    # --chain-json holds the winner/rollback/evidence fields for the
    # version-chain line; transition() commits ledger+chain together.
    chain_fields = None
    cj = getattr(args, "chain_json", None)
    if cj:
        try:
            chain_fields = json.loads(cj)
        except ValueError:
            log("[error] --chain-json is not valid JSON")
            return 2
        if not isinstance(chain_fields, dict):
            log("[error] --chain-json must be a JSON object")
            return 2
    # CLI verbs are approve/reject/adopt; the ledger state machine stores
    # past-tense statuses (approved/rejected/adopted).  Without this map the
    # human decision path always raised ValueError.
    transition_action = {"approve": "approved", "reject": "rejected",
                         "adopt": "adopted"}.get(action, action)
    # [evolve-20261117-16] resolve snapshot inputs once, absolute; the
    # transition() guard raises BEFORE any ledger/chain write when the
    # rollback text claims a sha256 that matches nothing on disk.
    cand = getattr(args, "candidate_files_cli", None) or None
    inc = getattr(args, "incumbent_files_cli", None) or None
    cand = [os.path.abspath(x) for x in cand] if cand else None
    inc = [os.path.abspath(x) for x in inc] if inc else None
    hit = R.transition(pid, transition_action, note=note,
                       chain_fields=chain_fields,
                       candidate_files=cand, incumbent_files=inc)
    if hit is None:
        log("[error] no such proposal: %s" % pid)
        return 2
    if transition_action == "adopted":
        ce = hit.get("chain_entry")
        if ce:
            log("[rsi] ADOPTED %s -> version-chain entry %d (%s)" %
                (pid, ce, R.chain_path()))
        else:
            log("[rsi] ADOPTED %s (chain line already present; "
                "ledger history only)" % pid)
        if cand or inc:
            log("      snapshots: %d candidate / %d incumbent under %s" %
                (len(cand or []), len(inc or []),
                 os.path.join(R.SNAPSHOTS_REL, pid)))
        log("      following eval-suite run is MANDATORY:")
        log("      python -X utf8 ctfpilot.py eval-suite")
    else:
        log("[rsi] %s: %s (%s)" % (transition_action, pid, hit.get("target")))
    return 0


def do_arena(args, cfg):
    """Arena practice bank. Training ledger stays separate from production."""
    action = getattr(args, "action", None) or "stats"
    if action == "register":
        if not (args.name and args.category):
            log("[error] arena register needs --name and --category")
            return 2
        reg = A.register(args.name, args.category, args.source or "manual",
                         tier=args.tier or "normal")
        log("[arena] registered '%s' (%s, tier=%s); items=%d" %
            (args.name, args.category, args.tier or "normal",
             len(reg["items"])))
        return 0
    if action == "pull":
        # reuse bench-pull mechanics, then register the pulled challenge
        rc = do_bench_pull(args, cfg)
        if rc != 0:
            return rc
        if not args.dest:
            log("[error] arena pull needs --dest")
            return 2
        dest = os.path.abspath(args.dest)
        pulled = sorted((d for d in os.listdir(dest)
                         if os.path.isdir(os.path.join(dest, d))),
                        key=lambda n: os.path.getmtime(
                            os.path.join(dest, n)), reverse=True)
        if not pulled:
            log("[arena] nothing pulled")
            return 3
        name = pulled[0]
        routing = route(os.path.join(dest, name))
        A.register(name, routing["category"], args.source or "",
                   tier="normal")
        log("[arena] pulled+registered '%s' (triage=%s)" %
            (name, routing["category"]))
        return 0
    if action == "record":
        if not (args.name and args.category):
            log("[error] arena record needs --name and --category")
            return 2
        # [redrill] 记分守门: campaign 活跃期间, 考批题禁止直接 arena
        # record（伪造成绩通道物理封锁; 记分只能走 redrill record 的
        # 引擎证据校验路径）。
        try:
            import redrill as _rd
            _rst = _rd.active_state()
            if _rst and args.name in _redrill_exam_names(_rst):
                log("[arena][REJECTED] '%s' is in the active redrill exam "
                    "set; credit it only via `redrill record --slot ...` "
                    "(engine-verify evidence required)" % args.name)
                return 2
        except ImportError:
            pass
        if args.fail:
            solved, fast = False, False
        else:
            solved, fast = True, bool(args.fast)
        hit = A.record_result(args.name, args.category, solved,
                              tier=args.tier or "normal", fast_solved=fast,
                              time_s=getattr(args, "time_s", None),
                              cost_tokens=getattr(args, "cost_tokens", None))
        A.recompute_tiers()
        cost_txt = (" cost=%d" % (args.cost_tokens or 0)) \
            if getattr(args, "cost_tokens", None) is not None else ""
        log("[arena] '%s': solves=%d fails=%d tier=%s%s" %
            (hit["name"], hit["solves"], hit["fails"], hit["tier"], cost_txt))
        return 0
    lb = A.recompute_tiers()
    board = A.leaderboard()
    log("== arena leaderboard (practice only; production untouched) ==")
    ov = board["overall"]
    ax = ""
    if ov.get("mean_time_s") is not None:
        ax += " clock=%.1fs" % ov["mean_time_s"]
    if ov.get("mean_cost_tokens") is not None:
        ax += " cost=%.0ftok" % ov["mean_cost_tokens"]
    log("  overall : %d/%d solved (%.1f%%)%s" %
        (ov["solved"], ov["attempted"], ov["win_rate"], ax))
    for c in board["categories"]:
        cx = ""
        if c.get("mean_time_s") is not None:
            cx += " clock=%.1fs" % c["mean_time_s"]
        if c.get("mean_cost_tokens") is not None:
            cx += " cost=%.0ftok" % c["mean_cost_tokens"]
        log("  %-10s %d/%d solved (%.1f%%) tiers easy=%d normal=%d hard=%d%s" %
            (c["category"], c["solved"], c["attempted"], c["win_rate"],
             c["by_tier"]["easy"], c["by_tier"]["normal"], c["by_tier"]["hard"], cx))
    log("[arena] tiers recomputed: %d changed; items=%d" %
        (lb["changed"], board["registered"]))
    return 0


def do_research(args, cfg):
    """Research queue: human-guided study topics with evidence-pointer gating."""
    action = getattr(args, "action", None) or "list"
    if action == "add":
        topic = args.topic or ""
        if not topic:
            log("[error] research add needs a TOPIC")
            return 2
        rec = RS.add_topic(topic, category=args.category or "misc",
                           source=args.source or "manual")
        log("[research] queued '%s' (%s); draft file already written by "
            "add_topic -- fill it, then attach evidence pointers."
            % (topic, rec["category"]))
        return 0
    if action == "evidence":
        if not (args.topic and args.pointer):
            log("[error] research evidence needs TOPIC and --pointer")
            return 2
        RS.register_evidence(args.topic, args.pointer,
                             getattr(args, "kind", "local-file"))
        rows = RS.list_topics(status="ready-ingest")
        log("[research] topics ready for ingest: %d" % len(rows))
        return 0
    if action == "ingest":
        if not args.topic:
            log("[error] research ingest needs TOPIC")
            return 2
        hit = RS.ingest_route(args.topic)
        return 0 if hit is not None else 2
    rows = RS.list_topics(status=getattr(args, "status", None),
                          limit=int(getattr(args, "limit", 50) or 50))
    log("== research queue (%d topics) ==" % len(rows))
    for t in rows:
        ev = len(t.get("evidence") or [])
        log("- [%s] %s | %s | evidence %d/2 | %s" %
            (t.get("id"), t.get("topic"), t.get("category"), ev,
             t.get("status")))
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
    # --- module selftests (isolated memory + scratch ledger dirs, never production) ---
    mod_results = []
    scratch = os.path.join(PROJECT_ROOT, ".temp", "selftest_modules")
    import shutil
    if os.path.isdir(scratch):
        shutil.rmtree(scratch)
    os.makedirs(scratch, exist_ok=True)
    # [evolve-15] synthetic-source DB guard: fixture dirs must route to the
    # isolated store when no explicit CTFPILOT_DB is set; an explicit env
    # (like the selftest one) always wins; production dirs never trigger it.
    try:
        saved_db = os.environ.get("CTFPILOT_DB")
        fixture = os.path.join(PROJECT_ROOT, "tests", "challs", "01_b64chain")
        arena_fixture = os.path.join(os.path.dirname(PROJECT_ROOT),
                                     "arena", "tasks", "x")
        prod_dir = os.path.join(PROJECT_ROOT, "writeups")
        # [evolve-20261117-45 / rsi-117] finally-restore (evolve-32 shape):
        # the env restore used to sit on the happy path only. On engine copies
        # rooted under .temp the production-dir face trips the ".temp" marker
        # inside _synthetic_db_guard, the assert fires, and the except branch
        # recorded the failure WITHOUT restoring CTFPILOT_DB -- the env stayed
        # popped or pointed at the PERSISTENT <db>.holdout store, so every
        # later selftest block's do_solve wrote into it (one duplicate solved
        # row per run; live .holdout at discovery: 01_b64chain x5 among 6 dup
        # groups). Restore now happens on BOTH paths; the block verdict itself
        # is unchanged -- a failing face still fails the block.
        try:
            for case, d, want_isolated in (
                    ("tests/challs fixture", fixture, True),
                    ("arena/tasks fixture", arena_fixture, True),
                    ("project .temp replay fixture",
                     os.path.join(PROJECT_ROOT, ".temp", "rsi-102-replay", "x"),
                     True),
                    ("workspace .temp replay fixture",
                     os.path.join(os.path.dirname(PROJECT_ROOT), ".temp",
                                  "rsi-102-replay", "x"),
                     True),
                    ("production dir", prod_dir, False)):
                os.environ.pop("CTFPILOT_DB", None)
                _synthetic_db_guard(cfg, d)
                got = os.environ.get("CTFPILOT_DB", "")
                if want_isolated:
                    assert got.endswith(".holdout"), \
                        "%s must route to the isolated .holdout store, got %r" % (case, got)
                else:
                    assert got == "", "%s must not trigger the guard, got %r" % (case, got)
        finally:
            os.environ["CTFPILOT_DB"] = saved_db or tmp_db
        _synthetic_db_guard(cfg, fixture)
        assert os.environ.get("CTFPILOT_DB") == (saved_db or tmp_db), \
            "explicit CTFPILOT_DB must win over the synthetic guard"
        mod_results.append(True)
    except Exception:
        log("selftest evolve15-guard failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-03] garbage-flag credibility gate: wrong-key XOR
        # output must not credit a U+FFFD / control-char "flag" (live:
        # large_note fake SOLVED 401.5s, RED replay 401.6s) while real
        # printable flags keep passing (str + bytes paths).
        from fastpath.detectors import _flag_text
        gc = FlagChecker(compiled_flag_patterns(cfg))
        assert _flag_text(gc, "FLAG{no_dLAG\U0000FFFDmo_WLAG}") is None, \
            "U+FFFD garbage flag must be gated"
        assert _flag_text(gc, "FLAG{no_dLAG\x01\x02mo_WLAG}") is None, \
            "control-char garbage flag must be gated"
        assert _flag_text(gc, "FLAG{no_dLAGFLAG=no_FLAG}") == \
            "FLAG{no_dLAGFLAG=no_FLAG}", "real printable flag must pass"
        assert _flag_text(gc, b"note flag{real_ascii_key_1} end") == \
            "flag{real_ascii_key_1}", "bytes path real flag must pass"
        mod_results.append(True)
    except Exception:
        log("selftest evolve03-gate failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-04] fastpath brute-stage time budget: config's
        # fastpath_time_limit_s (previously defined but never read anywhere)
        # now bounds the O(files x keys) stages (xor-single / mz / xor-token /
        # png). A loose budget must still solve through xor-single (normal
        # 1-3-file challenges keep their full search space); an exhausted
        # budget must skip the brute stages with a visible `budget` attempt
        # and must not credit a brute flag.
        from fastpath.detectors import run_fast_path
        bb = FlagChecker(compiled_flag_patterns(cfg))
        bdir = os.path.join(scratch, "evolve04-budget")
        os.makedirs(bdir, exist_ok=True)
        plain = b"pad flag{budget_gate_ok_1} tail"
        crypt = bytes(b ^ 0x42 for b in plain)
        with open(os.path.join(bdir, "payload.bin"), "wb") as f:
            f.write(crypt)
        bfiles = [{"path": "payload.bin",
                   "abs": os.path.join(bdir, "payload.bin"),
                   "size": len(crypt), "magic": "elf"}]
        att = []
        got = run_fast_path(bdir, bfiles, bb, att, time_budget_s=30)
        assert got == "flag{budget_gate_ok_1}", \
            "loose budget must still solve via xor-single, got %r" % got
        assert not any(a.detector == "budget" for a in att), \
            "loose budget must not leave budget-cut attempts"
        att2 = []
        got2 = run_fast_path(bdir, bfiles, bb, att2, time_budget_s=0.000001)
        assert got2 is None, \
            "exhausted budget must not credit a brute flag, got %r" % got2
        assert any(a.detector == "budget" for a in att2), \
            "exhausted budget must leave a visible budget attempt"
        mod_results.append(True)
    except Exception:
        log("selftest evolve04-budget failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        import rsi as R
        import arena as AR
        import research as RS
        store = TrajStore(tmp_db)
        # rsi cycle end-to-end on isolated store (empty signal set is fine,
        # a crash is not); ledger goes under scratch
        R.cycle(cfg=cfg, store=store, log=lambda *_a: None, root=scratch)
        assert len(R.list_proposals(limit=10, root=scratch)) >= 0, \
            "rsi list_proposals crashed"
        # approve -> adopt transitions keep history
        rows = R.list_proposals(root=scratch)
        if rows:
            t = R.transition(rows[0]["id"], "approved", note="selftest",
                             root=scratch)
            assert t["status"] == "approved" and t["history"], \
                "rsi transition lost history"
        # skill-card technique-gap scan against the REAL engine cards
        # (read-only); must yield well-formed gaps and exclude covered ones
        gaps = R.skill_card_gaps()
        assert isinstance(gaps, list) and all(
            g.get("category") and g.get("bullet") and g.get("path")
            for g in gaps), "skill_card_gaps malformed"
        assert R._covered_by_detector("caesar 全扫") is True, \
            "covered bullet must be excluded from gaps"
        assert any(g["bullet"] for g in gaps), \
            "expected some card bullets with no fast-path detector"
        props = R.propose_signals({"card_gaps": gaps[:5]}, k=5, seq=0)
        assert props and all(p["type"] == "detector" for p in props), \
            "card gaps must become detector proposals"
        assert R.skill_card_gaps(root=scratch) == [], \
            "gap scan must be silent without skill cards (scratch)"
        mod_results.append(True)
    except Exception:
        log("selftest rsi failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        AR.register("smoke-arena-easy", "misc", "manual", tier="easy", root=scratch)
        AR.record_result("smoke-arena-easy", "misc", True, "normal",
                         fast_solved=True, root=scratch)
        AR.record_result("smoke-arena-easy", "misc", False, "hard",
                         fast_solved=False, root=scratch)
        # cost axis (evolve-20261116-05): ledger rows carry clock + tokens;
        # leaderboard means must reflect them (and skip rows without)
        AR.record_result("smoke-arena-easy", "misc", True, "normal",
                         fast_solved=False, root=scratch,
                         time_s=12.5, cost_tokens=800)
        AR.recompute_tiers(root=scratch)
        lb = AR.leaderboard(root=scratch)
        assert lb["overall"]["attempted"] >= 3, "arena leaderboard empty"
        assert lb["categories"][0]["win_rate"] >= 0, "arena win rate broken"
        assert lb["overall"]["mean_time_s"] == 12.5, "practice clock mean lost"
        assert lb["overall"]["mean_cost_tokens"] == 800, "cost mean lost"
        mod_results.append(True)
    except Exception:
        log("selftest arena failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        RS.add_topic("selftest research topic", category="crypto", source="manual",
                     root=scratch)
        rows = RS.list_topics(status="queued", root=scratch)
        assert any(t["topic"] == "selftest research topic" for t in rows), \
            "rsch topic missing from queue"
        draft = RS.draft_path_for("selftest research topic", root=scratch)
        assert draft is not None and os.path.exists(draft), "research draft missing"
        assert RS.draft_parser_ok(draft), "template draft must parse against the learner grammar"
        with open(draft, "r", encoding="utf-8") as f:
            parsed_fields = __import__(
                "learn.writeup_learner", fromlist=["parse_writeup"]
            ).parse_writeup(f.read())["fields"]
        assert parsed_fields.get("题型") == "crypto", \
            "分类 alias must map to the learner's 题型 field"
        RS.register_evidence("selftest research topic", "/tmp/x.md", "local-file",
                              root=scratch)
        block = RS.ingest_route("selftest research topic", root=scratch)  # 1 ptr -> blocked
        assert block is None, "ingest must be blocked below 2 evidence pointers"
        RS.register_evidence("selftest research topic", "https://example.org/y", "url",
                             root=scratch)
        hit = RS.ingest_route("selftest research topic", root=scratch)  # now 2 -> routed
        assert hit is not None, "ingest must route once draft+evidence are ready"
        # parser gate: a structurally broken draft must block ingest
        broken = os.path.join(scratch, "broken-draft.md")
        with open(broken, "w", encoding="utf-8") as f:
            f.write("# broken\n\nnothing but prose here\n")
        assert not RS.draft_parser_ok(broken), "broken draft must fail the parser gate"
        mod_results.append(True)
    except Exception:
        log("selftest research failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        snap = M.compute_metrics(tmp_db, knowledge={"cases": 10, "with_signals": 7})
        assert snap["overall"]["total"] >= 0, "metrics overall broken"
        assert snap["efficiency"]["fast_path_share"] >= 0.0, \
            "fast-path share negative"
        for _k in ("cost_tokens_total", "cost_per_solve", "attempts_per_solve",
                   "zero_cost_share"):
            assert _k in snap["efficiency"], "cost axis metric missing: " + _k
        v = M.gate_metrics(snap, snap)  # same-values baseline passes trivially
        assert v["passed"] is True, "self-gate must pass"
        v2 = M.gate_metrics({"capability": {"pass_rate": 0.9}},
                            {"capability": {"smoke_pass_rate": 1.0}})  # regression -> fail
        assert v2["passed"] is False, "regression must be detected"
        v3 = M.gate_metrics({"retrieval": {"top1": 0.70, "top3": 0.95}},
                            {"retrieval": {"top1": 0.65, "top3": 0.90}})  # improvement -> pass
        assert v3["passed"] is True, "improvement must pass"
        # --- cost ceiling (evolve-20261116-05); note gate_metrics(current, baseline) ---
        v4 = M.gate_metrics({"efficiency": {"cost_per_solve": 160.0}},
                            {"efficiency": {"cost_per_solve": 100.0}})
        assert v4["passed"] is False, "cost blow-up must fail the gate"
        v5 = M.gate_metrics({"efficiency": {"cost_per_solve": 105.0}},
                            {"efficiency": {"cost_per_solve": 100.0}})
        assert v5["passed"] is True, "bounded cost rise must pass"
        v6 = M.gate_metrics({"efficiency": {"cost_per_solve": 999.0}},
                            {"efficiency": {}})
        assert v6["passed"] is True, "missing baseline cost must skip"
        v7 = M.gate_metrics({"efficiency": {"cost_per_solve": 10.0}},
                            {"efficiency": {"cost_per_solve": 0.0}})
        assert v7["passed"] is False, "zero-cost incumbent must stay zero-cost"
        # --- dual-axis admission (time rule unchanged, cost only rejects) ---
        a1 = M.admit_candidate({"time": 100, "cost": 100},
                               {"time": 92, "cost": 112})
        assert a1["admit"] is True, "8% time win must admit <=18% cost rise"
        a2 = M.admit_candidate({"time": 100, "cost": 100},
                               {"time": 92, "cost": 160})
        assert a2["admit"] is False, "8% time win must NOT admit 60% cost rise"
        a3 = M.admit_candidate({"time": 100, "cost": 100},
                               {"time": 105, "cost": 50})
        assert a3["admit"] is False, "slower candidate must never be rescued"
        a4 = M.admit_candidate({"time": 100}, {"time": 95})
        assert a4["admit"] is True, "cost unknown -> time rule decides alone"
        a5 = M.admit_candidate({"time": 100, "cost": 0},
                               {"time": 95, "cost": 1})
        assert a5["admit"] is False, "zero-cost incumbent + token spend fails"
        a6 = M.admit_candidate({"adversarial_time": 100, "cost_per_solve": 100},
                               {"adversarial_time": 98, "cost_per_solve": 100})
        assert a6["admit"] is False, "2% win is below the 3% threshold"
        # --- trajstore cost axis: column migration + cost_stats ---
        cost_db = os.path.join(scratch, "cost.sqlite")
        cs = TrajStore(cost_db)
        cid = cs.new_challenge("cost-probe", "crypto", scratch, "solve",
                               "cost selftest")
        cs.log_attempt(cid, "model", "model-channel", "model round", True,
                       "flag{c}", cost_tokens=1200)
        cs.log_attempt(cid, "model", "z3", "dead-end", False,
                       error_class="semantic:dead-end", cost_tokens=300)
        cs.finish_challenge(cid, True, "flag{c}")
        cid2 = cs.new_challenge("cost-zero", "misc", scratch, "solve", "zero")
        cs.log_attempt(cid2, "fast", "probe", "native solve", True, "flag{z}")
        cs.finish_challenge(cid2, True, "flag{z}")
        st = cs.cost_stats()
        assert st["cost_tokens_total"] == 1500, "token total wrong"
        assert st["cost_per_solve"] == 750.0, "cost per solve wrong"
        assert st["attempts_per_solve"] == 1.5, "attempts per solve wrong"
        assert st["zero_cost_solved"] == 1, "zero-cost native share wrong"
        cs2 = TrajStore(cost_db)  # reopen: migration must be idempotent
        assert cs2.cost_stats()["cost_tokens_total"] == 1500, "migration lost cost"
        mod_results.append(True)
    except Exception:
        log("selftest metrics failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # account-model: sidecar ingest -> model-channel row + dead-end
        # attempts; duplicate accounting (same flag) must skip with rc=3
        am_dir = os.path.join(scratch, "am-chall")
        os.makedirs(am_dir, exist_ok=True)
        with open(os.path.join(am_dir, "_ctfpilot_model_result.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"solved": True, "flag": "flag{am_test}",
                       "time_seconds": 12.5, "cost_tokens": 2000,
                       "category": "crypto",
                       "summary": "selftest sidecar",
                       "dead_ends": [{"tool": "z3", "time_seconds": 5.0,
                                      "cost_tokens": 500,
                                      "note": "timeout"}]}, f)
        rc_am = do_account_model(_NS(dir=am_dir, name="", flag="", category="",
                                     time_s=None, cost_tokens=None, summary="",
                                     dead_end=None), cfg)
        assert rc_am == 0, "account-model must ingest a valid sidecar"
        am_store = TrajStore(tmp_db)
        row = am_store.conn.execute(
            "SELECT mode, solved, flag, summary FROM challenges "
            "WHERE name='am-chall' ORDER BY id DESC").fetchone()
        assert row and row[0] == "model-channel" and row[1] == 1 \
            and row[2] == "flag{am_test}", "model-channel row missing/malformed"
        assert "12.5" in (row[3] or ""), "time must be visible in the row summary"
        am_cost = am_store.conn.execute(
            "SELECT phase, cost_tokens FROM attempts WHERE challenge_id=("
            "SELECT id FROM challenges WHERE name='am-chall' ORDER BY id DESC "
            "LIMIT 1) ORDER BY id").fetchall()
        assert len(am_cost) == 2 and am_cost[0][1] == 2000 \
            and am_cost[1][1] == 500, \
            "sidecar cost_tokens must land on the attempt rows (solve, dead-end)"
        dead = am_store.conn.execute(
            "SELECT COUNT(*) FROM attempts WHERE challenge_id=("
            "SELECT id FROM challenges WHERE name='am-chall' ORDER BY id DESC "
            "LIMIT 1) AND error_class='semantic:dead-end'").fetchone()[0]
        assert dead == 1, "dead-end attempt missing"
        rc_dup = do_account_model(_NS(dir=am_dir, name="", flag="", category="",
                                      time_s=None, summary="", dead_end=None), cfg)
        assert rc_dup == 3, "duplicate accounting must be skipped (rc=3)"
        n_rows = am_store.conn.execute(
            "SELECT COUNT(*) FROM challenges WHERE name='am-chall'").fetchone()[0]
        assert n_rows == 1, "dedup must not append rows"
        # --- evolve-20261117-46 / rsi-118 (supersedes the
        # evolve-20261116-12 sidecar exception): engine-authored
        # artifacts are not challenge material on either side. The
        # solve input side excludes every _ctfpilot* file (router
        # inventory; the printable-strings-scan read gap is closed in
        # deep/planner.py this round), so a scanner can no longer echo
        # the sidecar at all (dfjk-Misc fake-SOLVED shape now
        # structurally impossible). flag_in_raw therefore exempts the
        # sidecar symmetrically with the other _ctfpilot* byproducts:
        # the exit-side compare face only ever fired on
        # legitimate-coincidence retracts (live finding B: 2024-OMCTF
        # pdf-text CID solve retracted against the sidecar while the
        # flag bytes sat verbatim-absent inside a compressed PDF
        # stream). ---
        from core.flagcheck import flag_in_raw
        hit_sc, _src_sc = flag_in_raw(am_dir, "flag{am_test}")
        assert not hit_sc, \
            "sidecar must be exempt: engine-authored, not challenge " \
            "material (rsi-118)"
        with open(os.path.join(am_dir, "_ctfpilot_result.json"), "w",
                  encoding="utf-8") as f:
            f.write('{"solved": true, "flag": "flag{engine_ok}"}')
        with open(os.path.join(am_dir, "_ctfpilot_trace.json"), "w",
                  encoding="utf-8") as f:
            f.write('{"flag": "flag{engine_ok}"}')
        with open(os.path.join(am_dir, "_ctfpilot_step.py"), "w",
                  encoding="utf-8") as f:
            f.write("print('flag{engine_ok}')")
        hit_ok, _src_ok = flag_in_raw(am_dir, "flag{engine_ok}")
        assert not hit_ok, \
            "engine result/trace/step byproducts must stay exempt"
        mod_results.append(True)
    except Exception:
        log("selftest account-model failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # ghsource app-root fidelity (rsi-050, 2026-11-16): a repo root with
        # build manifests + framework dirs is ONE challenge (whole-repo
        # pull, .github skipped); competition roots (README/attachments
        # only, incl. loose index.php) must keep the subdir challenge pick.
        from core import ghsource as GH
        GH_TREES = {
            "app-root-repo": {
                "": [
                    {"type": "dir", "name": ".github", "path": ".github"},
                    {"type": "dir", "name": "static", "path": "static"},
                    {"type": "dir", "name": "views", "path": "views"},
                    {"type": "file", "name": "Dockerfile", "path": "Dockerfile",
                     "size": 406},
                    {"type": "file", "name": "index.js", "path": "index.js",
                     "size": 2531},
                    {"type": "file", "name": "package.json",
                     "path": "package.json", "size": 358},
                    {"type": "file", "name": "README.md", "path": "README.md",
                     "size": 348},
                ],
                "views": [{"type": "file", "name": "preview.ejs",
                           "path": "views/preview.ejs", "size": 608}],
                "static": [{"type": "file", "name": "style.css",
                            "path": "static/style.css", "size": 100}],
                ".github": [{"type": "file", "name": "ci.yml",
                             "path": ".github/ci.yml", "size": 10}],
            },
            "comp-repo": {
                "": [
                    {"type": "dir", "name": "Crypto-A", "path": "Crypto-A"},
                    {"type": "file", "name": "README.md", "path": "README.md",
                     "size": 100},
                ],
                "Crypto-A": [{"type": "file", "name": "task.py",
                              "path": "Crypto-A/task.py", "size": 100}],
            },
            "loose-files-repo": {
                "": [
                    {"type": "dir", "name": "attachments720",
                     "path": "attachments720"},
                    {"type": "file", "name": "index.php", "path": "index.php",
                     "size": 100},
                    {"type": "file", "name": "10086.apk", "path": "10086.apk",
                     "size": 100},
                    {"type": "file", "name": "README.md", "path": "README.md",
                     "size": 100},
                ],
                "attachments720": [{"type": "file", "name": "x.zip",
                                    "path": "attachments720/x.zip",
                                    "size": 50}],
            },
            "files-only-repo": {
                "": [{"type": "file", "name": "README.md",
                      "path": "README.md", "size": 100}],
            },
        }

        def _fake_entries(owner_repo, subpath, token="", deadline=None):
            # rsi-125: deadline kwarg mirrors the production _entries
            # signature (pull-chain budget); the mock ignores it -- these
            # assertions cover pick semantics, not wire behavior.
            tree = GH_TREES[owner_repo.split("/")[-1]]
            if subpath not in tree:
                raise GH.SourceError("no such path in fake tree: %s" % subpath)
            return tree[subpath]

        _orig_entries = GH._entries
        GH._entries = _fake_entries
        try:
            k, t, fs = GH._pick_challenge("CTF-Archives/app-root-repo",
                                          set(), "")
            assert k == "CTF-Archives/app-root-repo::.", \
                "app-root repo must pull whole-repo (got %s)" % k
            paths = [f["path"] for f in fs]
            assert "index.js" in paths and "views/preview.ejs" in paths, \
                "app-root pull lost server code or framework files"
            assert not any(p.startswith(".github") for p in paths), \
                "CI metadata must not count as challenge files"
            assert GH._pick_challenge(
                "CTF-Archives/app-root-repo",
                {"CTF-Archives/app-root-repo::."}, "") is None, \
                "used whole-repo key must dedup"
            k2, t2, fs2 = GH._pick_challenge("CTF-Archives/comp-repo",
                                              set(), "")
            assert k2 == "CTF-Archives/comp-repo::Crypto-A" and \
                [f["path"] for f in fs2] == ["Crypto-A/task.py"], \
                "competition repo must keep subdir challenge pick"
            k3, t3, fs3 = GH._pick_challenge(
                "CTF-Archives/loose-files-repo", set(), "")
            assert k3 == "CTF-Archives/loose-files-repo::attachments720" \
                and "index.php" not in [f["path"] for f in fs3], \
                "loose attachment files must not trigger whole-repo"
            k4, t4, fs4 = GH._pick_challenge("CTF-Archives/files-only-repo",
                                              set(), "")
            assert k4 == "CTF-Archives/files-only-repo::." and \
                [f["path"] for f in fs4] == ["README.md"], \
                "files-only repo keeps whole-repo behavior"
        finally:
            GH._entries = _orig_entries
        mod_results.append(True)
    except Exception:
        log("selftest ghsource failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # dedup zip/sampled fingerprints (dup 6th hole, 2026-11-16): the
        # same challenge re-packaged as a .zip attachment must collide
        # with its already-materialized EXTRACTED dir both ways (live
        # case: 2024-qwb-qsnxbs ezFindShell.zip vs 2024-qwb-qsnxbs__ezFindShell),
        # oversized dirs keep a sampled identity instead of none, and
        # inner deploy boilerplate must not fabricate a hit.
        import zipfile
        from core import dedup as DDX
        dd = os.path.join(scratch, "dedup-fp")
        os.makedirs(dd, exist_ok=True)
        dir_a = os.path.join(dd, "chall-extracted")
        dir_b = os.path.join(dd, "chall-zipped")
        os.makedirs(dir_a, exist_ok=True)
        os.makedirs(dir_b, exist_ok=True)
        payload = [("web_%02d.php" % i, ("shell-%d" % i).encode())
                   for i in range(30)]
        for n, c in payload:
            with open(os.path.join(dir_a, n), "wb") as f:
                f.write(c)
        with zipfile.ZipFile(os.path.join(dir_b, "samples.zip"),
                             "w") as zf:
            for n, c in payload:
                zf.writestr(n, c)
        with open(os.path.join(dir_b, "deploy.sh"), "w") as f:
            f.write("exit 0\n")
        fps_a = DDX.dir_fps(dir_a)
        assert len(fps_a) == 30, "30-file dir must keep full identity"
        idx_a = {"fps": {fp: {"chall-extracted": True} for fp in fps_a}}
        hit, why = DDX.is_dup_dir(idx_a, dir_b, "chall-zipped")
        assert hit and "samples.zip!!" in why, \
            "zip re-packaging must hit the extracted dir: %r" % why
        idx_b = {"fps": {fp: {"chall-zipped": True}
                         for fp in DDX.dir_fps(dir_b)}}
        hit_rev, why_rev = DDX.is_dup_dir(idx_b, dir_a, "chall-extracted")
        assert hit_rev, "extracted dir must hit the zip form back: %r" % why_rev
        # oversized (>FP_MAX_FILES) dirs: sampled identity, zip form hits
        big_a = os.path.join(dd, "big-extracted")
        big_b = os.path.join(dd, "big-zipped")
        os.makedirs(big_a, exist_ok=True)
        os.makedirs(big_b, exist_ok=True)
        for i in range(50):
            with open(os.path.join(big_a, "f%02d.dat" % i), "wb") as f:
                f.write(b"data-%03d" % i)
        with zipfile.ZipFile(os.path.join(big_b, "pack.zip"), "w") as zf:
            for i in range(50):
                zf.writestr("f%02d.dat" % i, b"data-%03d" % i)
        fps_big = DDX.dir_fps(big_a)
        assert len(fps_big) == DDX.FP_KEEP, \
            "oversized dir must keep a sampled identity"
        hit_big, _w = DDX.is_dup_dir(
            {"fps": {fp: {"big-extracted": True} for fp in fps_big}},
            big_b, "big-zipped")
        assert hit_big, "oversized extracted dir must hit its zip form"
        # inner deploy boilerplate must not fabricate a dup
        boil_a = os.path.join(dd, "boiler-a")
        boil_b = os.path.join(dd, "boiler-b")
        os.makedirs(boil_a, exist_ok=True)
        os.makedirs(boil_b, exist_ok=True)
        with zipfile.ZipFile(os.path.join(boil_a, "t.zip"), "w") as zf:
            zf.writestr("docker-compose.yml", "services: {}\n")
            zf.writestr("start.sh", "#!/bin/sh\n")
        with open(os.path.join(boil_b, "docker-compose.yml"), "w") as f:
            f.write("services: {}\n")
        fps_boil = DDX.dir_fps(boil_a)
        assert len(fps_boil) == 1 and "!!" not in list(fps_boil.values())[0], \
            "inner boilerplate must not contribute fps"
        miss, _w2 = DDX.is_dup_dir(
            {"fps": {fp: {"boiler-b": True}
                     for fp in DDX.dir_fps(boil_b)}},
            boil_a, "boiler-a")
        assert not miss, "inner boilerplate must not fabricate a dup"
        # fresh unique content passes; a corrupt zip keeps its own fp only
        fresh = os.path.join(dd, "fresh-zipped")
        os.makedirs(fresh, exist_ok=True)
        with zipfile.ZipFile(os.path.join(fresh, "new.zip"), "w") as zf:
            for i in range(5):
                zf.writestr("unique_%d.bin" % i, os.urandom(16))
        miss2, _w3 = DDX.is_dup_dir(idx_a, fresh, "fresh-zipped")
        assert not miss2, "fresh content must pass"
        badz = os.path.join(dd, "bad-zip-dir")
        os.makedirs(badz, exist_ok=True)
        with open(os.path.join(badz, "fake.zip"), "wb") as f:
            f.write(b"PK\x03\x04 not really a zip")
        assert len(DDX.dir_fps(badz)) == 1, \
            "corrupt zip must keep its own file fp, no crash"
        # --- evolve-16: pre-download git-blob fingerprint layer -------------
        # known vectors first: git's empty blob, then the header+content
        # construction for random bytes (streaming path must agree)
        import hashlib as _hl
        vec = os.path.join(dd, "blob-vec")
        os.makedirs(vec, exist_ok=True)
        empty = os.path.join(vec, "empty.bin")
        open(empty, "wb").close()
        assert DDX.git_blob_sha(empty) == \
            "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391", \
            "empty file must hash to git's empty-blob vector"
        raw = os.urandom(1500)
        rndf = os.path.join(vec, "rnd.bin")
        with open(rndf, "wb") as f:
            f.write(raw)
        assert DDX.git_blob_sha(rndf) == \
            _hl.sha1(b"blob %d\x00" % len(raw) + raw).hexdigest(), \
            "git blob sha must be sha1('blob <size>\\0'+content)"
        # selection parity: the blob layer keeps the exact same file
        # identity dir_fps keeps (30-file dir + oversized sampled dir)
        blobs_a = DDX.dir_blob_shas(dir_a)
        assert sorted(blobs_a.values()) == sorted(DDX.dir_fps(dir_a).values()), \
            "blob selection must mirror fps selection file-for-file"
        assert len(DDX.dir_blob_shas(big_a)) == DDX.FP_KEEP, \
            "oversized dir must keep a sampled blob identity"
        # pre-download pairs (name, sha, size) hit the blob index without
        # any download; fresh bytes pass; excluded names and oversize
        # entries never fabricate a hit; a missing sha passes safely
        pairs = [(_n, _hl.sha1(b"blob %d\x00" % len(_c) + _c).hexdigest(),
                  len(_c)) for _n, _c in payload]
        idx_bl = {"blobs": {s: {"chall-extracted": True} for s in blobs_a}}
        hit_bl, why_bl = DDX.is_dup_blobs(idx_bl, pairs)
        assert hit_bl and "chall-extracted" in why_bl, \
            "pre-download pairs must hit the blob index: %r" % why_bl
        miss_bl, _w4 = DDX.is_dup_blobs(
            idx_bl, [("new.bin", _hl.sha1(b"blob 1\x00x").hexdigest(), 1)])
        assert not miss_bl, "fresh blob content must pass"
        miss_ex, _w5 = DDX.is_dup_blobs(
            idx_bl, [("README.md",) + pairs[0][1:]])
        assert not miss_ex, "FP_EXCLUDE names must not fabricate hits"
        miss_big, _w6 = DDX.is_dup_blobs(
            idx_bl, [("huge.bin", pairs[0][1], DDX.FP_MAX_SIZE + 1)])
        assert not miss_big, "oversize pairs must be ignored"
        miss_nosha, _w7 = DDX.is_dup_blobs(idx_bl, [("a.bin", "", 3)])
        assert not miss_nosha, "entries without a sha must pass safely"
        # index self-heal: a prior index without the blobs section is
        # rebuilt (still keeping the learned-writeup memory)
        _ip = os.path.join(dd, "idx-selfheal.json")
        with open(_ip, "w", encoding="utf-8") as _f:
            json.dump({"fp_schema": DDX.FP_SCHEMA, "fps": {},
                       "writeups_done": {"keepme": "abc"}}, _f)
        _orig_ip = DDX.INDEX_PATH
        DDX.INDEX_PATH = _ip
        try:
            ix, _px = DDX.load_index()
            assert "blobs" in ix and \
                ix.get("blobs_schema") == DDX.BLOBS_SCHEMA, \
                "missing blobs section must self-heal via rebuild"
            assert ix.get("writeups_done", {}).get("keepme") == "abc", \
                "rebuild must keep the learned-writeup memory"
        finally:
            DDX.INDEX_PATH = _orig_ip
        # live production-index known-answer: a re-published smtp content
        # file precheck-hits under a foreign identity, never under its own
        _prod, _pp2 = DDX.load_index()
        _smtp = os.path.join(DDX.FEED_DIR, "2022-mtgxs-pwn-smtp")
        if os.path.isdir(_smtp):
            _sb = DDX.dir_blob_shas(_smtp)
            _live = [(_n, _s, os.path.getsize(os.path.join(_smtp, _n)))
                     for _s, _n in _sb.items()]
            _h1, _r1 = DDX.is_dup_blobs(_prod, _live, "2022-mtgxs-pwn-smtp")
            assert not _h1, "self-identity must be excluded, no self-hit"
            _h2, _r2 = DDX.is_dup_blobs(_prod, _live, "re-publisher")
            assert _h2 and "2022-mtgxs-pwn-smtp" in _r2, \
                "live index must precheck-block a re-published smtp"
        # --- evolve-11: year-in-identity + whole-repo exact-only ----------
        # (dup 5th hole; live misfire: the 2025-LanqiaoCup-Finals whole-repo
        # pick was fuzzy-killed by the 2024 0x100 writeup row
        # 'writeup:0x100-LanqiaoCup2024Finals' -> core '0x100cupfinals')
        _yr_pairs = {}
        for _e in ("2024-LanqiaoCup-Finals__0x100",
                   "writeup:0x100-LanqiaoCup2024Finals",
                   "2024-LanqiaoCup-Finals-dirty_data",
                   "2025-LanqiaoCup-Finals__10.应急3",
                   "writeup:babyRSA-LanqiaoCup2025Finals",
                   "2024-sanxia"):
            for _pk in DDX._pair_keys(_e):
                _yr_pairs.setdefault(DDX.SEP_KEY.join(_pk), set()).add("t")
        _yr = {"pairs": {k: sorted(v) for k, v in sorted(_yr_pairs.items())}}
        _wr25 = ("CTF-Archives/2025-LanqiaoCup-Finals::. "
                 "2025-LanqiaoCup-Finals")
        _h3, _r3 = DDX.is_dup(_yr, _wr25, whole_repo=True)
        assert not _h3, "whole-repo 2025 must not fuzzy-hit singles: %r" % _r3
        _h4, _r4 = DDX.is_dup(
            _yr, "CTF-Archives/2025-LanqiaoCup-Finals::0x100")
        assert not _h4, "cross-year single must not be a dup: %r" % _r4
        _h5, _r5 = DDX.is_dup(
            _yr, "CTF-Archives/2024-LanqiaoCup-Finals::0x100")
        assert _h5, "same-year single must stay a dup"
        _h6, _r6 = DDX.is_dup(
            _yr, "CTF-Archives/2024-LanqiaoCup-Finals::dirty_data "
                 "2024-LanqiaoCup-Finals-dirty_data")
        assert _h6, "same-year fuzzy (ledger tail vs record) must stay"
        _h7, _r7 = DDX.is_dup(_yr, "CTF-Archives/2024-sanxia::. 2024-sanxia",
                              whole_repo=True)
        assert _h7, "whole-repo vs whole-repo same-year must stay a dup"
        # production index must carry the new schema (load_index
        # self-heals a stale one) and answer the live misfire correctly
        assert _prod.get("pairs_schema") == DDX.PAIRS_SCHEMA, \
            "production index must carry pairs_schema after evolve-11"
        _hp, _rp = DDX.is_dup(_prod, _wr25, whole_repo=True)
        # flipped 2026-11-17 (evolve-20261116-19 schema bump surfaced it):
        # the 2025-LanqiaoCup-Finals repo was PULLED by the 03:01 feed
        # round (challenges row, engine-solved attempt) so the rebuilt
        # index correctly lists it -- the pre-2026-11-17 'must pass' form
        # encoded the then-true assumption that the repo was unseen; the
        # no-false-kill semantics stay guarded by the _yr assertions above
        # (_h3/_h9: a 2025 repo must not be fuzzy-killed by 2024 rows).
        assert _hp and _rp.startswith("exact(lanqiao/2025/cupfinals"), \
            "live prod: pulled 2025 whole-repo must be an exact dup: %r" \
            % _rp
        _hq, _rq = DDX.is_dup(
            _prod, "CTF-Archives/2024-LanqiaoCup-Finals::0x100")
        assert _hq, "live prod: 2024 single 0x100 must stay a dup"
        # --- evolve-20261116-20: skip history never blocks a re-pull ------
        # (dup 7th hole; live lock evidence pre-fix: three repos whose only
        # pickable candidate sat in persisted skipped_keys returned
        # 'repo exhausted' with no skip reason at all)
        assert _pull_exclusion({"keys": ["a", "b"],
                                "skipped_keys": ["a", "c"]}) == {"a", "b"}, \
            "persisted skip keys must never enter the picker exclusion"
        assert _pull_exclusion({"skipped_keys": ["x"]}) == set(), \
            "skip-only ledger must exclude nothing"
        assert _pull_exclusion({}) == set(), \
            "empty ledger must exclude nothing"
        assert _merge_skips(["a", "c"], ["b", "c"]) == ["a", "b", "c"], \
            "audit merge must union and sort in-call skips"
        assert _merge_skips(None, ["z"]) == ["z"], \
            "missing persisted list must still record new skips"
        _capped = _merge_skips(["k%03d" % i for i in range(600)], ["new"])
        assert len(_capped) == 500 and _capped[-1] == "new", \
            "audit merge must stay capped at 500 keeping the sort tail"
        # --- evolve-20261117-06: whole-repo category-alias exact (dup 8th
        # hole; live: the 2026-11-17 02:0x gh-org pull of CTF-Archives/
        # 2022-xhlj-crypto-myerrorlearn::. slipped all four layers -- the
        # candidate core kept the mid-name category glued
        # ('cryptomyerrorlearn'), whole-repo skips fuzzy, and the one shared
        # content file main.py had drifted bytes (old dir LF 602B vs repo
        # CRLF 634B) so blob/attach had nothing identical to match) --
        _aa_pairs = {}
        for _e in ("2022-xhlj-crypto-myerrorlearn/myerrorlearn",
                   "CTF-Archives__myerrorlearn"):
            for _pk in DDX._pair_keys(_e):
                _aa_pairs.setdefault(DDX.SEP_KEY.join(_pk),
                                     set()).add("done.json")
        _aa = {"pairs": {k: sorted(v) for k, v in sorted(_aa_pairs.items())}}
        _wc = ("CTF-Archives/2022-xhlj-crypto-myerrorlearn::. "
               "2022-xhlj-crypto-myerrorlearn")
        _h8, _r8 = DDX.is_dup(_aa, _wc, whole_repo=True)
        assert _h8 and "exact-alias" in _r8, \
            "glued-core whole-repo must exact-alias the clean single: %r" % _r8
        # negative: no leading category word -> no alias -> the evolve-11
        # live misfire precedent must stay a pass
        _h9, _r9 = DDX.is_dup(_yr, _wr25, whole_repo=True)
        assert not _h9, \
            "2025-LanqiaoCup-Finals whole-repo must stay a pass: %r" % _r9
        # negative: year gate on aliases (a 2030 same-name re-release is a
        # new identity; yearless legacy entries still dedup)
        _h10, _r10 = DDX.is_dup(
            _aa, "CTF-Archives/2030-xhlj-crypto-myerrorlearn::. "
                 "2030-xhlj-crypto-myerrorlearn", whole_repo=True)
        assert not _h10, "cross-year alias must not be a dup: %r" % _r10
        _h11, _r11 = DDX.is_dup(
            {"pairs": {DDX.SEP_KEY.join(("xhlj", "", "myerrorlearn")):
                       ["t"]}}, _wc, whole_repo=True)
        assert _h11 and "yearless-legacy" in _r11, \
            "yearless legacy alias must stay a dup: %r" % _r11
        # negative: unknown-comp candidates never alias (cr guard)
        _h12, _r12 = DDX.is_dup(
            _aa, "CTF-Archives/2022-xxcrypto-baby::. 2022-xxcrypto-baby",
            whole_repo=True)
        assert not _h12, "unknown-comp whole-repo must not alias: %r" % _r12
        # alias length guard: single/double-char aliases never fire
        _h13, _r13 = DDX.is_dup(
            {"pairs": {DDX.SEP_KEY.join(("xhlj", "2022", "z")): ["t"]}},
            "CTF-Archives/2022-xhlj-web-z::. 2022-xhlj-web-z",
            whole_repo=True)
        assert not _h13, "short alias must be length-gated: %r" % _r13
        # production-index known-answer: the live slip must now precheck-
        # block at the name layer (it slipped on 2026-11-17 02:0x)
        _ha, _ra = DDX.is_dup(_prod, _wc, whole_repo=True)
        assert _ha and "exact-alias" in _ra and "done.json" in _ra, \
            "live prod index must exact-alias the slipped repo: %r" % _ra
        mod_results.append(True)
    except Exception:
        log("selftest dedup-fp failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261116-21] structured-tag technique matcher: 标签/技术/
        # 技术链 label values and the pure tag line ARE the techniques
        # (verbatim, author-curated); 题型/类型/分类/类别 labels and the
        # tagline's stated category tokens adjudicate the category.
        # Whole-text TECH_PATTERNS voting stays only the no-structure
        # fallback. Live RED: 0603-MatCipher voted category=misc off a
        # 修复 engineering note and renamed known-plaintext into a
        # zip-password card while its real hill-cipher / mod-257 stack had
        # no patterns at all (rsi-064 known_side_effect).
        from learn.writeup_learner import parse_writeup as _pw
        wt = ("# MatCipher-like\n\n## 七维标签\n"
              "crypto / hill-cipher / known-plaintext / linear-system-mod-p"
              " / gauss-elimination / AES-ECB\n\n"
              "## 失败原因与变体预测\n"
              "工程侧注意：SKILL.md 索引已过期，待下轮核对修复。\n")
        p = _pw(wt)
        assert p["category"] == "crypto" and not p["category_inferred"], \
            "tagline first token must adjudicate the category (MatCipher RED was misc/inferred)"
        _names = [n for _c, n in p["techniques"]]
        assert "hill-cipher" in _names and "known-plaintext" in _names, \
            "author tags must become techniques verbatim: %r" % _names
        assert not any("repair" in n or "carving" in n for n in _names), \
            "decoy 修复 word must not leak into structured techniques"
        assert all(c == "crypto" for c, _n in p["techniques"]), \
            "structured techniques must carry the stated category"
        # label-value forms: 技术：lifts the technique list, 类型：declares
        wt2 = ("## 标签\n"
               "- 类型：reverse / android / crypto-tea\n"
               "- 技术：JNI 静态注册、dex 静态分析、自定义 TEA\n")
        p2 = _pw(wt2)
        assert p2["category"] == "reverse" and not p2["category_inferred"], \
            "类型 label value must declare the category"
        assert [n for _c, n in p2["techniques"]] == \
            ["JNI 静态注册", "dex 静态分析", "自定义 TEA"], \
            "技术 label value must lift the technique list verbatim"
        # ；-packed decl/packed one-liners reach per-label grammar
        wt3 = ("## 标签\n"
               "题型=forensics/工控流量隐写；信号=Modbus 风暴；"
               "技术链=pcap重组→AES-ZIP解密\n")
        p3 = _pw(wt3)
        assert p3["category"] == "forensics" and not p3["category_inferred"], \
            "packed 题型= must declare the category"
        assert [n for _c, n in p3["techniques"]] == ["pcap重组", "AES-ZIP解密"], \
            "packed 技术链= must lift the chain as techniques"
        # fallback intact: no structured source -> keyword vote as before
        p4 = _pw("# plain\n\nwe used rsa factoring with a private key modulus.\n")
        assert p4["category"] == "crypto" and p4["techniques"] and \
            p4["techniques"][0][1].startswith("RSA"), \
            "no-structure writeups must keep the keyword-vote fallback"
        # bracket-aware splitter invariants
        from learn.writeup_learner import _split_tags as _st
        assert len([x for x in _st("wxapkg、wasm-reverse、reverse") if x.strip()]) == 3, \
            "、 must split tags"
        assert len([x for x in _st("fastjson-1.2.83 / 类污染/原型链污染") if x.strip()]) == 2, \
            "unspaced / must stay inside a tag"
        assert len([x for x in _st("safe-linking混淆(G=0x9e37…,seed=AT_RANDOM)") if x.strip()]) == 1, \
            "bracket content must be protected from comma splits"
        assert len([x for x in _st("pcap重组→AES-ZIP解密→PNG尾部提取") if x.strip()]) == 3, \
            "chain arrows must split"
        mod_results.append(True)
    except Exception:
        log("selftest evolve21-matcher failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-05] JSON-safe adjudication write path: rsi-066
        # hand-rolled the large_note retraction with a repr-style \U0000FFFD
        # escape (json.loads rejects it -- the whole evidence file died);
        # adjudicate must (a) serialize every value through the stdlib
        # encoder (\ufffd), (b) byte-faithfully back up the pre-adjudication
        # state, (c) verify the write by re-parsing, and (d) refuse an
        # already-corrupt result file instead of silently munging it.
        _ad = os.path.join(scratch, "evolve05-adjudicate")
        os.makedirs(_ad, exist_ok=True)
        _rp = os.path.join(_ad, "_ctfpilot_result.json")
        _garbage = "FLAG{no_dLAG\U0000FFFDmo_WLAG}"
        with open(_rp, "w", encoding="utf-8") as f:
            json.dump({"name": "evolve05-fixture", "category": "pwn",
                       "solved": True, "flag": _garbage,
                       "time_seconds": 401.5, "attempts": 130,
                       "mode": "auto",
                       "provenance": {"status": "derived"}}, f,
                      ensure_ascii=True)
        _rc5 = do_adjudicate(_NS(dir=_ad,
                                 true_status="NOT SOLVED (dynamic $FLAG)",
                                 reason="xor-token decoy-key garbage flag",
                                 by="selftest evolve-05", set_solved="0",
                                 set_flag="", original_solved=None,
                                 original_flag=None,
                                 field=["poison_lesson_deleted=617"]), cfg)
        assert _rc5 == 0, "adjudicate must succeed on a valid file: rc=%r" % _rc5
        with open(_rp, "r", encoding="utf-8") as f:
            _back = json.load(f)        # rsi-066's file died right here
        assert _back["solved"] is False and _back["flag"] == "", \
            "top-level retraction must apply"
        _blk = _back["adjudication"]
        assert _blk["retracted"] is True, "retraction must be marked"
        assert _blk["original_solved"] == 1 and \
            _blk["original_flag"] == _garbage, \
            "originals must be auto-captured byte-faithfully"
        assert _blk["poison_lesson_deleted"] == "617", "--field must land"
        with open(_rp, "r", encoding="utf-8") as f:
            _raw = f.read()
        assert "\\U0000" not in _raw, \
            "repr-style \\U0000 escapes must never appear (rsi-066 bug)"
        assert "\\ufffd" in _raw, "U+FFFD must be the valid \\ufffd escape"
        _bks = [x for x in os.listdir(_ad)
                if x.startswith("_ctfpilot_result.pre-adjudicate-")]
        assert len(_bks) == 1, "one pre-adjudication backup: %r" % _bks
        with open(os.path.join(_ad, _bks[0]), "r", encoding="utf-8") as f:
            _orig = json.load(f)
        assert _orig["solved"] is True and _orig["flag"] == _garbage, \
            "backup must hold the pre-adjudication state"
        # RED negative: a corrupt (repr-escaped) result file is refused and
        # left untouched
        _bad = os.path.join(scratch, "evolve05-corrupt")
        os.makedirs(_bad, exist_ok=True)
        _bp = os.path.join(_bad, "_ctfpilot_result.json")
        with open(_bp, "w", encoding="utf-8") as f:
            f.write('{"name": "corrupt", "solved": false, '
                    '"original_flag": "FLAG{no_dLAG\\U0000FFFDmo_WLAG}"}')
        _rc5b = do_adjudicate(_NS(dir=_bad, true_status="x", reason="y",
                                  by="selftest", set_solved=None,
                                  set_flag=None, original_solved=None,
                                  original_flag=None, field=[]), cfg)
        assert _rc5b == 2, "corrupt result file must be refused: rc=%r" % _rc5b
        with open(_bp, "r", encoding="utf-8") as f:
            _rawb = f.read()
        assert "\\U0000FFFD" in _rawb, "corrupt file must be left untouched"
        mod_results.append(True)
    except Exception:
        log("selftest evolve05-adjudicate failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-07] rebuild_lessons prune pass: upsert-only left
        # stale-category twins (rsi-070's 22 re-adjudications -> 54 twins)
        # and dead shadows (removed KB cases) forever; rsi-071 cleaned the
        # stock by hand. rebuild must now self-clean, same predicates as
        # lab/probes.py lesson_shadow_audit: (a) GREEN -- a re-adjudicated
        # case's old-category twin is counter-merged into the live twin and
        # deleted, a removed case's dead shadow is deleted, live shadows and
        # their counters are untouched; (b) idempotent -- a second rebuild
        # deletes/merges nothing; (c) conservative -- a twin whose live
        # twin is missing is kept (never lose the sole content carrier).
        from knowledge import rebuild_lessons
        pr_db = os.path.join(scratch, "evolve07-prune", "store.sqlite")
        pr_store = TrajStore(pr_db)
        cases = [{"id": "case-aaaa1111", "name": "keep-me", "category": "web",
                  "signals": "s1"},
                 {"id": "case-bbbb2222", "name": "removed-later",
                  "category": "crypto", "signals": "s2"}]
        _n0, _d0, _t0 = rebuild_lessons(pr_store, cases)
        assert (_n0, _d0, _t0) == (2, 0, 0), "fresh upsert: %r" % _d0
        # re-adjudicate keep-me: crypto -> web, and drop removed-later
        cases[0]["category"], cases[1:] = "crypto", []
        _n1, _d1, _t1 = rebuild_lessons(pr_store, cases)
        assert (_n1, _d1, _t1) == (1, 1, 1), \
            "one dead shadow + one merged twin: %r/%r/%r" % (_n1, _d1, _t1)
        rows = pr_store.conn.execute(
            "SELECT category, hit_count, success_count FROM lessons "
            "WHERE challenge_id=0 ORDER BY id").fetchall()
        assert rows == [("crypto", 0, 0)], \
            "only the live twin of the re-adjudicated case must survive: %r" % (rows,)
        # counters: the crypto live twin had hits, then the case is
        # re-adjudicated back to web -> the crypto row becomes the stale
        # twin and its counters must merge into the new web live twin
        pr_store.conn.execute(
            "UPDATE lessons SET hit_count=5, success_count=2 "
            "WHERE category='crypto' AND challenge_id=0")
        pr_store.conn.commit()
        cases[0]["category"] = "web"   # re-adjudicate back: web twin is stale
        _n2, _d2, _t2 = rebuild_lessons(pr_store, cases)
        assert (_n2, _d2, _t2) == (1, 0, 1), "second twin must merge: %r" % _t2
        rows = pr_store.conn.execute(
            "SELECT category, hit_count, success_count FROM lessons "
            "WHERE challenge_id=0").fetchall()
        assert rows == [("web", 5, 2)], \
            "counters must merge into the live twin: %r" % (rows,)
        # idempotency: rebuild again -> zero deletions, zero merges
        _n3, _d3, _t3 = rebuild_lessons(pr_store, cases)
        assert (_d3, _t3) == (0, 0), "prune must be idempotent: %r/%r" % (_d3, _t3)
        # extra twin on an existing live row still converges through rebuild
        pr_store.conn.execute(
            "INSERT INTO lessons(challenge_id, ts, category, technique, "
            "when_to_use, snippet) VALUES(0, ?, 'pwn', 'case:ghost', "
            "'case ''case-aaaa1111'': stale', '')", (time.time(),))
        pr_store.conn.commit()
        _n4, _d4, _t4 = rebuild_lessons(pr_store, cases)
        assert (_d4, _t4) == (0, 1), \
            "ghost twin must merge into the live twin: %r/%r" % (_d4, _t4)
        assert pr_store.conn.execute(
            "SELECT COUNT(*) FROM lessons WHERE category='pwn' "
            "AND challenge_id=0").fetchone()[0] == 0, \
            "merged twin must be deleted"
        # conservative keep: a stale twin whose live twin is ABSENT is the
        # sole content carrier -- direct prune (no upsert first) must keep
        # it instead of deleting data it cannot prove dead
        from knowledge import _prune_case_shadows
        pr_store.conn.execute(
            "DELETE FROM lessons WHERE challenge_id=0 AND category='web'")
        pr_store.conn.execute(
            "INSERT INTO lessons(challenge_id, ts, category, technique, "
            "when_to_use, snippet) VALUES(0, ?, 'pwn', 'case:ghost', "
            "'case ''case-aaaa1111'': stale', 'sole carrier')", (time.time(),))
        pr_store.conn.commit()
        _d5, _t5 = _prune_case_shadows(pr_store, cases)
        assert (_d5, _t5) == (0, 0), \
            "no live twin -> conservative keep, not delete: %r/%r" % (_d5, _t5)
        assert pr_store.conn.execute(
            "SELECT COUNT(*) FROM lessons WHERE challenge_id=0").fetchone()[0] == 1, \
            "kept twin row must still exist"
        mod_results.append(True)
    except Exception:
        log("selftest evolve07-prune failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-10] adoption dual-file guarded write: rsi-067..073
        # seven rounds lost their version-chain entries to an unguarded hand
        # append (rebuilt by hand in the rsi-074 round, reconstructed=true).
        # transition(adopted) is now the only adoption write path: chain line
        # + ledger row commit together, verified by re-parsing both files,
        # rolled back byte-faithfully on any failure. A re-adopt of a pid
        # that already carries a chain line must never duplicate it.
        r10 = os.path.join(scratch, "evolve10-dualwrite")
        os.makedirs(os.path.join(r10, "skills", "rsi"), exist_ok=True)
        os.makedirs(os.path.join(r10, "skills", "arena", "state"),
                    exist_ok=True)
        cpath = R.chain_path(r10)
        seed = {"winner": "seed/variant-v0", "pid": "rsi-900",
                "date": "2026-11-17 00:00:00", "chain_entry": 1}
        with open(cpath, "w", encoding="utf-8") as f:
            f.write(json.dumps(seed, ensure_ascii=False) + "\n")
        lpath = os.path.join(r10, "skills", "rsi", "ledger.json")
        with open(lpath, "w", encoding="utf-8") as f:
            json.dump({"proposals": [
                {"id": "rsi-901", "type": "measurement",
                 "target": "evolve10-fixture", "status": "proposed"}]}, f,
                ensure_ascii=False)
        # GREEN: adopt -> both files commit, verified, chain_entry=2
        h = R.transition("rsi-901", "adopted", note="green adopt",
                        root=r10, chain_fields={
                            "winner": "seed/v1",
                            "evidence": {"gates": "fake"}})
        assert h.get("chain_entry") == 2, \
            "chain_entry must be 2, got %r" % h.get("chain_entry")
        rows = R.load_chain(cpath)
        assert len(rows) == 2 and rows[-1]["pid"] == "rsi-901" \
            and str(rows[-1]["prev"]).startswith("seed/variant-v0 ") \
            and rows[-1]["evidence"] == {"gates": "fake"}, \
            "chain line must carry pid/prev/evidence: %r" % (rows[-1],)
        led = R.load_ledger(lpath)
        assert any(p["id"] == "rsi-901" and p["status"] == "adopted"
                   for p in led["proposals"]), "ledger row must be adopted"
        # re-adopt: history only, never a duplicate chain line
        R.transition("rsi-901", "adopted", note="re-adopt", root=r10)
        assert len(R.load_chain(cpath)) == 2, \
            "re-adopt must not duplicate the chain line"
        # RED: ledger write failure after a successful chain append ->
        # BOTH files roll back byte-identical, exception propagates
        before_c = open(cpath, "rb").read()
        with open(lpath, "w", encoding="utf-8") as f:
            json.dump({"proposals": [
                {"id": "rsi-902", "type": "measurement",
                 "target": "evolve10-rollback", "status": "proposed"}]}, f,
                ensure_ascii=False)
        before_l = open(lpath, "rb").read()
        _orig_save = R.save_ledger

        def _boom(data, path):
            raise OSError("simulated ledger write failure")
        R.save_ledger = _boom
        try:
            try:
                R.transition("rsi-902", "adopted", root=r10)
                raise AssertionError("simulated failure must propagate")
            except OSError:
                pass
        finally:
            R.save_ledger = _orig_save
        assert open(cpath, "rb").read() == before_c, \
            "chain must roll back byte-identical when the ledger write fails"
        led2 = R.load_ledger(lpath)
        assert open(lpath, "rb").read() == before_l, \
            "ledger must roll back byte-identical too"
        r902 = [p for p in led2["proposals"] if p["id"] == "rsi-902"][0]
        assert r902["status"] == "proposed" and "history" not in r902, \
            "ledger row must be untouched by the failed adoption"
        # fresh-root adoption: no chain file yet -> entry 1, prev genesis
        r10b = os.path.join(scratch, "evolve10-freshroot")
        os.makedirs(os.path.join(r10b, "skills", "rsi"), exist_ok=True)
        with open(os.path.join(r10b, "skills", "rsi", "ledger.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"proposals": [
                {"id": "rsi-903", "type": "measurement",
                 "target": "evolve10-genesis", "status": "proposed"}]}, f,
                ensure_ascii=False)
        h3 = R.transition("rsi-903", "adopted", root=r10b)
        rows3 = R.load_chain(R.chain_path(r10b))
        assert h3.get("chain_entry") == 1 and rows3[-1]["prev"] == "genesis" \
            and rows3[-1]["change"] == [], \
            "genesis chain line must be entry 1: %r" % (rows3[-1],)
        # approve/reject stay ledger-only: no chain line ever
        with open(os.path.join(r10b, "skills", "rsi", "ledger.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"proposals": [
                {"id": "rsi-904", "type": "measurement",
                 "target": "evolve10-approve", "status": "proposed"}]}, f,
                ensure_ascii=False)
        R.transition("rsi-904", "approved", root=r10b)
        assert len(R.load_chain(R.chain_path(r10b))) == 1, \
            "approve must not touch the version chain"
        mod_results.append(True)
    except Exception:
        log("selftest evolve10-dualwrite failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261116-17] learn-writeup auto-rebuild: a real learn must
        # leave skills/knowledge consistent (dfjk 2026-11-16 19:40 claimed a
        # cases count the on-disk build never carried). The decision helper
        # is exercised branch-by-branch with the source resolution and the
        # heavy rebuild stubbed, so selftest never writes production
        # cases.jsonl; the real CLI path is covered by the RED/GREEN scratch
        # engine replays.
        r17 = os.path.join(scratch, "evolve17-learn-rebuild")
        wdir = os.path.join(r17, "writeups")
        os.makedirs(os.path.join(r17, "knowledge"), exist_ok=True)
        os.makedirs(wdir, exist_ok=True)
        with open(os.path.join(wdir, "in_tree.md"), "w",
                  encoding="utf-8") as f:
            f.write("# seed writeup\n")
        with open(os.path.join(r17, "knowledge", "stats.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"writeups_dir": wdir}, f)
        # patch module globals through the globals() dict: a bare-name
        # assignment here would make do_selftest treat these helpers as
        # locals (UnboundLocalError on first read, caught live this round)
        _g = globals()
        _orig_src = _g["_knowledge_source_dir"]
        _orig_rebuild = _g["do_rebuild_knowledge"]
        calls = []

        def _fake_rebuild(args, cfg):
            calls.append((args.dir, args.exclude, args.dry_run))
            return 0
        try:
            # real resolution: stats-recorded dir first, engine default next
            got_src, got_ok = _knowledge_source_dir()
            assert got_ok and os.path.isdir(got_src), \
                "production stats.json must resolve to a live source: %r" \
                % got_src
            _g["_knowledge_source_dir"] = lambda: (wdir, True)
            # in-tree learn: rebuild invoked via the CLI path with the
            # recorded source dir and production flags
            _g["do_rebuild_knowledge"] = _fake_rebuild
            rc = _rebuild_knowledge_after_learn(
                cfg, os.path.join(wdir, "in_tree.md"))
            assert rc == 0 and calls == [(wdir, None, False)], \
                "in-tree learn must rebuild via CLI path, got rc=%r %r" \
                % (rc, calls)
            # outside-tree writeup: explicit notice, never a rebuild
            calls.clear()
            rc2 = _rebuild_knowledge_after_learn(
                cfg, os.path.join(r17, "outside.md"))
            assert rc2 == 3 and not calls, \
                "outside-tree learn must notice without rebuild, rc=%r" \
                % rc2
            # rebuild raising -> pending notice, learn stays committed
            def _boom_rebuild(args, cfg):
                raise OSError("simulated rebuild failure")
            _g["do_rebuild_knowledge"] = _boom_rebuild
            rc3 = _rebuild_knowledge_after_learn(
                cfg, os.path.join(wdir, "in_tree.md"))
            assert rc3 == 4, "rebuild failure must surface pending, rc=%r" \
                % rc3
            # rebuild returning non-zero -> pending notice passthrough
            _g["do_rebuild_knowledge"] = lambda a, c: 2
            rc4 = _rebuild_knowledge_after_learn(
                cfg, os.path.join(wdir, "in_tree.md"))
            assert rc4 == 2, "bad rebuild rc must pass through, rc=%r" % rc4
            # missing source dir -> explicit pending notice, no rebuild call
            _g["do_rebuild_knowledge"] = _fake_rebuild
            calls.clear()
            _g["_knowledge_source_dir"] = lambda: (wdir, False)
            rc5 = _rebuild_knowledge_after_learn(
                cfg, os.path.join(wdir, "in_tree.md"))
            assert rc5 == 2 and not calls, \
                "missing source must notice without rebuild, rc=%r" % rc5
        finally:
            _g["_knowledge_source_dir"] = _orig_src
            _g["do_rebuild_knowledge"] = _orig_rebuild
        mod_results.append(True)
    except Exception:
        log("selftest evolve17-learn-rebuild failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261116-19] BOM-rewritten fingerprints: legacy PowerShell
        # rewrites seeded UTF-8 BOMs into old-timer dirs (rsi-060 evidence),
        # so their md5/blob-sha fps are unfaithful -- they dead-weight the
        # index and let re-published PRISTINE bytes leak pre-download. The
        # fix excludes the polluted FILES (not whole dirs): pristine
        # siblings keep their part-polluted dedup power, FP_EXCLUDE names
        # (engine traces with a BOM too) never reach the check, the name
        # layer is untouched, and both schema bumps force stale indexes to
        # self-heal-rebuild the polluted fps away.
        from core import dedup as DDX
        r19 = os.path.join(scratch, "evolve19-bom-skip")
        feed19 = os.path.join(r19, "feed")
        ws19 = os.path.join(r19, "ws")
        bomd = os.path.join(feed19, "2024-ciscn__bom")
        clnd = os.path.join(feed19, "2024-ciscn__clean")
        wsbm = os.path.join(ws19, "2024-ciscn__wsbom")
        lrnd = os.path.join(r19, "learned")
        for d in (bomd, clnd, wsbm, lrnd):
            os.makedirs(d, exist_ok=True)
        _notes = b"original notes bytes\n"
        _bomnotes = b"\xef\xbb\xbf" + _notes
        _task = b"print('task')\n"
        _trace = b"\xef\xbb\xbfgenerated snippet\n"
        with open(os.path.join(bomd, "notes.txt"), "wb") as f:
            f.write(_bomnotes)
        with open(os.path.join(bomd, "task.py"), "wb") as f:
            f.write(_task)
        with open(os.path.join(bomd, "_ctfpilot_step.py"), "wb") as f:
            f.write(_trace)          # FP_EXCLUDE name with a BOM: ignored
        with open(os.path.join(clnd, "task2.py"), "wb") as f:
            f.write(b"x = 1\n")
        with open(os.path.join(wsbm, "README.md"), "wb") as f:
            f.write(b"\xef\xbb\xbfrewritten readme\n")  # excluded name
        with open(os.path.join(wsbm, "main.py"), "wb") as f:
            f.write(b"y = 2\n")
        # unit: the detector itself
        assert DDX._file_bom_polluted(os.path.join(bomd, "notes.txt")), \
            "BOM-headed content file must be flagged"
        assert not DDX._file_bom_polluted(os.path.join(bomd, "task.py")), \
            "pristine file must pass"
        assert not DDX._file_bom_polluted(
            os.path.join(bomd, "missing.bin")), \
            "missing file must pass safely"
        tiny = os.path.join(bomd, "tiny.txt")
        with open(tiny, "wb") as f:
            f.write(b"ab")           # < 3 bytes: no BOM possible
        assert not DDX._file_bom_polluted(tiny), \
            "short file must pass (no BOM possible)"
        # selection: the polluted file contributes nothing, siblings
        # survive in BOTH fingerprint layers
        _fps_bom = DDX.dir_fps(bomd)
        assert not any(k.split(DDX.SEP_KEY, 1)[0] == "notes.txt"
                       for k in _fps_bom), "BOM file must not contribute fps"
        assert any(k.split(DDX.SEP_KEY, 1)[0] == "task.py"
                   for k in _fps_bom), \
            "pristine sibling must keep its fp"
        _bl_bom = DDX.dir_blob_shas(bomd)
        assert _bl_bom and "notes.txt" not in _bl_bom.values(), \
            "BOM file must not contribute a blob sha"
        assert "task.py" in _bl_bom.values(), \
            "pristine sibling must keep its blob sha"
        # index build: polluted fingerprints never seed, the name layer
        # stays intact, the schemas carry the new generation
        _o = (DDX.LEGACY_JSON, DDX.DB_PATH, DDX.LEARNED_DIR,
              DDX.LEGACY_DIRS, DDX.WS_ROOT)
        DDX.LEGACY_JSON = os.path.join(r19, "no-done.json")
        DDX.DB_PATH = os.path.join(r19, "no-db.sqlite")
        DDX.LEARNED_DIR = lrnd
        DDX.LEGACY_DIRS = feed19
        DDX.WS_ROOT = ws19
        try:
            idx19 = DDX.build_index()
            import hashlib as _hl19

            def _gsha19(c):
                return _hl19.sha1(
                    b"blob %d\x00" % len(c) + c).hexdigest()
            assert _gsha19(_bomnotes) not in idx19["blobs"], \
                "polluted blob sha must never seed the index"
            assert _gsha19(_task) in idx19["blobs"], \
                "part-polluted dedup power must survive the rebuild"
            hit_leak, _w = DDX.is_dup_blobs(
                idx19, [("notes.txt", _gsha19(_notes), len(_notes))],
                "re-publisher")
            assert not hit_leak, "pristine re-publish stays a leak until " \
                "the dir is re-pulled (honest, sourceless index side)"
            hit_dead, _w2 = DDX.is_dup_blobs(
                idx19, [("notes.txt", _gsha19(_bomnotes), len(_bomnotes))],
                "re-publisher")
            assert not hit_dead, "dead fingerprint must not fabricate a hit"
            assert idx19["fp_schema"] == DDX.FP_SCHEMA and \
                idx19["blobs_schema"] == DDX.BLOBS_SCHEMA, \
                "schemas must carry the BOM-exclusion generation"
            assert DDX.SEP_KEY.join(("ciscn", "2024", "bom")) \
                in idx19["pairs"], "name layer must stay untouched"
        finally:
            (DDX.LEGACY_JSON, DDX.DB_PATH, DDX.LEARNED_DIR,
             DDX.LEGACY_DIRS, DDX.WS_ROOT) = _o
        mod_results.append(True)
    except Exception:
        log("selftest evolve19-bom-skip failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-11] wrapper redirection byproducts never enter the
        # three observation faces (scan/evidence, provenance, wrap-guard
        # signature). Live evidence: 2026-LanqiaoCup-Finals ~25/71 attempts
        # burned scanning .solve.log; 2024-dfjk-Pwn's ext=.log vote dragged
        # a pwn-family dud to forensics; the provenance face would retract a
        # genuine solve whose printed flag the wrapper parked in .solve.log;
        # the signature face never repeated, silently defeating the guard.
        from core.router import inventory, route, WRAPPER_BYPRODUCTS
        from core.flagcheck import flag_in_raw
        from deep.loop import _dir_signature
        r2111 = os.path.join(scratch, "evolve17-11-scanface")
        os.makedirs(r2111, exist_ok=True)
        with open(os.path.join(r2111, "task.txt"), "wb") as f:
            f.write(b"plain challenge notes\n")
        with open(os.path.join(r2111, ".solve.log"), "wb") as f:
            f.write(b"[triage] scanning challenge dir\n")
        with open(os.path.join(r2111, ".solve.err"), "wb") as f:
            f.write(b"")
        # face 1: scan + evidence exclusion, real files survive
        names = [f["path"] for f in inventory(r2111)]
        assert ".solve.log" not in names and ".solve.err" not in names, \
            "wrapper byproducts must not enter the scan face: %r" % names
        assert "task.txt" in names, "real attachments must survive"
        rt = route(r2111)
        assert not any(".log" in e for e in rt["evidence"]), \
            "ext=.log vote must not pollute evidence: %r" % rt["evidence"]
        # face 2: provenance -- engine-printed flag in .solve.log is NOT a
        # raw echo; a real challenge-provided placeholder still IS
        with open(os.path.join(r2111, ".solve.log"), "ab") as f:
            f.write(b"[result] SOLVED flag{engine_stdout_echo}\n")
        hit, rel = flag_in_raw(r2111, "flag{engine_stdout_echo}")
        assert not hit, "engine stdout echo must not be judged raw: %r" % rel
        with open(os.path.join(r2111, "task.txt"), "ab") as f:
            f.write(b"placeholder flag{challenge_provided}\n")
        hit2, rel2 = flag_in_raw(r2111, "flag{challenge_provided}")
        assert hit2 and rel2 == "task.txt", \
            "real challenge-provided flag must stay a raw echo"
        # face 3: wrap-guard signature stable across .solve.log growth,
        # still sensitive to genuine new input
        sig1 = _dir_signature(r2111)
        with open(os.path.join(r2111, ".solve.log"), "ab") as f:
            f.write(b"[fast] detector progress (grows during solve)\n")
        assert _dir_signature(r2111) == sig1, \
            "signature must ignore wrapper byproduct growth"
        with open(os.path.join(r2111, "task.txt"), "ab") as f:
            f.write(b"new genuine input\n")
        assert _dir_signature(r2111) != sig1, \
            "genuine new input must still change the signature"
        # name set is exact: no broad prefix that could eat real attachments
        assert WRAPPER_BYPRODUCTS == frozenset({".solve.log", ".solve.err"}), \
            "byproduct name set must stay exact"
        mod_results.append(True)
    except Exception:
        log("selftest evolve17-11-scanface failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-13] wrapper redirection byproducts never seed the
        # dedup index -- the FINGERPRINT face, found open live by the rsi-080
        # round after rsi-079 closed the three observation faces. Live RED on
        # the production index: 14 byproduct fps keys (26 owner-links; the
        # empty .solve.err md5 d41d8cd9 alone owned by 13 dirs) plus 14
        # byproduct blob keys, re-seeded at every rebuild; two challenges
        # whose wrappers both leave an empty .solve.err collide via
        # attach-fp. GREEN: name-exact exclusion at the two dir-level
        # selection loops; zip INNER entries keep full identity power
        # (wrapper byproducts can never exist inside a challenge-shipped
        # archive); schema generation bumps force self-heal rebuilds.
        from core.router import WRAPPER_BYPRODUCTS as _R_BY
        import zipfile as _zf
        r2113 = os.path.join(scratch, "evolve13-dedup-byproducts")
        os.makedirs(r2113, exist_ok=True)
        d1 = os.path.join(r2113, "2024-FakeCup__one")
        d2 = os.path.join(r2113, "2025-OtherCup__two")
        os.makedirs(d1)
        os.makedirs(d2)
        with open(os.path.join(d1, "task.txt"), "wb") as f:
            f.write(b"challenge one notes\n")
        with open(os.path.join(d1, ".solve.log"), "wb") as f:
            f.write(b"[triage] wrapper stdout byproduct\n")
        with open(os.path.join(d1, ".solve.err"), "wb") as f:
            f.write(b"")
        with open(os.path.join(d2, "notes.txt"), "wb") as f:
            f.write(b"a completely different challenge\n")
        with open(os.path.join(d2, ".solve.err"), "wb") as f:
            f.write(b"")
        fps1 = DDX.dir_fps(d1)
        shas1 = DDX.dir_blob_shas(d1)
        assert all(k.split(DDX.SEP_KEY)[0] not in _R_BY for k in fps1), \
            "byproducts must not seed fps: %r" % sorted(fps1)
        assert all(n not in _R_BY for n in shas1.values()), \
            "byproducts must not seed blob shas: %r" % sorted(shas1.values())
        assert any(k.split(DDX.SEP_KEY)[0] == "task.txt" for k in fps1), \
            "real attachments keep their fingerprints"
        hit, reason = DDX.is_dup_dir({"fps": dict(fps1)}, d2)
        assert not hit, \
            "two challenges must not collide on wrapper exhaust: %r" % reason
        # zip inner entries keep full identity power: byproducts can never
        # exist inside a challenge-shipped archive, so the exclusion stays
        # at dir level on purpose
        zp = os.path.join(d1, "pack.zip")
        with _zf.ZipFile(zp, "w") as z:
            z.writestr(".solve.log", b"challenge-shipped inner artifact")
            z.writestr("real_inner.txt", b"inner content\n")
        fpsz = DDX.dir_fps(d1)
        vals = set(fpsz.values())
        assert "pack.zip!!.solve.log" in vals and \
            "pack.zip!!real_inner.txt" in vals, \
            "zip inner identity must stay at full power (dir-level scope)"
        # name sets stay equal to the canonical router definition
        assert DDX.WRAPPER_BYPRODUCTS == _R_BY == \
            frozenset({".solve.log", ".solve.err"}), "byproduct set drift"
        # schema generation: a stale index must self-heal on next touch,
        # never pin pre-exclusion fps/blobs sections back over a rebuild
        assert DDX.FP_SCHEMA >= 4 and DDX.BLOBS_SCHEMA >= 3, \
            "schemas must carry the byproduct-exclusion generation"
        mod_results.append(True)
    except Exception:
        log("selftest evolve13-dedup-byproducts failed:"); traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-14: empty files carry zero identity ---------------
    try:
        r2114 = os.path.join(scratch, "evolve14-empty-file-identity")
        os.makedirs(r2114, exist_ok=True)
        e1 = os.path.join(r2114, "2024-FakeCup__pyempty_a")
        e2 = os.path.join(r2114, "2025-OtherCup__pyempty_b")
        os.makedirs(e1)
        os.makedirs(e2)
        with open(os.path.join(e1, "task.txt"), "wb") as f:
            f.write(b"challenge one notes\n")
        with open(os.path.join(e1, "__init__.py"), "wb") as f:
            f.write(b"")            # legit empty package marker
        with open(os.path.join(e2, "task.txt"), "wb") as f:
            f.write(b"a completely different challenge\n")
        with open(os.path.join(e2, "__init__.py"), "wb") as f:
            f.write(b"")            # same empty name, different challenge
        fps1 = DDX.dir_fps(e1)
        shas1 = DDX.dir_blob_shas(e1)
        assert all(not k.split(DDX.SEP_KEY)[-1].startswith("d41d8cd9")
                   for k in fps1), \
            "empty files must not seed fps: %r" % sorted(fps1)
        assert "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391" not in shas1, \
            "empty files must not seed blob shas"
        assert any(k.split(DDX.SEP_KEY)[0] == "task.txt" for k in fps1), \
            "real attachments keep their fingerprints"
        hit, reason = DDX.is_dup_dir({"fps": dict(fps1)}, e2)
        assert not hit, \
            "two challenges must not collide on an empty file: %r" % reason
        # pre-download mirror: a candidate listing ANY empty file must not
        # false-hit the universal empty blob sha
        pairs = [("__init__.py",
                  "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391", 0),
                 ("real.py", DDX.git_blob_sha(os.path.join(e2, "task.txt")),
                  os.path.getsize(os.path.join(e2, "task.txt")))]
        hit2, reason2 = DDX.is_dup_blobs({"blobs": dict(shas1)}, pairs,
                                          self_name="2025-OtherCup__pyempty_b")
        assert not hit2, \
            "empty-file candidate listing must not precheck-hit: %r" % reason2
        # non-empty true re-publication keeps hitting precheck
        hit3, reason3 = DDX.is_dup_blobs(
            {"blobs": dict(shas1)},
            [("task.txt", DDX.git_blob_sha(os.path.join(e1, "task.txt")),
              os.path.getsize(os.path.join(e1, "task.txt")))],
            self_name="re-publisher")
        assert hit3, \
            "non-empty true re-publication must still precheck-hit"
        # zip INNER empty entries are excluded in lockstep with the top level
        zp = os.path.join(e1, "pack.zip")
        with _zf.ZipFile(zp, "w") as z:
            z.writestr("__init__.py", b"")
            z.writestr("real_inner.txt", b"inner content\n")
        fpsz = DDX.dir_fps(e1)
        assert any(v == "pack.zip!!real_inner.txt" for v in fpsz.values()), \
            "non-empty zip inner identity stays at full power"
        assert all(v != "pack.zip!!__init__.py" for v in fpsz.values()), \
            "empty zip inner entries carry no identity"
        # size-unknown listings keep the conservative old behavior
        hit4, _ = DDX.is_dup_blobs(
            {"blobs": {"dead" * 10: {"someone": True}}},
            [("x.bin", "dead" * 10, None)])
        assert hit4, "size-unknown listings must keep the conservative check"
        # schema generation: stale indexes self-heal on next touch
        assert DDX.FP_SCHEMA == 5 and DDX.BLOBS_SCHEMA == 4, \
            "schemas must carry the empty-file-exclusion generation"
        mod_results.append(True)
    except Exception:
        log("selftest evolve14-empty-file-identity failed:"); traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-15: single book + persisted ledger annotation ----
    try:
        # The adoption write path keeps ONE chain book (the canonical
        # version-chain.jsonl, rsi-075 guarded) and commits the ledger row
        # WITH its chain_entry annotation. Incumbent RED (this round, live
        # scratch replay): the disk row carried only ['history','id',
        # 'status','target','type'] -- the annotation was set in memory
        # AFTER save_ledger and never persisted, which fed the rsi-081
        # phantom-'ledger-bypass' audit; and the skill-doc mirror book
        # (arena/state/chain.jsonl) is hand-maintained because no code path
        # ever writes it (rsi-080 lost its row; rsi-081/082 needed manual
        # backfills) -- the mirror is retired by this round, canonical only.
        r2115 = os.path.join(scratch, "evolve15-singlebook-ledger")
        os.makedirs(os.path.join(r2115, "skills", "rsi"), exist_ok=True)
        os.makedirs(os.path.join(r2115, "skills", "arena", "state"),
                    exist_ok=True)
        cpath15 = R.chain_path(r2115)
        with open(cpath15, "w", encoding="utf-8") as f:
            f.write(json.dumps({"winner": "seed/v15", "pid": "rsi-900",
                                "date": "2026-11-17 00:00:00",
                                "chain_entry": 1}, ensure_ascii=False) + "\n")
        lpath15 = os.path.join(r2115, "skills", "rsi", "ledger.json")
        with open(lpath15, "w", encoding="utf-8") as f:
            json.dump({"proposals": [
                {"id": "rsi-921", "type": "measurement",
                 "target": "evolve15-green", "status": "proposed"}]}, f,
                ensure_ascii=False)
        R.transition("rsi-921", "adopted", root=r2115,
                     chain_fields={"winner": "seed/v15green"})
        # GREEN core: the ledger row ON DISK carries the chain_entry the
        # chain line just committed (incumbent RED: the key was absent)
        led15 = R.load_ledger(lpath15)
        row15 = [p for p in led15["proposals"] if p["id"] == "rsi-921"][0]
        assert int(row15.get("chain_entry") or 0) == 2, \
            "ledger row must persist chain_entry==2, got %r" % \
            row15.get("chain_entry")
        # single book: the only .jsonl under the root is the canonical chain
        written15 = []
        for base, _dirs, files15 in os.walk(r2115):
            for fn15 in files15:
                if fn15.endswith(".jsonl"):
                    written15.append(os.path.relpath(
                        os.path.join(base, fn15), r2115))
        assert written15 == [os.path.join("skills", "arena", "state",
                                          "version-chain.jsonl")], \
            "adoption must write ONLY the canonical chain book: %r" % \
            written15
        # re-adopt: history only -- chain unduplicated, annotation kept
        R.transition("rsi-921", "adopted", note="re-adopt", root=r2115)
        assert len(R.load_chain(cpath15)) == 2, \
            "re-adopt must not duplicate the chain line"
        led15b = R.load_ledger(lpath15)
        row15b = [p for p in led15b["proposals"] if p["id"] == "rsi-921"][0]
        assert int(row15b.get("chain_entry") or 0) == 2, \
            "re-adopt keeps the persisted annotation"
        assert len(row15b.get("history") or []) == 2, "history appended"
        # rollback: a ledger write failure must leave the annotation absent
        # from the disk row (it commits WITH the ledger, never before it)
        with open(lpath15, "w", encoding="utf-8") as f:
            json.dump({"proposals": [
                {"id": "rsi-922", "type": "measurement",
                 "target": "evolve15-rollback", "status": "proposed"}]}, f,
                ensure_ascii=False)
        before15c = open(cpath15, "rb").read()
        before15l = open(lpath15, "rb").read()
        _orig15 = R.save_ledger

        def _boom15(data, path):
            raise OSError("simulated ledger write failure")
        R.save_ledger = _boom15
        try:
            try:
                R.transition("rsi-922", "adopted", root=r2115)
                raise AssertionError("simulated failure must propagate")
            except OSError:
                pass
        finally:
            R.save_ledger = _orig15
        assert open(cpath15, "rb").read() == before15c, \
            "chain must roll back byte-identical when the ledger write fails"
        assert open(lpath15, "rb").read() == before15l, \
            "ledger must roll back byte-identical too"
        led15c = R.load_ledger(lpath15)
        row922 = [p for p in led15c["proposals"] if p["id"] == "rsi-922"][0]
        assert "chain_entry" not in row922 and "history" not in row922, \
            "failed adoption leaves the row untouched (annotation included)"
        # approve stays ledger-only and annotation-free
        with open(lpath15, "w", encoding="utf-8") as f:
            json.dump({"proposals": [
                {"id": "rsi-923", "type": "measurement",
                 "target": "evolve15-approve", "status": "proposed"}]}, f,
                ensure_ascii=False)
        R.transition("rsi-923", "approved", root=r2115)
        led15d = R.load_ledger(lpath15)
        row923 = [p for p in led15d["proposals"] if p["id"] == "rsi-923"][0]
        assert "chain_entry" not in row923, "approve never annotates"
        assert len(R.load_chain(cpath15)) == 2, "approve never chains"
        mod_results.append(True)
    except Exception:
        log("selftest evolve15-singlebook-ledger failed:")
        traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-16: durable candidate snapshots + rollback hash guard
    try:
        # Incumbent RED (live scratch replay this round): an adoption whose
        # rollback text claims a NEVER-COMPUTED placeholder sha256 commits to
        # the chain unseen -- the rsi-083 first-pass production errata; and
        # rollback snapshots parked under .temp/ are not retained (rsi-078/
        # 079 dirs already gone). GREEN: --candidate-file/--incumbent-file
        # make the CLI hash the round's REAL files, snapshot them durably
        # under skills/rsi/snapshots/<pid>/, guard every claimed sha256
        # against them BEFORE any ledger/chain write, and persist the
        # computed identity into the chain row (hand-typed same-named
        # fields lose: the chain row must describe what is on disk).
        r2116 = os.path.join(scratch, "evolve16-snapshot-guard")
        os.makedirs(os.path.join(r2116, "skills", "rsi"), exist_ok=True)
        os.makedirs(os.path.join(r2116, "skills", "arena", "state"),
                    exist_ok=True)
        cpath16 = R.chain_path(r2116)
        with open(cpath16, "w", encoding="utf-8") as f:
            f.write(json.dumps({"winner": "seed/v16", "pid": "rsi-900",
                                "date": "2026-11-17 00:00:00",
                                "chain_entry": 1}, ensure_ascii=False) + "\n")
        cand16 = os.path.join(r2116, "cand.py")
        inc16 = os.path.join(r2116, "inc.py")
        with open(cand16, "w", encoding="utf-8") as f:
            f.write("# candidate bytes v16\n")
        with open(inc16, "w", encoding="utf-8") as f:
            f.write("# incumbent bytes v16\n")
        cand_sha = R._sha256_file(cand16)
        inc_sha = R._sha256_file(inc16)
        lpath16 = os.path.join(r2116, "skills", "rsi", "ledger.json")
        with open(lpath16, "w", encoding="utf-8") as f:
            json.dump({"proposals": [
                {"id": "rsi-931", "type": "measurement",
                 "target": "evolve16-green", "status": "proposed"}]}, f,
                ensure_ascii=False)
        # hand-typed candidate_files must LOSE to the computed identity
        R.transition("rsi-931", "adopted", root=r2116,
                     chain_fields={"winner": "seed/v16green",
                                   "candidate_files": [{"sha256": "forged"}],
                                   "rollback": "restore sha256 %s %s"
                                               % (cand_sha, inc_sha)},
                     candidate_files=[cand16], incumbent_files=[inc16])
        row16 = R.load_chain(cpath16)[-1]
        assert [f["sha256"] for f in row16["candidate_files"]] == [cand_sha], \
            "chain candidate_files must be the CLI-computed hashes"
        assert row16["candidate_files"][0]["sha256"] != "forged", \
            "hand-typed candidate_files must never survive"
        assert row16["incumbent_files"][0]["sha256"] == inc_sha
        # durable snapshot bytes survive and live OUTSIDE any .temp path
        snap16 = os.path.join(r2116, R.SNAPSHOTS_REL, "rsi-931",
                              "candidate-cand.py")
        assert os.path.isfile(snap16), "durable snapshot must exist"
        assert open(snap16, "rb").read() == open(cand16, "rb").read(), \
            "snapshot must be byte-faithful"
        assert ".temp" not in row16["candidate_files"][0]["snapshot"], \
            "snapshots must never be parked under .temp"
        # GREEN guard refusal: a claimed sha256 matching nothing real
        with open(lpath16, "w", encoding="utf-8") as f:
            json.dump({"proposals": [
                {"id": "rsi-932", "type": "measurement",
                 "target": "evolve16-refuse", "status": "proposed"}]}, f,
                ensure_ascii=False)
        before16c = open(cpath16, "rb").read()
        before16l = open(lpath16, "rb").read()
        try:
            R.transition("rsi-932", "adopted", root=r2116,
                        chain_fields={"rollback": "placeholder sha256 " +
                                     "F" * 64},
                        candidate_files=[cand16])
            raise AssertionError("placeholder-hash adopt must be refused")
        except ValueError:
            pass
        assert open(cpath16, "rb").read() == before16c, \
            "refused adoption must leave the chain byte-identical"
        assert open(lpath16, "rb").read() == before16l, \
            "refused adoption must leave the ledger byte-identical"
        # idempotent re-adopt: history-only branch -- the chain line already
        # exists, so the snapshot face is not re-entered and no dup appears
        R.transition("rsi-931", "adopted", note="re-adopt", root=r2116,
                      candidate_files=[cand16])
        assert len(R.load_chain(cpath16)) == 2, \
            "re-adopt must not duplicate the chain line"
        # pid reuse with drifted bytes is forbidden (helper face: re-adopt
        # itself is history-only and never re-snapshots)
        with open(cand16, "a", encoding="utf-8") as f:
            f.write("# drifted\n")
        try:
            R.candidate_snapshot(r2116, "rsi-931", "candidate", [cand16])
            raise AssertionError("drifted same-pid snapshot must be refused")
        except ValueError:
            pass
        with open(cand16, "w", encoding="utf-8") as f:
            f.write("# candidate bytes v16\n")      # restore for later blocks
        # same-bytes re-snapshot is the idempotent skip (no raise)
        R.candidate_snapshot(r2116, "rsi-931", "candidate", [cand16])
        # opt-out keeps incumbent semantics: no guard, no injected fields
        with open(lpath16, "w", encoding="utf-8") as f:
            json.dump({"proposals": [
                {"id": "rsi-933", "type": "measurement",
                 "target": "evolve16-legacy", "status": "proposed"}]}, f,
                ensure_ascii=False)
        R.transition("rsi-933", "adopted", root=r2116,
                     chain_fields={"rollback": "legacy sha256 " + "A" * 64})
        row933 = R.load_chain(cpath16)[-1]
        assert "candidate_files" not in row933 and \
            "incumbent_files" not in row933, \
            "no files passed -> no computed fields, unguarded (legacy rows)"
        # approve/reject never snapshot or guard
        with open(lpath16, "w", encoding="utf-8") as f:
            json.dump({"proposals": [
                {"id": "rsi-934", "type": "measurement",
                 "target": "evolve16-approve", "status": "proposed"}]}, f,
                ensure_ascii=False)
        R.transition("rsi-934", "approved", root=r2116,
                     candidate_files=[cand16])
        assert not os.path.exists(os.path.join(
            r2116, R.SNAPSHOTS_REL, "rsi-934")), \
            "approve must never write snapshots"
        mod_results.append(True)
    except Exception:
        log("selftest evolve16-snapshot-guard failed:")
        traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-17] dedup self-heal dry-run: the would-be
        # rebuild must be previewable with ZERO writes. On the incumbent
        # the only engine path that computes the would-be index
        # (load_index on a stale-schema prior) rewrites the file in
        # place -- schema-bump rounds mutated production first and
        # hand-diffed copies aside (the rsi-081 manual-diff tax; RED
        # replayed this round on the incumbent scratch shape). GREEN:
        # self_heal_diff() computes the same would-be index through the
        # same _needs_rebuild() predicate, reports added/purged/kept per
        # section, and the index bytes stay untouched on every face
        # (missing / drift / current / forced); the mutating path keeps
        # its exact legacy behavior; preview counts equal the forced
        # rebuild's (the preview can never drift from the write path).
        from core import dedup as D17
        r2117 = os.path.join(scratch, "evolve17-dryrun")
        if os.path.isdir(r2117):
            shutil.rmtree(r2117)
        os.makedirs(os.path.join(r2117, "feed", "2029-fake__alpha"))
        os.makedirs(os.path.join(r2117, "feed", "2029-fake__beta"))
        os.makedirs(os.path.join(r2117, "wsroot"))
        os.makedirs(os.path.join(r2117, "learned"))
        with open(os.path.join(r2117, "feed", "2029-fake__alpha",
                               "a.bin"), "wb") as f:
            f.write(b"alpha-content-one")
        with open(os.path.join(r2117, "feed", "2029-fake__beta",
                               "b.bin"), "wb") as f:
            f.write(b"beta-content-two")
        saved17 = {k: getattr(D17, k) for k in (
            "INDEX_PATH", "DB_PATH", "LEGACY_JSON", "LEGACY_DIRS",
            "WS_ROOT", "LEARNED_DIR", "FEED_DIR")}
        try:
            D17.INDEX_PATH = os.path.join(r2117, "index.json")
            D17.DB_PATH = os.path.join(r2117, "nope.sqlite")
            D17.LEGACY_JSON = os.path.join(r2117, "nope-legacy.json")
            D17.LEGACY_DIRS = os.path.join(r2117, "feed")
            D17.WS_ROOT = os.path.join(r2117, "wsroot")
            D17.LEARNED_DIR = os.path.join(r2117, "learned")
            D17.FEED_DIR = os.path.join(r2117, "feed")
            # face 1: missing index -> rebuild previewed, file still absent
            rep17 = D17.self_heal_diff()
            assert rep17["will_rebuild"] is True and \
                rep17["reason"] == "missing-index", rep17
            assert rep17["fps"]["new"] == 2 and rep17["fps"]["added"] == 2, \
                rep17["fps"]
            assert not os.path.exists(D17.INDEX_PATH), \
                "dry-run must not create the index file"
            # face 2: stale schema + byproduct-style pollution key +
            # carried memory -> drift previewed, bytes untouched
            stale17 = {"gen": 111.0, "fp_schema": D17.FP_SCHEMA - 1,
                       "blobs_schema": D17.BLOBS_SCHEMA,
                       "pairs_schema": D17.PAIRS_SCHEMA, "counts": {},
                       "pairs": {},
                       "fps": {"d41d8cd98f00b204e9800998ecf8427e":
                               {"some-dir": True}},
                       "blobs": {}, "writeups_done": {"writeup-md5-1": True}}
            with open(D17.INDEX_PATH, "w", encoding="utf-8") as f:
                json.dump(stale17, f, ensure_ascii=False, indent=1)
            b17 = open(D17.INDEX_PATH, "rb").read()
            rep18 = D17.self_heal_diff()
            assert rep18["will_rebuild"] is True and \
                rep18["reason"] == "schema-drift", rep18
            assert rep18["fp_schema_prior"] == D17.FP_SCHEMA - 1 and \
                rep18["fp_schema_new"] == D17.FP_SCHEMA, rep18
            assert rep18["fps"]["prior"] == 1 and rep18["fps"]["new"] == 2 \
                and rep18["fps"]["added"] == 2 \
                and rep18["fps"]["purged"] == 1 \
                and rep18["fps"]["kept"] == 0, rep18["fps"]
            assert rep18["writeups_done_carried"] == 1, rep18
            assert open(D17.INDEX_PATH, "rb").read() == b17, \
                "dry-run must leave the index byte-identical"
            # face 3: current schema -> no rebuild; forced preview -> no write
            D17.load_index()                    # legacy mutating path: writes
            cur17 = open(D17.INDEX_PATH, "rb").read()
            rep19 = D17.self_heal_diff()
            assert rep19["will_rebuild"] is False and \
                rep19["reason"] == "schemas-current", rep19
            rep20 = D17.self_heal_diff(force=True)
            assert rep20["will_rebuild"] is True and \
                rep20["reason"] == "forced" and \
                rep20["fps"]["added"] == 0 and \
                rep20["fps"]["purged"] == 0, rep20
            assert open(D17.INDEX_PATH, "rb").read() == cur17, \
                "current/forced dry-runs must not touch the index"
            # parity: previewed NEW counts equal the forced rebuild's
            idx17, _p17 = D17.load_index(force=True)
            assert rep20["fps"]["new"] == len(idx17["fps"]) and \
                rep20["blobs"]["new"] == len(idx17["blobs"]) and \
                rep20["pairs"]["new"] == len(idx17["pairs"]), \
                "preview counts must equal the forced rebuild's"
        finally:
            for _k17, _v17 in saved17.items():
                setattr(D17, _k17, _v17)
        mod_results.append(True)
    except Exception:
        log("selftest evolve17-dryrun failed:")
        traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-18: adjudicate-db -- DB-row adjudication face --
    try:
        # The rsi-086 row-496 surgery was hand-executed because no CLI
        # face could reach a row whose dir holds no result file. This
        # block proves the durable face on a scratch DB: WAL checkpoint
        # -> byte-faithful backup next to the DB -> single-transaction
        # UPDATE of exactly one row -> verify-after-commit; attempts and
        # lessons are never touched; repeated adjudication only appends
        # audit blocks; the summary block is JSON-safe end to end; every
        # refusal path returns rc=2 without mutating anything.
        import sqlite3 as _sq18
        r2118 = os.path.join(scratch, "evolve18-dbddrow")
        if os.path.isdir(r2118):
            shutil.rmtree(r2118)
        os.makedirs(r2118)
        db18 = os.path.join(r2118, "trajectories.sqlite")
        c18 = _sq18.connect(db18)
        c18.execute("CREATE TABLE challenges (id INTEGER PRIMARY KEY "
                    "AUTOINCREMENT, name TEXT, solved INTEGER, flag TEXT, "
                    "summary TEXT)")
        c18.execute("CREATE TABLE attempts (id INTEGER PRIMARY KEY "
                    "AUTOINCREMENT, challenge_id INTEGER, detail TEXT)")
        c18.execute("CREATE TABLE lessons (id INTEGER PRIMARY KEY "
                    "AUTOINCREMENT, challenge_id INTEGER, technique TEXT)")
        c18.execute("INSERT INTO challenges (name, solved, flag, summary) "
                    "VALUES ('fake-leak', 1, 'flag{sm0ke}', 'orig line')")
        c18.execute("INSERT INTO attempts (challenge_id, detail) "
                    "VALUES (1, 'a1'), (1, 'a2')")
        c18.execute("INSERT INTO lessons (challenge_id, technique) "
                    "VALUES (1, 'chain-decode')")
        c18.commit()
        c18.close()
        pre18 = open(db18, "rb").read()
        old_env18 = os.environ.get("CTFPILOT_DB")
        try:
            os.environ["CTFPILOT_DB"] = db18
            # face 1: full surgery -- solve verdict flipped, block appended
            rc18 = do_adjudicate_db(_NS(
                db_row=1, true_status="NOT SOLVED (selftest fixture)",
                reason="leak \ufffd probe", by="", set_solved="0",
                set_flag="", field=["pid=selftest-evolve18"]), {})
            assert rc18 == 0, "adjudicate-db must succeed, rc=%r" % rc18
            post18 = _sq18.connect(db18)
            row18 = post18.execute("SELECT solved, flag, summary FROM "
                                   "challenges WHERE id=1").fetchone()
            assert row18[0] == 0 and row18[1] == "", row18[:2]
            tail18 = str(row18[2])
            b18 = json.loads(
                tail18[tail18.rfind('{"adjudication"'):])["adjudication"]
            assert b18["true_status"] == "NOT SOLVED (selftest fixture)" \
                and b18["original_solved"] == 1 \
                and b18["original_flag"] == "flag{sm0ke}" \
                and b18["pid"] == "selftest-evolve18", b18
            assert "leak \ufffd probe" == b18["reason"], \
                "U+FFFD must survive the round trip (rsi-066 lesson)"
            assert tail18.startswith("orig line\n{"), repr(tail18[:40])
            # backup: byte-faithful pre-surgery snapshot next to the DB
            bks18 = [f for f in os.listdir(r2118)
                     if f.startswith("trajectories.pre-adjuddb-1-")]
            assert len(bks18) == 1, "exactly one pre-surgery backup: %r" % \
                bks18
            assert open(os.path.join(r2118, bks18[0]), "rb").read() \
                == pre18, "backup must be byte-identical to pre-surgery DB"
            # dependents untouched
            assert post18.execute("SELECT count(*) FROM attempts"). \
                fetchone()[0] == 2
            assert post18.execute("SELECT count(*) FROM lessons"). \
                fetchone()[0] == 1
            # face 2: refusal paths mutate nothing
            n18 = len([f for f in os.listdir(r2118)
                       if f.startswith("trajectories.pre-adjuddb-")])
            assert do_adjudicate_db(_NS(db_row=999, true_status="T",
                                        reason="R"), {}) == 2, \
                "unknown row id must refuse"
            assert do_adjudicate_db(_NS(db_row=1, true_status="",
                                        reason="R"), {}) == 2, \
                "missing true-status must refuse"
            assert do_adjudicate_db(_NS(db_row=1, true_status="T",
                                        reason=""), {}) == 2, \
                "missing reason must refuse"
            assert do_adjudicate_db(_NS(db_row=1, true_status="T",
                                        reason="R", field=["novalue"]), {}) \
                == 2, "field without = must refuse"
            assert len([f for f in os.listdir(r2118)
                        if f.startswith("trajectories.pre-adjuddb-")]) \
                == n18, "refusals must not leave stray backups"
            # face 3: repeat adjudication appends a second audit block,
            # originals re-captured from the CURRENT values, row stable
            rc18b = do_adjudicate_db(_NS(
                db_row=1, true_status="CONFIRMED NOT SOLVED", reason="re-audit",
                set_solved="0", set_flag=""), {})
            assert rc18b == 0, "repeat adjudication must succeed"
            row18b = post18.execute("SELECT solved, flag, summary FROM "
                                    "challenges WHERE id=1").fetchone()
            assert row18b[0] == 0 and row18b[1] == "", row18b[:2]
            tail18b = str(row18b[2])
            blocks18 = [m.start() for m in
                        __import__("re").finditer('{"adjudication"',
                                                   tail18b)]
            assert len(blocks18) == 2, "audit trail only ever appends"
            b18b = json.loads(tail18b[blocks18[-1]:])["adjudication"]
            assert b18b["true_status"] == "CONFIRMED NOT SOLVED" \
                and b18b["original_solved"] == 0 \
                and b18b["original_flag"] == "", b18b
            post18.close()
        finally:
            if old_env18 is None:
                os.environ.pop("CTFPILOT_DB", None)
            else:
                os.environ["CTFPILOT_DB"] = old_env18
        mod_results.append(True)
    except Exception:
        log("selftest evolve18-dbddrow failed:")
        traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-23: adjudicate-db corrupt-tail parity ------------
    try:
        # Parity with the dir face: do_adjudicate REFUSES a corrupt result
        # file, so adjudicate-db must refuse a summary whose LAST line
        # opens an adjudication block that does not parse -- before any
        # checkpoint/backup/UPDATE (rsi-092 finding (c); RED replay on the
        # incumbent: rc=0, corrupt fragment buried under a fresh block).
        # Valid old blocks, prose mentions and empty summaries proceed.
        import sqlite3 as _sq23
        r2323 = os.path.join(scratch, "evolve23-adjudsanity")
        if os.path.isdir(r2323):
            shutil.rmtree(r2323)
        os.makedirs(r2323)
        db23 = os.path.join(r2323, "trajectories.sqlite")
        c23 = _sq23.connect(db23)
        c23.execute("CREATE TABLE challenges (id INTEGER PRIMARY KEY "
                    "AUTOINCREMENT, name TEXT, solved INTEGER, flag TEXT, "
                    "summary TEXT)")
        c23.execute("CREATE TABLE attempts (id INTEGER PRIMARY KEY "
                    "AUTOINCREMENT, challenge_id INTEGER, detail TEXT)")
        c23.execute("CREATE TABLE lessons (id INTEGER PRIMARY KEY "
                    "AUTOINCREMENT, challenge_id INTEGER, technique TEXT)")
        c23.execute("INSERT INTO attempts (challenge_id, detail) "
                    "VALUES (1, 'a1')")
        c23.execute("INSERT INTO lessons (challenge_id, technique) "
                    "VALUES (1, 'chain-decode')")
        c23.commit()
        c23.close()
        old_env23 = os.environ.get("CTFPILOT_DB")

        def _seed23(s):
            cc = _sq23.connect(db23)
            cc.execute("DELETE FROM challenges")
            cc.execute("INSERT INTO challenges (id, name, solved, flag, "
                       "summary) VALUES (1, 'probe', 1, 'flag{probe}', ?)",
                       (s,))
            cc.commit()
            cc.close()

        def _sum23():
            cc = _sq23.connect(db23)
            v = cc.execute("SELECT summary FROM challenges WHERE id=1") \
                .fetchone()
            cc.close()
            return "" if v is None or v[0] is None else str(v[0])

        def _bks23():
            return [f for f in os.listdir(r2323)
                    if f.startswith("trajectories.pre-adjuddb-")]

        try:
            os.environ["CTFPILOT_DB"] = db23
            # (A) REFUSE: truncated trailing block -- no mutation, no backup
            _seed23('orig line\n{"adjudication": {"true_status": "old')
            pre23 = open(db23, "rb").read()
            n23 = len(_bks23())
            rc23 = do_adjudicate_db(_NS(db_row=1, true_status="RETEST",
                                        reason="parity probe"), {})
            assert rc23 == 2, "corrupt tail must refuse, rc=%r" % rc23
            assert open(db23, "rb").read() == pre23, \
                "refusal must not mutate the DB"
            assert len(_bks23()) == n23, "refusal must not write a backup"
            assert _sum23().endswith('{"true_status": "old'), _sum23()
            # (A2) REFUSE: parseable JSON, adjudication value not an object
            _seed23('orig\n{"adjudication": "nope"}')
            pre23b = open(db23, "rb").read()
            assert do_adjudicate_db(_NS(db_row=1, true_status="T",
                                        reason="R"), {}) == 2
            assert open(db23, "rb").read() == pre23b, \
                "non-object adjudication must refuse without mutation"
            # (B) VALID old block proceeds; audit trail only appends
            _seed23('orig line\n{"adjudication": {"true_status": "OLD"}}')
            assert do_adjudicate_db(_NS(db_row=1, true_status="NEW",
                                        reason="R"), {}) == 0
            t23 = _sum23()
            assert t23.startswith('orig line\n{"adjudication": '
                                  '{"true_status": "OLD"}}'), repr(t23)
            poss23 = [m.start() for m in __import__("re").finditer(
                '{"adjudication"', t23)]
            assert len(poss23) == 2, "old block kept, new block appended"
            assert json.loads(t23[poss23[-1]:])["adjudication"] \
                ["true_status"] == "NEW"
            # (C) prose mention on the last line is not a block opener
            _seed23('the tool writes {"adjudication": {...}} blocks.'
                    '\nsecond line')
            assert do_adjudicate_db(_NS(db_row=1, true_status="P",
                                        reason="R"), {}) == 0
            assert _sum23().startswith("the tool writes"), _sum23()
            # (D) empty summary proceeds (legacy behaviour unchanged)
            _seed23("")
            assert do_adjudicate_db(_NS(db_row=1, true_status="E",
                                        reason="R"), {}) == 0
            assert _sum23().startswith('{"adjudication"'), _sum23()
            # dependents are never touched by any face above
            cc = _sq23.connect(db23)
            assert cc.execute("SELECT count(*) FROM attempts"). \
                fetchone()[0] == 1
            assert cc.execute("SELECT count(*) FROM lessons"). \
                fetchone()[0] == 1
            cc.close()
        finally:
            if old_env23 is None:
                os.environ.pop("CTFPILOT_DB", None)
            else:
                os.environ["CTFPILOT_DB"] = old_env23
        mod_results.append(True)
    except Exception:
        log("selftest evolve23-adjudsanity failed:")
        traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-19: router archive-member pwn-carrier vote -----
    try:
        # RED (live, this feed round): 2023-ISCC-Group-stage-match-PWN私地附件
        # routed misc conf=0.75 on zip magic/ext alone -- a remote-pwn
        # attachment archive (register ELF + libc.so.6) is invisible to
        # every observation face. GREEN: small archive (<=12 members)
        # carrying BOTH a libc/ld shared object AND a non-lib ELF binary
        # votes pwn weight 4 (clears the archive's own misc 2+1 outright);
        # every guard below has a live fixture asserting it.
        import zipfile as _zf19, tarfile as _tf19, io as _io19
        from core.router import (route as _rt19, _archive_members as _am19,
                                 _member_is_elf as _me19,
                                 ARCHIVE_MEMBER_CAP as _cap19)
        r19 = os.path.join(scratch, "evolve19-archive-member-pwn")
        os.makedirs(r19, exist_ok=True)
        ELF19 = b"\x7fELF\x02\x01\x01\x00" + b"\x41" * 32
        LIB19 = b"\x7fELF\x02\x01\x01\x00" + b"\x4c" * 32
        def _mkd19(sub):
            d = os.path.join(r19, sub)
            os.makedirs(d, exist_ok=True)
            return d
        # (A) positive: binary ELF + libc.so.6 in a zip -> pwn
        dA = _mkd19("pos-pwn-zip")
        with _zf19.ZipFile(os.path.join(dA, "attach.zip"), "w") as z:
            z.writestr("register", ELF19)
            z.writestr("libc.so.6", LIB19)
            z.writestr("README.md", b"pwn\n")
        rtA = _rt19(dA)
        assert rtA["category"] == "pwn", \
            "pwn carrier zip must route pwn: %r %r" % (rtA["category"], rtA["evidence"])
        assert any("[pwn] carrier" in e for e in rtA["evidence"]), rtA["evidence"]
        # (B) negative: misc zip (no lib member) -> stays misc
        dB = _mkd19("neg-misc-zip")
        with _zf19.ZipFile(os.path.join(dB, "data.zip"), "w") as z:
            z.writestr("readme.txt", b"misc\n")
            z.writestr("photo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        assert _rt19(dB)["category"] == "misc", "plain misc zip must stay misc"
        # (C) negative: reverse crackme zip (ELF member, NO lib) -> stays
        # misc this round (single-variable scope; separate candidate)
        dC = _mkd19("neg-reverse-zip")
        with _zf19.ZipFile(os.path.join(dC, "crackme.zip"), "w") as z:
            z.writestr("crackme.elf", ELF19)
        assert _rt19(dC)["category"] == "misc", \
            "lib-less ELF zip stays misc (documented scope)"
        # (D) negative: lib dump over the member cap (40 libs) -> stays misc
        dD = _mkd19("neg-libdump-zip")
        with _zf19.ZipFile(os.path.join(dD, "libdump.zip"), "w") as z:
            for i in range(40):
                z.writestr("lib/lib%d.so" % i, LIB19)
        assert _rt19(dD)["category"] == "misc", "member-cap guard must hold"
        # (E) negative: pure-lib zip (libc+ld only, no non-lib ELF) -> misc
        dE = _mkd19("neg-purelib-zip")
        with _zf19.ZipFile(os.path.join(dE, "libs.zip"), "w") as z:
            z.writestr("libc.so.6", LIB19)
            z.writestr("ld-linux-x86-64.so.2", LIB19)
        assert _rt19(dE)["category"] == "misc", "lib-only archive is not a pwn carrier"
        # (F) positive: tar.gz pwn carrier -> pwn
        dF = _mkd19("pos-pwn-targz")
        with _tf19.open(os.path.join(dF, "arch.tar.gz"), "w:gz") as t:
            for mn, data in [("vuln_bin", ELF19), ("libc.so.6", LIB19)]:
                ti = _tf19.TarInfo(mn); ti.size = len(data)
                t.addfile(ti, _io19.BytesIO(data))
        assert _rt19(dF)["category"] == "pwn", "tar.gz pwn carrier must route pwn"
        # (G) negative: corrupted zip must not crash and stays misc
        dG = _mkd19("neg-corrupt-zip")
        with open(os.path.join(dG, "broken.zip"), "wb") as f:
            f.write(b"PK\x03\x04" + b"\x00" * 37)
        assert _rt19(dG)["category"] == "misc", "corrupted carrier unchanged"
        # (H) negative: lib-named member present but the 'binary' member's
        # content is NOT ELF -> the content check must refuse the vote
        dH = _mkd19("neg-fakeelf-zip")
        with _zf19.ZipFile(os.path.join(dH, "fake.zip"), "w") as z:
            z.writestr("register", b"not an elf" + b"\x00" * 16)
            z.writestr("libc.so.6", LIB19)
        assert _rt19(dH)["category"] == "misc", \
            "content check must refuse non-ELF binary members"
        # (I) helper faces degrade to empty on unreadable input
        assert _am19(os.path.join(dG, "broken.zip"), "zip") == [], \
            "corrupted zip member list degrades to empty"
        assert not _me19(os.path.join(dG, "broken.zip"), "whatever", "zip")
        assert _cap19 == 12, "member cap literal"
        mod_results.append(True)
    except Exception:
        log("selftest evolve19-archive-member-pwn failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-20] deep-loop provenance gate: an env placeholder
        # flag (docker-compose FLAG literal) is a raw echo; it must be refused
        # INSIDE the hypothesis loop and the loop must keep its remaining
        # budget. Before this round the loop returned the placeholder at step 0,
        # do_solve retracted it afterwards and the solve ended -- every
        # docker-shipping challenge lost its chance at a derived flag that a
        # later strategy (archive unpack) could find.
        import zipfile as _zf20
        d20 = os.path.join(scratch, "evolve20-deepflag")
        os.makedirs(d20, exist_ok=True)
        with open(os.path.join(d20, "docker-compose.yml"), "w", encoding="utf-8") as f20:
            f20.write("services:\n  test:\n    environment:\n"
                      "      FLAG: \"flag{placehold3r}\"\n")
        with open(os.path.join(d20, "notes.txt"), "w", encoding="utf-8") as f20:
            f20.write("seat notes\n" * 20)
        with _zf20.ZipFile(os.path.join(d20, "payload.bin"), "w",
                           _zf20.ZIP_DEFLATED) as z20:
            z20.writestr("secret.txt", "internal: flag{deep_l00p_cont1nues}\n")
        rc20 = do_solve(_NS(dir=d20, mode="auto", brain="mock",
                            name="evolve20-deepflag", flag_regex=None,
                            write_trace=False, allow_replay=True), cfg)
        assert rc20 == 0, "deep loop must continue past the raw-echo placeholder"
        row20 = get_stores(cfg).prior_attempt("evolve20-deepflag")
        assert row20 and row20["solved"] == 1, "derived flag must be credited"
        assert row20["flag"] == "flag{deep_l00p_cont1nues}", "derived flag value"
        mod_results.append(True)
    except Exception:
        log("selftest evolve20-deepflag failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-21 / rsi-091] legacy _extract residue pruning:
        # the inventory face pruned only the modern _extracted/ name, so a
        # pre-rename extraction dir fed every observation face as if it
        # were original attachments (live: 2023-Bluehat-Quals-pwn
        # _extract/heapSpary/{libc.so.6,main} + _extract/takeway/takeway;
        # do_solve feeds routing["files"] to both channels). The flagcheck
        # face had the same hole one layer down: a flag string in OLD-run
        # residue counted as "verbatim in the original files" and would
        # falsely retract a legitimate derived solve.
        from core.router import inventory as _inv21
        from core.flagcheck import flag_in_raw as _fr21
        r21 = os.path.join(scratch, "evolve21-extract-residue")
        d21 = os.path.join(r21, "residue-dir")
        os.makedirs(os.path.join(d21, "_extract"), exist_ok=True)
        os.makedirs(os.path.join(d21, "_extracted"), exist_ok=True)
        with open(os.path.join(d21, "task.txt"), "w", encoding="utf-8") as f21:
            f21.write("original task file\n")
        with open(os.path.join(d21, "extract_notes.txt"), "w",
                  encoding="utf-8") as f21:
            f21.write("real attachment, name-similar, must stay\n")
        with open(os.path.join(d21, "_extract", "old_flag.txt"), "w",
                  encoding="utf-8") as f21:
            f21.write("flag{legacy_residue}\n")
        with open(os.path.join(d21, "_extracted", "fresh.txt"), "w",
                  encoding="utf-8") as f21:
            f21.write("flag{modern_byproduct}\n")
        pats21 = sorted(f21x["path"].replace("\\", "/")
                        for f21x in _inv21(d21))
        assert "_extract/old_flag.txt" not in pats21, \
            "legacy residue must be pruned from inventory: %r" % pats21
        assert "_extracted/fresh.txt" not in pats21, \
            "modern byproduct stays pruned: %r" % pats21
        assert "task.txt" in pats21 and "extract_notes.txt" in pats21, \
            "real attachments survive (exact-name discipline): %r" % pats21
        assert _fr21(d21, "flag{legacy_residue}") == (False, None), \
            "legacy residue is exempt from raw-echo"
        assert _fr21(d21, "flag{modern_byproduct}") == (False, None), \
            "modern byproduct stays exempt"
        with open(os.path.join(d21, "task.txt"), "a", encoding="utf-8") as f21:
            f21.write("embedded: flag{real_original}\n")
        hit21 = _fr21(d21, "flag{real_original}")
        assert hit21[0] is True and hit21[1] == "task.txt", \
            "real original raw-echo face intact: %r" % (hit21,)
        mod_results.append(True)
    except Exception:
        log("selftest evolve21-extract-residue failed:"); traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-22: carrier vote vs multi-archive misc weight ---
    try:
        # RED (rsi-091 pool diff, live): 2023-Bluehat-Quals-pwn ships two
        # zips, one a pwn carrier (libc+main) and one opaque -- misc 6
        # (2 archives x magic+ext) beat the single carrier's pwn 4, so an
        # honestly-pwn directory routed misc once the residue votes were
        # pruned. A carrier archive's opaque misc votes are superseded by
        # its own members (per-file drop, magic 2 + ext 1); other archives
        # keep theirs.
        import zipfile as _zf22, tarfile as _tf22, io as _io22
        from core.router import route as _rt22, \
            PWN_CARRIER_VOTE as _pcv22
        r22 = os.path.join(scratch, "evolve22-multi-carrier")
        ELF22 = b"\x7fELF\x02\x01\x01\x00" + b"\x41" * 32
        LIB22 = b"\x7fELF\x02\x01\x01\x00" + b"\x4c" * 32

        def _mkd22(sub):
            d = os.path.join(r22, sub)
            os.makedirs(d, exist_ok=True)
            return d

        def _carrier_zip22(d, name):
            with _zf22.ZipFile(os.path.join(d, name), "w") as z:
                z.writestr("vuln_bin", ELF22)
                z.writestr("libc.so.6", LIB22)

        # (A) RED shape: carrier + opaque zip in one dir -> pwn
        dA = _mkd22("multi-carrier-plus-opaque")
        _carrier_zip22(dA, "heapSpary.zip")
        with _zf22.ZipFile(os.path.join(dA, "takeway.zip"), "w") as z:
            z.writestr("notes.txt", b"takeaway\n")
        rtA = _rt22(dA)
        assert rtA["category"] == "pwn", rtA
        assert sum("[misc]" in e for e in rtA["evidence"]) == 4, \
            "both archives' misc evidence lines stay listed: %r" % \
            rtA["evidence"]
        drop22 = [e for e in rtA["evidence"] if "own misc votes -3" in e]
        assert len(drop22) == 1 and "heapSpary.zip" in drop22[0], \
            "only the carrier's own votes are dropped: %r" % rtA["evidence"]
        # (B) single carrier: pwn conf rises from 0.50 to 0.80
        dB = _mkd22("single-carrier")
        _carrier_zip22(dB, "attach.zip")
        rtB = _rt22(dB)
        assert rtB["category"] == "pwn" and rtB["confidence"] >= 0.75, rtB
        # (C) two carriers: pwn, both drops recorded
        dC = _mkd22("double-carrier")
        _carrier_zip22(dC, "one.zip")
        _carrier_zip22(dC, "two.zip")
        rtC = _rt22(dC)
        assert rtC["category"] == "pwn", rtC
        assert sum("own misc votes -3" in e for e in rtC["evidence"]) == 2, \
            rtC["evidence"]
        # (D) opaque-only zips keep the old misc route
        dD = _mkd22("opaque-only")
        for n22 in ("a.zip", "b.zip"):
            with _zf22.ZipFile(os.path.join(dD, n22), "w") as z:
                z.writestr("readme.txt", b"misc\n")
        assert _rt22(dD)["category"] == "misc"
        # (E) carrier + loose README: the README's ext vote is NOT dropped
        dE = _mkd22("carrier-plus-loose-readme")
        _carrier_zip22(dE, "attach.zip")
        with open(os.path.join(dE, "README.md"), "w",
                  encoding="utf-8") as f22:
            f22.write("pwn challenge\n")
        rtE = _rt22(dE)
        assert rtE["category"] == "pwn", rtE
        assert "[misc] ext=.md" in rtE["evidence"], rtE["evidence"]
        # (F) tar.gz carrier: magic gzip + ext .gz are dropped too
        dF = _mkd22("targz-carrier")
        with _tf22.open(os.path.join(dF, "arch.tar.gz"), "w:gz") as t:
            for mn, data in [("vuln_bin", ELF22), ("libc.so.6", LIB22)]:
                ti = _tf22.TarInfo(mn)
                ti.size = len(data)
                t.addfile(ti, _io22.BytesIO(data))
        rtF = _rt22(dF)
        assert rtF["category"] == "pwn", rtF
        assert any("own misc votes -3" in e for e in rtF["evidence"]), \
            rtF["evidence"]
        # (G) plain .tar carrier: no magic/ext misc votes exist -> -0
        dG = _mkd22("tar-carrier")
        with _tf22.open(os.path.join(dG, "arch.tar"), "w") as t:
            for mn, data in [("vuln_bin", ELF22), ("libc.so.6", LIB22)]:
                ti = _tf22.TarInfo(mn)
                ti.size = len(data)
                t.addfile(ti, _io22.BytesIO(data))
        rtG = _rt22(dG)
        assert rtG["category"] == "pwn", rtG
        assert any("own misc votes -0" in e for e in rtG["evidence"]), \
            rtG["evidence"]
        # (H) member cap + ELF content guards still gate the vote
        dH = _mkd22("oversize-carrier")
        with _zf22.ZipFile(os.path.join(dH, "dump.zip"), "w") as z:
            z.writestr("vuln_bin", ELF22)
            z.writestr("libc.so.6", LIB22)
            for i22 in range(12):
                z.writestr("extra%d.txt" % i22, b"x")
        assert _rt22(dH)["category"] == "misc", "member cap must still gate"
        dI = _mkd22("fake-elf")
        with _zf22.ZipFile(os.path.join(dI, "fake.zip"), "w") as z:
            z.writestr("vuln_bin", b"not an elf")
            z.writestr("libc.so.6", LIB22)
        assert _rt22(dI)["category"] == "misc", \
            "ELF content check must still gate"
        assert _pcv22 == 4, "carrier vote literal"
        mod_results.append(True)
    except Exception:
        log("selftest evolve22-multi-carrier failed:"); traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-24: xor_sweep_single table-driven decode ---------
    try:
        # RED (live, this feed round): 2023-Bluehat-Quals-rank 412715 B burned
        # 12.244 s of the 24.67 s solve in the single-byte sweep alone (255
        # keys x 412 KB of Python-level byte XOR). The candidate decodes with
        # precomputed bytes.translate tables and samples only the 512 bytes
        # _looks_meaningful actually reads. This block pins the contract:
        # byte-identical output vs the reference loop on the interesting
        # boundaries, same key order, same types, and the head-only
        # printability rule (a payload whose first 512 bytes are noise is
        # skipped exactly as before even if later bytes are printable).
        from fastpath.detectors import (_XOR_BYTE_TABLES, _looks_meaningful,
                                        xor_sweep_single)

        def _ref24(data):
            r = []
            for k24 in range(1, 256):
                d24 = bytes(b ^ k24 for b in data)
                if _looks_meaningful(d24):
                    r.append((k24, d24))
            return r

        cases24 = [b"", b"A", b"x" * 511, b"x" * 512, b"x" * 513,
                   bytes(range(256)) * 3,
                   bytes((i * 37 + 11) & 0xFF for i in range(4096)),
                   b"CTF fixture plaintext with flag{ok} inside. " * 40]
        for ci24, c24 in enumerate(cases24):
            ref24 = _ref24(c24)
            got24 = list(xor_sweep_single(c24))
            assert [k for k, _ in ref24] == [k for k, _ in got24], \
                "case %d key order differs: %s vs %s" % (
                    ci24, [k for k, _ in ref24][:5], [k for k, _ in got24][:5])
            assert all(a == b for (_, a), (_, b) in zip(ref24, got24)), \
                "case %d decoded bytes differ" % ci24
            assert all(isinstance(d, bytes) for _, d in got24), \
                "case %d must yield bytes" % ci24
        # (A) the identity table is never swept (key 0 means no transform)
        assert _XOR_BYTE_TABLES[0] == bytes(range(256))
        assert all(_XOR_BYTE_TABLES[k][i] == (i ^ k)
                   for k in (1, 127, 255) for i in (0, 1, 65, 255)), \
            "table[k][i] must equal i ^ k"
        assert len(_XOR_BYTE_TABLES) == 256 and \
            all(len(t) == 256 for t in _XOR_BYTE_TABLES)
        assert all(k != 0 for k, _ in xor_sweep_single(b"hello world")), \
            "key 0 must never be yielded"
        # (B) a printable payload must surface under its own key...
        plain24 = b"payload line for the sweep\n" * 20
        hit24 = [(k, d) for k, d in xor_sweep_single(plain24)]
        assert any(k == 0x42 and d == bytes(b ^ 0x42 for b in plain24)
                   for k, d in hit24), "0x42 decode must be yielded intact"
        # ...and the printable head rule stays the only filter
        noisy24 = bytes(range(200)) + b"A" * 4000
        assert not any(d == bytes(b ^ 0x42 for b in noisy24)
                       for k, d in xor_sweep_single(noisy24)), \
            "noise head must skip the key exactly like the reference"
        # (C) bytearray input is normalised, output stays bytes
        ba24 = bytearray(b"bytearray payload for the sweep\n" * 8)
        got_ba = list(xor_sweep_single(ba24))
        assert all(isinstance(d, bytes) for _, d in got_ba)
        assert [k for k, _ in got_ba] == [k for k, _ in
                                          _ref24(bytes(ba24))]
        # (D) the live shape: 412 KB blob must decode orders of magnitude
        # faster than the reference loop and yield the same key set
        blob24 = (b"CTF archive leaderboard dump row. " * 12000)[:412000]
        import time as _t24
        t24 = _t24.perf_counter()
        fast_keys = [k for k, _ in xor_sweep_single(blob24)]
        fast_s = _t24.perf_counter() - t24
        t24 = _t24.perf_counter()
        ref_keys = [k for k, _ in _ref24(blob24)]
        ref_s = _t24.perf_counter() - t24
        assert fast_keys == ref_keys, "live-shape key set differs"
        assert fast_s < ref_s / 3.0, \
            "table decode must win by >3x (fast=%.3fs ref=%.3fs)" % (
                fast_s, ref_s)
        mod_results.append(True)
    except Exception:
        log("selftest evolve24-xor-table failed:"); traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-25: caesar_sweep table-driven decode --------------
    try:
        # RED (live, this feed round): after rsi-094 the caesar sweep is the
        # largest fastpath cost on text inputs -- 2023-Bluehat-Quals-rank
        # 总榜.txt burns 5.884 s (781638 charged chars: whole-file 2.967 s +
        # 105 per-line 2.917 s at ~133 k chars/s) and the qwbs10 README adds
        # 0.081 s; the text stages stay budget-unbounded, so this round only
        # moves the decode into C (no budget/verdict change is claimed). This
        # block pins the contract: byte-identical output vs the reference
        # char loop on the interesting boundaries, same shift order, str
        # types, ascii-only table shape, and surrogate/non-ASCII round-trips.
        from fastpath.detectors import _CAESAR_SHIFT_TABLES, caesar_sweep

        def _ref25(text):
            r = []
            for s25 in range(1, 26):
                out = []
                for ch in text:
                    if "a" <= ch <= "z":
                        out.append(chr((ord(ch) - 97 + s25) % 26 + 97))
                    elif "A" <= ch <= "Z":
                        out.append(chr((ord(ch) - 65 + s25) % 26 + 65))
                    else:
                        out.append(ch)
                r.append((s25, "".join(out)))
            return r

        cases25 = [
            "", "a", "A", "z", "Z", "aZzA019 .,!?\t\r\n",
            "flag{ok} shift me -- MiXeD CaSe, 0189 punct.",
            "accents \u00e9\u00fc\u00df CJK \u6c49\u5b57 replaced \ufffd tail",
            "lone surrogates \ud800\udc00 must pass through untouched",
            "x" * 511, "x" * 512, "x" * 513,
            ("QWERTYqwerty 0189 .,! " * 300),
        ]
        for ci25, c25 in enumerate(cases25):
            ref25 = _ref25(c25)
            got25 = caesar_sweep(c25)
            assert [s for s, _ in ref25] == [s for s, _ in got25], \
                "case %d shift order differs" % ci25
            assert len(got25) == 25, "case %d must return 25 shifts" % ci25
            assert all(a == b for (_, a), (_, b) in zip(ref25, got25)), \
                "case %d decodes differ" % ci25
            assert all(isinstance(d, str) for _, d in got25), \
                "case %d must yield str" % ci25
        # (A) table shape: ascii letters only, identity elsewhere; index 0
        # is the identity row and is never swept (mirrors xor key 0)
        assert len(_CAESAR_SHIFT_TABLES) == 26 and \
            all(len(t) == 256 for t in _CAESAR_SHIFT_TABLES)
        assert _CAESAR_SHIFT_TABLES[0] == bytes(range(256)), \
            "shift 0 must be identity"
        for s25 in (1, 13, 25):
            tb = _CAESAR_SHIFT_TABLES[s25]
            for i in (0, 10, 32, 64, 91, 96, 123, 128, 200, 255):
                if not (65 <= i <= 90 or 97 <= i <= 122):
                    assert tb[i] == i, "non-letter byte must be identity"
            assert tb[97] == 97 + s25 % 26, "'a' must shift"
            assert tb[122] == 97 + (25 + s25) % 26, "'z' must wrap"
            assert tb[65] == 65 + s25 % 26 and \
                tb[90] == 65 + (25 + s25) % 26, "A-Z must wrap the same way"
        # (B) the live shape: 412 KB text decodes >3x faster than the
        # reference loop and byte-identically
        blob25 = ("CTF archive leaderboard dump row. " * 12000)[:412000]
        import time as _t25
        t25 = _t25.perf_counter()
        fast25 = caesar_sweep(blob25)
        fast_s = _t25.perf_counter() - t25
        t25 = _t25.perf_counter()
        ref25 = _ref25(blob25)
        ref_s = _t25.perf_counter() - t25
        assert fast25 == ref25, "live-shape decodes differ"
        assert fast_s < ref_s / 3.0, \
            "table decode must win by >3x (fast=%.3fs ref=%.3fs)" % (
                fast_s, ref_s)
        mod_results.append(True)
    except Exception:
        log("selftest evolve25-caesar-table failed:"); traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-26: atbash table-driven decode ---------------------
    try:
        # RED (live, this feed round): after rsi-094/095 the chain decoder is
        # the dominant text cost and atbash is its largest pure-Python loop --
        # 2023-Bluehat-Quals-rank 1.678 s = 56.6% of chain (848 calls,
        # 6.25 M decode chars); 2023-ISCC-Analysis 5.223 s = 24.1% (230 k
        # calls, 16.37 M chars, ~3.9 M chars/s). Text stages stay budget-
        # unbounded, so this round only moves the mirror into C (no
        # budget/verdict change is claimed). This block pins the contract:
        # byte-identical output vs the reference char loop on the interesting
        # boundaries, str types, ascii-only table shape, involution, and
        # surrogate/non-ASCII round-trips.
        from fastpath.detectors import _ATBASH_TABLE, atbash

        def _ref26(text):
            out = []
            for ch in text:
                if "a" <= ch <= "z":
                    out.append(chr(219 - ord(ch)))
                elif "A" <= ch <= "Z":
                    out.append(chr(155 - ord(ch)))
                else:
                    out.append(ch)
            return "".join(out)

        cases26 = [
            "", "a", "A", "z", "Z", "abcXYZ", "aZzA019 .,!?\t\r\n",
            "flag{ok} mirror me -- MiXeD CaSe, 0189 punct.",
            "accents \u00e9\u00fc\u00df CJK \u6c49\u5b57 replaced \ufffd tail",
            "lone surrogates \ud800\udc00 must pass through untouched",
            "x" * 511, "x" * 512, "x" * 513,
            ("QWERTYqwerty 0189 .,! " * 300),
        ]
        for ci26, c26 in enumerate(cases26):
            got26 = atbash(c26)
            assert isinstance(got26, str), "case %d must return str" % ci26
            assert got26 == _ref26(c26), "case %d decode differs" % ci26
        # (A) table shape: ascii letters only, identity elsewhere; the mirror
        # is an involution on ASCII letters (atbash(atbash(s)) == s)
        assert len(_ATBASH_TABLE) == 256, "table must be 256 bytes"
        for i in (0, 10, 32, 64, 91, 96, 123, 128, 200, 255):
            if not (65 <= i <= 90 or 97 <= i <= 122):
                assert _ATBASH_TABLE[i] == i, "non-letter byte %d must be identity" % i
        assert _ATBASH_TABLE[97] == 122 and _ATBASH_TABLE[122] == 97, "'a'/'z' mirror"
        assert _ATBASH_TABLE[65] == 90 and _ATBASH_TABLE[90] == 65, "'A'/'Z' mirror"
        for s26 in ("", "aZzA019 .,!?", "flag{involutive}",
                    "accents \u00e9\u00fc\u00df \u6c49\u5b57 \ufffd",
                    "lone \ud800\udc00 surrogate"):
            assert atbash(atbash(s26)) == s26, "involution broken on %r" % s26
        # (B) the live shape: 412 KB text must decode >3x faster than the
        # reference loop and byte-identically
        blob26 = ("CTF archive leaderboard dump row. " * 12000)[:412000]
        import time as _t26
        t26 = _t26.perf_counter()
        fast26 = atbash(blob26)
        fast_s = _t26.perf_counter() - t26
        t26 = _t26.perf_counter()
        ref26 = _ref26(blob26)
        ref_s = _t26.perf_counter() - t26
        assert fast26 == ref26, "live-shape decodes differ"
        assert fast_s < ref_s / 3.0, \
            "table decode must win by >3x (fast=%.4fs ref=%.4fs)" % (
                fast_s, ref_s)
        mod_results.append(True)
    except Exception:
        log("selftest evolve26-atbash-table failed:"); traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-27: chain-decode provable precondition gates -----
    try:
        # RED (live, this feed round): the chain battery pays the FULL
        # decoder set on every BFS node -- ISCC 230,095 nodes / 35,045 text
        # inputs / 17.5 s wall (hex 6.10 s + b64 9.11 s + gzip-zlib 4.25 s
        # + b32 4.17 s + a85 3.40 s of the cProfile pass) -- the largest
        # remaining fastpath cost on text inputs after rsi-094/095/096.
        # Gates are NECESSARY conditions (gate failure == the decoder's own
        # validation is guaranteed to reject, preflight in
        # .temp/evolve27-profile), so results are byte-identical; the b64
        # cuts only drop provably-redundant decode calls. This block pins
        # that contract with the ungated reference implementations.
        import base64 as _b27
        import binascii as _ba27
        import gzip as _gz27
        import re as _re27
        import zlib as _zl27
        from fastpath.detectors import (_try_base64, _try_base32, _try_hex,
                                        _try_ascii85, _try_gzip_zlib,
                                        _looks_meaningful, chain_decode,
                                        B85_CHARS, _HEX_GATE_RE, _B32_GATE_RE,
                                        _A85_GATE_RE, _ZLIB_CMF_FIRST)

        def _ref_b64_27(data):
            if isinstance(data, str):
                s = data.strip().replace("\n", "").replace(" ", "")
                try:
                    raw = s.encode("ascii")
                except UnicodeEncodeError:
                    return None
            else:
                raw = data
            for pad in ("", "=" * (-len(raw) % 4)):
                for alt in (False, True):
                    try:
                        out = _b27.b64decode(raw + pad.encode(), validate=False) \
                            if not alt else _b27.urlsafe_b64decode(raw + pad.encode())
                        if out and _looks_meaningful(out):
                            return out
                    except (_ba27.Error, ValueError):
                        continue
            return None

        def _ref_b32_27(data):
            s = data.strip().replace("\n", "").replace(" ", "") \
                if isinstance(data, str) else None
            if not s:
                return None
            try:
                raw = s.encode("ascii")
            except UnicodeEncodeError:
                return None
            try:
                out = _b27.b32decode(raw + b"=" * (-len(raw) % 8))
                if out and _looks_meaningful(out):
                    return out
            except (_ba27.Error, ValueError):
                return None
            return None

        def _ref_hex_27(data):
            s = data.strip() if isinstance(data, str) else None
            if not s:
                return None
            clean = _re27.sub(r"^0x", "", s, flags=_re27.IGNORECASE)
            clean = _re27.sub(r"[\s,]", "", clean)
            if len(clean) < 8 or len(clean) % 2 \
                    or not _re27.fullmatch(r"[0-9a-fA-F]+", clean):
                return None
            try:
                out = bytes.fromhex(clean)
            except ValueError:
                return None
            return out if _looks_meaningful(out) else None

        def _ref_a85_27(data):
            if not isinstance(data, str) or len(data) < 12:
                return None
            res = []
            try:
                raw = data.encode("ascii")
            except UnicodeEncodeError:
                return None
            try:
                out = _b27.a85decode(raw)
                if out and _looks_meaningful(out):
                    res.append(("b85a", out))
            except (ValueError, _ba27.Error):
                pass
            if set(data) <= B85_CHARS:
                try:
                    out = _b27.b85decode(raw)
                    if out and _looks_meaningful(out):
                        res.append(("b85b", out))
                except (ValueError, _ba27.Error):
                    pass
            return res or None

        def _ref_gz_27(data):
            if not isinstance(data, bytes):
                return None
            for fn in (_zl27.decompress, _gz27.decompress,
                       lambda b: _zl27.decompress(b, -15)):
                try:
                    out = fn(data)
                    if out and _looks_meaningful(out):
                        return out
                except Exception:
                    continue
            return None

        # (A) gate constants: exact necessary-condition sets
        assert _ZLIB_CMF_FIRST == frozenset(
            (0x08, 0x18, 0x28, 0x38, 0x48, 0x58, 0x68, 0x78)), \
            "zlib CMF first-byte set"
        assert _HEX_GATE_RE.fullmatch("0X41 42,43") and \
            not _HEX_GATE_RE.fullmatch("0x41 42g"), "hex gate shape"
        assert _B32_GATE_RE.fullmatch("MZXW6YTBOI=") and \
            not _B32_GATE_RE.fullmatch("mzxw6ytboi="), "b32 gate shape"
        assert _A85_GATE_RE.fullmatch("X" * 11 + "z") and \
            not _A85_GATE_RE.fullmatch("X" * 11 + "\x0c") and \
            not _A85_GATE_RE.fullmatch("X" * 11 + "y"), "a85 gate shape"

        # (B) byte-identical outputs vs the ungated references on the
        # boundary battery + seeded fuzz
        cases27 = [
            "41424344454647", "0x41424344454647", "0X41424344454647",
            "  4142 4344,4546  47", "41424344g", "flag{deadbeef42}",
            "0x0x4142", "414243", "\xa041424344454647",
            "MZXW6YTBOI======", "mzxw6ytboi======", "MZXW 6YTB OI",
            "MZXW6YTBO!=", "MZXW6YTBOI=A",
            _b27.b64encode(b"flag{b64_dedup_roundtrip}").decode(),
            _b27.urlsafe_b64encode(b"flag{urlsafe_alt_rt}").decode(),
            "ZmxhZ3t", "ZmxhZ3t9ünicode", "====", "Zg==",
            "X" * 12, "z" * 12, "y" * 12, "X" * 11 + " ",
            "X" * 11 + "\x0c", "X" * 11 + "z",
            "汉字汉字汉字汉字", "", "  \t\n ",
        ]
        cases27 += [z27b for z27b in (
            _zl27.compress(b"flag{zlib_header_rt} " * 8),
            _gz27.compress(b"flag{gzip_magic_rt} " * 8),
            b"", b"garbage" * 40, b"\x78" + b"\x00" * 20,
            b"\x1f\x8b" + b"junkjunk",
        )]
        import random as _rnd27
        _rng27 = _rnd27.Random(20261127)
        for _ in range(300):
            ab27 = _rng27.choice([
                "0123456789abcdefABCDEF, \t\xa0",
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567=",
                "abcdefghijklmnopqrstuvwxyz0123456789+/-_=",
                "!#$%&()*+-;<=>?@^_`{|}~ ", "zZuUyY \x0c\t\r\n",
            ])
            cases27.append("".join(_rng27.choice(ab27)
                                   for _ in range(_rng27.randint(0, 60))))
        for ci27, c27 in enumerate(cases27):
            gz_in27 = c27.encode("utf-8", "replace") \
                if isinstance(c27, str) else c27
            assert _try_base64(c27) == _ref_b64_27(c27), "b64 case %d" % ci27
            assert _try_base32(c27) == _ref_b32_27(c27), "b32 case %d" % ci27
            assert _try_hex(c27) == _ref_hex_27(c27), "hex case %d" % ci27
            assert _try_ascii85(c27) == _ref_a85_27(c27), "a85 case %d" % ci27
            assert _try_gzip_zlib(gz_in27) == _ref_gz_27(gz_in27), \
                "gz case %d" % ci27

        # (C) chain end-to-end: nested chain still solves at the same depth
        # and note; garbage keeps the honest unsolved note
        _ch27 = FlagChecker(compiled_flag_patterns(cfg))
        n1_27 = _b27.b64encode(b"flag{nested_chain_rt}").decode()
        n2_27 = _b27.b32encode(n1_27.encode()).decode()
        n3_27 = n2_27.encode().hex()  # real hex layer: str of hex digits
        found27, note27 = chain_decode(_ch27, n3_27)
        assert found27 == "flag{nested_chain_rt}" and \
            note27 == "depth=3 via b64", "nested chain solve %r %r" % (
                found27, note27)
        assert chain_decode(_ch27, '{"id": 123, "org": "BIT"}') == \
            (None, "no flag in 8-depth chain"), "garbage note"

        # (D) live shape: the gated battery must cost less than half the
        # ungated reference on a 300 KB prose blob (3 passes each side)
        blob27 = ("CTF archive leaderboard dump row, mixed prose. " * 7000)[:300000]
        import time as _t27
        t0 = _t27.perf_counter()
        for _ in range(3):
            _ref_b64_27(blob27); _ref_b32_27(blob27); _ref_hex_27(blob27)
            _ref_a85_27(blob27); _ref_gz_27(blob27.encode("utf-8", "replace"))
        ref_s27 = _t27.perf_counter() - t0
        t0 = _t27.perf_counter()
        for _ in range(3):
            _try_base64(blob27); _try_base32(blob27); _try_hex(blob27)
            _try_ascii85(blob27)
            _try_gzip_zlib(blob27.encode("utf-8", "replace"))
        new_s27 = _t27.perf_counter() - t0
        assert new_s27 < ref_s27 / 2.0, \
            "gates must halve battery cost (new=%.4fs ref=%.4fs)" % (
                new_s27, ref_s27)
        mod_results.append(True)
    except Exception:
        log("selftest evolve27-chain-gates failed:"); traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-28: rot13 table-driven decode -----------------------
    try:
        # RED (live, this feed round): chain_decode used to call
        # codecs.encode(cur, "rot13") on every BFS node -- a per-call
        # codec-registry lookup whose rot_13 codec runs str.translate over
        # a dict (per-char dict lookups on wide strings; CPython keeps the
        # per-byte cache fast path only for 1-byte strings). Live
        # read-only cProfile on the incumbent: 2023-Bluehat-Quals-rank
        # 0.429 s cumtime over 848 calls = 34.7% of the 1.239 s chain
        # wall (6.8x the already-table-driven atbash); 2023-ISCC-Analysis
        # 1.902 s over 230,095 calls = 7.0% of chain. Real challenge text
        # is wide-kind (CJK / U+FFFD after errors="replace") where codecs
        # pays ~22 ms per 412 KB call vs 2.2-3.4 ms for the shift-13
        # bytes table; on pure-ASCII strings both paths are sub-2 ms
        # parity within measurement noise (NO ASCII speed claim is
        # made). Text stages stay budget-unbounded, so this round only
        # moves the rotation into C (no budget/verdict change is
        # claimed). This block pins the contract: byte-identical output
        # vs codecs.encode, shift-13 table shape, involution, chain
        # reachability through rot13, and a >3x wall assertion on the
        # wide live shape.
        from fastpath.detectors import _ROT13_TABLE, rot13
        import codecs as _cd28

        def _ref28(s):
            return _cd28.encode(s, "rot13")

        cases28 = [
            "", "a", "n", "A", "N", "m", "M", "z", "Z", "abcXYZ",
            "synt{ synt vf ebg13 }", "flag{rot13_rt} flf",
            "0123 !@# $\t|_+ punct pass-through",
            "accents \u00e9\u00fc\u00df CJK \u6c49\u5b57 replaced \ufffd tail",
            "lone surrogates \ud800\udc00 must pass through untouched",
            "x" * 511, "x" * 512, "x" * 513,
            ("QWERTYqwerty 0189 .,! " * 300),
        ]
        for ci28, c28 in enumerate(cases28):
            got28 = rot13(c28)
            assert isinstance(got28, str), "case %d must return str" % ci28
            assert got28 == _ref28(c28), "case %d differs from codecs" % ci28
        # (A) table shape: the shift-13 caesar table itself; ASCII
        # letters rotate a-m<->n-z / A-M<->N-Z, every other byte identity
        from fastpath.detectors import _CAESAR_SHIFT_TABLES as _cst28
        assert _ROT13_TABLE is _cst28[13], "rot13 table must be caesar shift-13"
        assert len(_ROT13_TABLE) == 256, "table must be 256 bytes"
        for i28 in (0, 10, 32, 64, 91, 96, 123, 128, 200, 255):
            if not (65 <= i28 <= 90 or 97 <= i28 <= 122):
                assert _ROT13_TABLE[i28] == i28, \
                    "non-letter byte %d must be identity" % i28
        assert _ROT13_TABLE[97] == 110 and _ROT13_TABLE[110] == 97, "'a'/'n'"
        assert _ROT13_TABLE[65] == 78 and _ROT13_TABLE[78] == 65, "'A'/'N'"
        # involution on ascii and wide strings (incl. lone surrogates)
        for s28 in ("", "aZzA019 .,!?", "flag{involutive_ynetrk}",
                    "accents \u00e9\u00fc\u00df \u6c49\u5b57 \ufffd",
                    "lone \ud800\udc00 surrogate"):
            assert rot13(rot13(s28)) == s28, "involution broken on %r" % s28
        # (B) chain reachability: a rot13-encoded flag must still be
        # found through the real chain_decode at depth=1 via rot13, and
        # garbage keeps the honest unsolved note
        _ch28 = FlagChecker(compiled_flag_patterns(cfg))
        _rot28 = _ref28("flag{rot13_chain_rt}")
        found28, note28 = chain_decode(_ch28, _rot28)
        assert found28 == "flag{rot13_chain_rt}" and \
            note28 == "depth=1 via rot13", "rot13 chain solve %r %r" % (
                found28, note28)
        assert chain_decode(_ch28, '{"id": 123, "org": "BIT"}') == \
            (None, "no flag in 8-depth chain"), "garbage note"
        # (C) live-shape speed on the WIDE kind (the real challenge
        # shape after errors="replace"): table must win >3x and
        # byte-identically; pure-ASCII is identity-asserted only
        blob28 = ("CTF archive leaderboard dump row. " * 6000)[:200000] + \
                 ("replaced \ufffd \u6c49\u5b57 tail " * 6000)
        import time as _t28
        t28 = _t28.perf_counter()
        fast28 = rot13(blob28)
        fast_s = _t28.perf_counter() - t28
        t28 = _t28.perf_counter()
        ref28b = _ref28(blob28)
        ref_s = _t28.perf_counter() - t28
        assert fast28 == ref28b, "live-shape decodes differ"
        assert fast_s < ref_s / 3.0, \
            "table decode must win >3x on wide kind (fast=%.4fs ref=%.4fs)" % (
                fast_s, ref_s)
        mod_results.append(True)
    except Exception:
        log("selftest evolve28-rot13-table failed:"); traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-29: wrapped-token chain extraction ------------------
    try:
        # RED (live, this feed round): 2024-newstar-category crypto.md #L5
        # carries the week-1 Base checkin payload as a backtick-wrapped hex
        # run; stage 1 chain-decodes the LINE (backticks included) and every
        # rsi-097 charset gate rejects it, so the hex>b32>b64 chain is never
        # entered (1.75 s / 329 attempts honest NOT SOLVED; model channel
        # solved the unwrapped span in ~120 s). This block pins the stage-1.2
        # extraction contract and chain reachability with a SYNTHETIC token;
        # the real challenge is the live RED/GREEN replay, not this source.
        from fastpath.detectors import (TOK_MAX_PER_FILE, TOK_MIN_LEN,
                                        TOK_MAX_TOTAL, wrapped_tokens)
        import base64 as _b29

        # synthetic 3-layer token: hex(b32(b64(flag)))
        _f29 = "flag{wrapped_token_chain_rt}"
        _l1_29 = _b29.b64encode(_f29.encode()).decode()
        _tok29 = _b29.b32encode(_l1_29.encode()).decode().encode().hex()
        assert len(_tok29) >= TOK_MIN_LEN and TOK_MIN_LEN == 16
        # (A) extraction: backtick/quote spans, charset gates, dedup, caps
        brief29 = ("### Base\n`%s`\nplain prose `short` and \"quoted words\""
                   " stay out\n") % _tok29
        toks29 = wrapped_tokens(brief29)
        assert toks29 == [_tok29], "backtick hex run must extract alone: %r" % toks29
        q29 = "ZmxhZ3t3cmFwcGVkX3Rva2VuX3J0fQ=="
        assert wrapped_tokens('"%s"' % q29) == [q29], "quoted b64 span extracts"
        assert wrapped_tokens("'%s' `%s`" % (q29, q29)) == [q29], \
            "dedup across wrap styles"
        # alpha-only prose spans DO fullmatch the b64/a85 charsets -- that
        # is the honest design: the boundary gates mirror the rsi-097 decoder
        # NECESSITY sets, not English-likeness, and the caps bound the cost;
        # such a span extracts and the REAL chain battery rejects it
        # downstream (pinned end-to-end in (C) below).
        _prose29 = "ThisIsJustProseNotPayload"
        assert wrapped_tokens("`%s`" % _prose29) == [_prose29], \
            "alpha-only span extracts (b64/a85 charset)"
        assert wrapped_tokens("`wdflag{xxxxx}`") == [], \
            "short/braced format hint stays out"
        assert wrapped_tokens("no wrapped payload at all") == []
        many29 = " ".join("`%s`" % (("ab%02d" % i) * 8) for i in range(80))
        assert len(wrapped_tokens(many29)) == TOK_MAX_PER_FILE, "per-file cap"
        assert TOK_MAX_TOTAL == 256
        # (B) chain reachability through the REAL battery: the extracted
        # synthetic token solves at depth=3 via b64, garbage keeps the note
        _ch29 = FlagChecker(compiled_flag_patterns(cfg))
        found29, note29 = chain_decode(_ch29, _tok29)
        assert found29 == _f29 and note29 == "depth=3 via b64", \
            "token chain solve %r %r" % (found29, note29)
        assert chain_decode(_ch29, "0123456789abcdef0123") == \
            (None, "no flag in 8-depth chain"), "garbage note unchanged"
        assert chain_decode(_ch29, _prose29) == \
            (None, "no flag in 8-depth chain"), \
            "alpha-only prose token keeps the honest no-flag note"
        mod_results.append(True)
    except Exception:
        log("selftest evolve29-token-extract failed:"); traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-30: xor_with_key period-sliced translate ----------------
    try:
        # RED (live, this feed round): the token stage's multi-byte cyclic XOR
        # was the last pure-Python per-element XOR loop (bytes(b ^ kb[i %
        # len(kb)] ...), ~4.3 MB/s) -- 48.19 s = 76% of baby_re's 63.7 s and
        # 69.78 s = 86% of WAF-bypass's 81.1 s (budget overrun to 80.8 s);
        # pool-wide 288 dirs project 7.8 GB. This block pins the rewritten
        # contract: byte-identity vs the reference loop over boundary shapes
        # + seeded fuzz, the return-type contract (always bytes / None), the
        # exotic-input fallback, and a >3x wall assertion -- the live RED/GREEN
        # replay is the real evidence, this source only pins the contract.
        from fastpath.detectors import xor_with_key
        import time as _t30

        def _ref30(data, key):
            kb = (key.encode("utf-8", errors="replace")
                  if isinstance(key, str) else key)
            if not kb:
                return None
            return bytes(b ^ kb[i % len(kb)] for i, b in enumerate(data))

        def _eq30(data, key):
            a = _ref30(data, key)
            b = xor_with_key(data, key)
            assert a == b and type(a) is type(b), \
                "xor_with_key contract broken: %r vs %r" % (a[:32], b[:32])

        # (A) boundary battery: lengths x key widths, bytearray data, key types
        for n in (0, 1, 2, 3, 511, 512, 513):
            _d30 = (bytes(range(256)) * ((n // 256) + 2))[:n]
            for k in (1, 2, 3, 13, 17, 64):
                _k30 = bytes((i * 7 + 3) % 256 for i in range(k))
                _eq30(_d30, _k30)
                _eq30(bytearray(_d30), _k30)
                if all(32 <= c < 127 for c in _k30):
                    _eq30(_d30, _k30.decode("ascii"))
        for n, k in ((5, 7), (7, 7), (8, 7), (13, 13), (14, 13), (12, 13)):
            _eq30(bytes(range(n)), bytes(range(k)))
        _eq30(memoryview(bytes(range(256)) * 4), b"key12345",
              )  # exotic input falls back to the reference loop
        _eq30(b"x", b"")  # empty key -> None on both sides
        _eq30(bytes(range(256)) * 4, bytearray(b"cyclekey"))
        # (B) seeded fuzz vs the reference (boundary widths included)
        import random as _r30
        _rng30 = _r30.Random(20261130)
        for _ in range(300):
            _nd30 = _rng30.randrange(0, 1500)
            _dd30 = bytes(_rng30.randrange(256) for _ in range(_nd30))
            _kl30 = _rng30.randrange(1, 25)
            _kk30 = bytes(_rng30.randrange(1, 128) for _ in range(_kl30))
            _eq30(_dd30, _kk30)
            if all(32 <= c < 127 for c in _kk30):
                _eq30(_dd30, _kk30.decode("ascii"))
        # (C) involution + token-stage reachability shape (synthetic flag)
        _d30 = b"flag{xor_slice_rt} " * 32
        _k30 = b"zzDecrypt9Key"
        assert xor_with_key(xor_with_key(_d30, _k30), _k30) == _d30, \
            "cyclic xor must be involutive"
        assert xor_with_key(xor_with_key(_d30, _k30), "zzDecrypt9Key") \
            == _d30, "str and bytes keys share one cyclic space"
        assert xor_with_key(b"", b"k") == b"" and \
            xor_with_key(b"", b"k") is not None
        # (D) wall assertion: 412 KB blob, k=13, min-of-3, >3x the reference
        _blob30 = (bytes(range(256)) * 1613)[:412_000]
        _best_r30 = _best_n30 = None
        for _ in range(3):
            _t0 = _t30.perf_counter()
            _ref30(_blob30, _k30)
            _dt = _t30.perf_counter() - _t0
            _best_r30 = _dt if _best_r30 is None else min(_best_r30, _dt)
            _t0 = _t30.perf_counter()
            xor_with_key(_blob30, _k30)
            _dt = _t30.perf_counter() - _t0
            _best_n30 = _dt if _best_n30 is None else min(_best_n30, _dt)
        assert _best_n30 * 3 < _best_r30, \
            "table path must beat the reference loop >3x: %r vs %r" % (
                _best_n30, _best_r30)
        mod_results.append(True)
    except Exception:
        log("selftest evolve30-xorkey-slice failed:"); traceback.print_exc()
        mod_results.append(False)
    # --- evolve-20261117-31: flag-check anchor necessary-condition gate ------
    try:
        # RED (live, this round): FlagChecker.find charged every chain node
        # with the full 10-pattern battery (ISCC 3.292 s cum = 13.3% of
        # chain, 230,095 calls; rank 0.336 s = 31.1%). The gate derives each
        # pattern's leading literal and short-circuits on miss; a hit falls
        # back to the original loop, so output is identical by construction.
        from core.flagcheck import _leading_literal, _anchor_ok_flags
        import re as _re31
        _pats31 = compiled_flag_patterns(cfg)
        _chk31 = FlagChecker(_pats31)
        # (A) derivation: exact anchor set for the live config + extractor
        # adversarial battery (alternation/classes/groups/quantifiers/
        # lookbehind/escapes all conservative)
        assert _chk31._anchors == ["flag:", "flag{", "FLAG{", "ctf{", "CTF{",
                                    "DASCTF{", "ctfshow{", "NSSCTF{",
                                    "SUCTF{", "hgame{"], _chk31._anchors
        _x31 = _leading_literal
        for _src31, _want31 in [
                ("abc|def", None), ("[Ff]lag", None), ("(?:abc)", None),
                ("flag\\{x}", "flag{x"), ("(?<![A-Za-z0-9])CTF\\{q}", "CTF{q"),
                ("\\dflag", None), ("(?=(x))flag", None), ("flag{2}", "fla"),
                ("flag+tail", "fla"), ("flag.tail", "flag"),
                ("flag(?:x)y", "flag"), ("^flag:ok", "flag:ok"),
                ("flag\\x41b", "flagAb"), ("fla$", "fla"),
                ("flag{0,3}x", "fla"), ("]lead", None)]:
            assert _x31(_src31) == _want31, \
                "extractor %r -> %r want %r" % (_src31, _x31(_src31), _want31)
        # (B) gate shape: a pattern whose .search must never run proves the
        # miss short-circuit; a hit raises -> fallback to the original loop
        class _Boom31:
            def __init__(self, src):
                self.pattern = src
                self.flags = 0
            def search(self, text):
                raise AssertionError("fallback loop ran")
        _b31 = FlagChecker([_Boom31(r"flag\{[^\s{}]{4,256}\}")])
        assert _b31._anchors == ["flag{"], _b31._anchors
        assert _b31.find("plain prose, nothing here") is None
        try:
            _b31.find("note: flag{boom_hit_rt} end")
            raise AssertionError("gate-hit must fall back to the pattern loop")
        except AssertionError as _e31:
            assert "fallback loop ran" in str(_e31)
        assert FlagChecker(
            [_re31.compile(r"(?i)FLAG\{[a-z]{4,}\}")])._anchors is None, \
            "IGNORECASE must disable the gate"
        assert FlagChecker(
            [_re31.compile(r"abc | def", _re31.VERBOSE)])._anchors is None, \
            "VERBOSE must disable the gate"
        # (C) output identity: battery + 300 seeded fuzz vs a gate-disabled
        # twin (same checker with _anchors None = the incumbent behavior)
        _twin31 = FlagChecker(_pats31)
        _twin31._anchors = None
        _cases31 = ["", "x", "flag{abcd1234}", "FLAG{abcd1234}",
                    "ctf{abcd1234}", "CTF{abcd1234}", "xCTF{abcd1234}",
                    "9CTF{abcd1234}", "DASCTF{abcd1234}", "ctfshow{abcd1234}",
                    "NSSCTF{abcd1234}", "SUCTF{abcd1234}", "hgame{abcd1234}",
                    "flag:abcd1234:", "flag{flag_not_here}", "flag{test_flag}",
                    "flag{abc}", "flag{" + "x" * 255 + "}",
                    "flag{" + "x" * 257 + "}", "ctf{second} flag{first}",
                    "\ufffdflag{gar\ufffdbage}", "no flags here"]
        for _t31 in _cases31:
            assert _chk31.find(_t31) == _twin31.find(_t31), _t31
            assert _chk31.find_all(_t31) == _twin31.find_all(_t31), _t31
        import random as _r31
        _frag31 = ["flag{", "FLAG{", "ctf{", "CTF{", "DASCTF{", "ctfshow{",
                   "NSSCTF{", "SUCTF{", "hgame{", "flag:", "}", "{",
                   "x" * 40, " ", "\ufffd", "flag_not_here}", "test_flag}",
                   "0123456789"]
        for _i31 in range(300):
            _rr31 = _r31.Random(31000 + _i31)
            _s31 = "".join(_rr31.choice(_frag31)
                           for _ in range(_rr31.randrange(0, 14)))
            assert _chk31.find(_s31) == _twin31.find(_s31), repr(_s31)
            assert _chk31.find_all(_s31) == _twin31.find_all(_s31), repr(_s31)
        # (D) chain reachability with the gate live: rot13 flag still solves,
        # garbage keeps the honest unsolved note
        import codecs as _c31
        _rot31 = _c31.encode("flag{anchor_chain_rt}", "rot13")
        assert chain_decode(_chk31, _rot31) == ("flag{anchor_chain_rt}",
                                                "depth=1 via rot13")
        assert chain_decode(_chk31, '{"id": 123, "org": "BIT"}') == \
            (None, "no flag in 8-depth chain")
        # (E) miss-face wall: the gate must beat the gate-disabled twin >3x
        # on a 200 KB garbage blob (min-of-3)
        import time as _t31m
        _blob31 = ("CTF archive leaderboard dump row. " * 6000)[:200000]
        _bt31 = _bg31 = None
        for _ in range(3):
            _t0 = _t31m.perf_counter()
            _chk31.find(_blob31)
            _dt = _t31m.perf_counter() - _t0
            _bt31 = _dt if _bt31 is None else min(_bt31, _dt)
            _t0 = _t31m.perf_counter()
            _twin31.find(_blob31)
            _dt = _t31m.perf_counter() - _t0
            _bg31 = _dt if _bg31 is None else min(_bg31, _dt)
        assert _bt31 * 3 < _bg31, \
            "gate must win >3x on miss face (%r vs %r)" % (_bt31, _bg31)
        mod_results.append(True)
    except Exception:
        log("selftest evolve31-anchor-gate failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-32] replay-DB isolation guard (rsi-102): after the
        # rsi-099 adoption its live replays ran with the production store
        # because clean engine copies under .temp were NOT covered by the
        # evolve-15 markers; rows 939-948 (incl. 2 synthetic SOLVED) leaked.
        # The scratch root must now route to the isolated store, an explicit
        # CTFPILOT_DB must always win, and production dirs must not trigger.
        _saved32 = os.environ.get("CTFPILOT_DB")
        try:
            _wsroot = os.path.dirname(PROJECT_ROOT)
            _replay32 = os.path.join(_wsroot, ".temp", "rsi-102-replay",
                                     "2024-xhlj-web-easy_api")
            _engine_copy32 = os.path.join(_wsroot, ".temp", "rsi102-ab",
                                          "engine-candidate", "tests",
                                          "challs", "01_b64chain")
            for _case32, _d32 in (("workspace .temp replay", _replay32),
                                  ("engine copy under .temp", _engine_copy32)):
                os.environ.pop("CTFPILOT_DB", None)
                _synthetic_db_guard(cfg, _d32)
                _got32 = os.environ.get("CTFPILOT_DB", "")
                assert _got32.endswith(".holdout"), \
                    "%s must route to .holdout, got %r" % (_case32, _got32)
            os.environ.pop("CTFPILOT_DB", None)
            _synthetic_db_guard(cfg, os.path.join(_wsroot, "ctf-feed",
                                                  "2024-xhlj-web-easy_api"))
            assert os.environ.get("CTFPILOT_DB", "") == "", \
                "a production feed dir must stay on the production store"
            os.environ.pop("CTFPILOT_DB", None)
            _synthetic_db_guard(cfg, os.path.join(PROJECT_ROOT, "writeups"))
            assert os.environ.get("CTFPILOT_DB", "") == "", \
                "an in-project production dir must stay on the production store"
            os.environ["CTFPILOT_DB"] = "explicit-replay.sqlite"
            _synthetic_db_guard(cfg, _replay32)
            assert os.environ.get("CTFPILOT_DB") == "explicit-replay.sqlite", \
                "explicit CTFPILOT_DB must win over the scratch guard"
        finally:
            if _saved32 is None:
                os.environ.pop("CTFPILOT_DB", None)
            else:
                os.environ["CTFPILOT_DB"] = _saved32
        mod_results.append(True)
    except Exception:
        log("selftest evolve32-temp-db-guard failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-34 / rsi-104] _candidate_inputs per-line
        # same-file content dedup. RED (live, full 365-dir pool, read-only
        # engine functions): 26.5% of all per-line inputs (93,396 /
        # 352,750) are same-content duplicates within one file and every
        # one of them independently pays chain-decode stage 1 +
        # js-unicode-escape + the caesar 25-shift sweep (chain_decode's
        # seen set is call-local). Carrier walls: HuiXiang 13,861 lines ->
        # 6 distinct, chain per-line 5.05 -> 0.06 s (-98.8%); AECBS -88.2%;
        # attachments726 -74.0%; MORSE -69.1%; ISCC -63.6%. Equivalence is
        # by construction: every per-line consumer is a PURE function of
        # the line content, stage-1.2 wrapped_tokens dedups tokens
        # cross-input by content, and the first occurrence of each
        # distinct content keeps its original slot and line number, so
        # hit capability and hit position are unchanged; only redundant
        # miss attempts disappear.
        import tempfile as _tf34
        from fastpath.detectors import _candidate_inputs as _ci34
        _wsroot34 = os.path.dirname(PROJECT_ROOT)
        _d34 = os.path.join(_wsroot34, ".temp", "rsi104-selftest", "dedup")
        os.makedirs(_d34, exist_ok=True)
        # (A) behavior: duplicates collapse to the first occurrence, #L1
        # survives, unique lines keep their numbers, short lines still
        # dropped, whole-file + [bytes] inputs unchanged
        _f34 = os.path.join(_d34, "dup.txt")
        with open(_f34, "w", encoding="utf-8") as fh34:
            fh34.write("alpha beta gamma one\n")
            fh34.write("repeated payload line\n")
            fh34.write("short\n")
            fh34.write("repeated payload line\n")
            fh34.write("another unique line here\n")
            fh34.write("repeated payload line\n")
        _sz34 = os.path.getsize(_f34)
        _files34 = [{"abs": _f34, "path": "dup.txt", "size": _sz34,
                     "magic": None}]
        _got34 = _ci34(_d34, _files34)
        _kinds34 = [(s, "b" if isinstance(x, bytes) else
                     ("L" if "#L" in s else "t")) for s, x in _got34]
        assert ("dup.txt", "t") in _kinds34, "whole-file input missing"
        assert ("dup.txt [bytes]", "b") in _kinds34, "bytes input missing"
        _line_srcs34 = [s for s, k in _kinds34 if k == "L"]
        assert _line_srcs34 == ["dup.txt #L1", "dup.txt #L2",
                                 "dup.txt #L5"], _line_srcs34
        _line_data34 = {s: x for s, x in _got34 if "#L" in s and
                        isinstance(x, str)}
        assert _line_data34["dup.txt #L2"] == "repeated payload line"
        # (B) hit reachability with dedup live: a chain payload on a
        # KEPT line still solves, including when it is the second copy of
        # a line that appears once before it (content-unique here)
        import base64 as _b34
        _flag34 = "flag{line_dedup_rt}"
        _b64l34 = _b34.b64encode(_flag34.encode()).decode()
        assert len(_b64l34) >= 8
        _f34b = os.path.join(_d34, "hit.txt")
        with open(_f34b, "w", encoding="utf-8") as fh34b:
            fh34b.write("prose intro line one\n")
            fh34b.write(_b64l34 + "\n")
        _files34b = [{"abs": _f34b, "path": "hit.txt",
                      "size": os.path.getsize(_f34b), "magic": None}]
        _got34b = _ci34(_d34, _files34b)
        _hit34 = [x for s, x in _got34b if "#L" in s and x == _b64l34]
        assert _hit34, "payload line must survive dedup"
        _pats34 = compiled_flag_patterns(cfg)
        _chk34 = FlagChecker(_pats34)
        _res34, _note34 = chain_decode(_chk34, _hit34[0])
        assert _res34 == _flag34, (_res34, _note34)
        # (C) duplicate with the FIRST copy on a dropped-for-length line:
        # dedup keys on content, not on line number, and lines <8 chars
        # were already filtered pre-change -- a >=8 duplicate must still
        # collapse to its first surviving occurrence
        _f34c = os.path.join(_d34, "edge.txt")
        with open(_f34c, "w", encoding="utf-8") as fh34c:
            fh34c.write("tiny\n")
            fh34c.write("duplicate eight-chars\n")
            fh34c.write("duplicate eight-chars\n")
        _files34c = [{"abs": _f34c, "path": "edge.txt",
                      "size": os.path.getsize(_f34c), "magic": None}]
        _srcs34c = [s for s, x in _ci34(_d34, _files34c)
                    if "#L" in s and isinstance(x, str)]
        assert _srcs34c == ["edge.txt #L2"], _srcs34c
        mod_results.append(True)
    except Exception:
        log("selftest evolve34-line-dedup failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-35 / rsi-107] base-N charset gates: set(data) ->
        # compiled char-class fullmatch. RED (real node streams on the rsi-104
        # incumbent): the four decoders built a Python set over EVERY node,
        # and whole-file text nodes are huge (2025-jksn-quals-attachments726
        # SOLVED carrier 768,181 B x 61 giant nodes; ISCC 767,189 B x 40);
        # 1.38 s of the 2.25 s fast wall on jksn, 1.42 s of 8.84 s on ISCC.
        # The gate predicate is identical (every char in the charset); this
        # block pins it three ways (predicate identity, fast-reject identity,
        # crafted decode reachability) plus a wall assertion on a 400 KB
        # prose blob.
        import base64 as _b35
        from fastpath import detectors as _det35
        _chs35 = {"b62": _det35.B62_CHARS, "b58": _det35.B58_CHARS,
                  "b45": _det35.B45_CHARS, "b92": _det35.B92_CHARS}
        _res35 = {"b62": _det35._B62_GATE_RE, "b58": _det35._B58_GATE_RE,
                  "b45": _det35._B45_GATE_RE, "b92": _det35._B92_GATE_RE,
                  "b85": _det35._B85_GATE_RE}
        _fn35 = {"b62": _det35._try_base62, "b58": _det35._try_base58,
                 "b45": _det35._try_base45, "b92": _det35._try_base92}
        # (A) predicate identity over boundary + seeded fuzz strings
        _rnd35 = random.Random(3517)
        _pool35 = ("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                   "abcdefghijklmnopqrstuvwxyz $%*+-./:=!#_-{}[]\\|\n\r\t")
        _cases35 = []
        for _k35, _cs35 in _chs35.items():
            _al35 = "".join(sorted(_cs35))
            _cases35 += [(_k35, _al35[:n]) for n in
                         (0, 1, 11, 12, 13, 63, 64, 65, 511, 512, 513)]
            _cases35 += [(_k35, _al35[:12] + bad) for bad in
                         ("\x00", "\u00e9", "\u4e2d", "\n", "{", "}")]
        for _ in range(300):
            _ln35 = _rnd35.choice([0, 1, 11, 12, 13, 20, 64, 65, 512])
            _cases35.append((_rnd35.choice(list(_chs35)),
                             "".join(_rnd35.choice(_pool35)
                                     for _ in range(_ln35))))
        for _k35, _s35 in _cases35:
            if not _s35:
                # empty string: set("") <= chars is vacuously True while the
                # '+' gate rejects it; every decoder returns None earlier on
                # len < 12, so the two faces agree at the function boundary
                # (pinned in section B below)
                continue
            _set_ok = set(_s35) <= _chs35[_k35]
            _re_ok = _res35[_k35].fullmatch(_s35) is not None
            assert _set_ok == _re_ok, (_k35, repr(_s35))
        # (B) fast-reject identity: a failed set gate must mean None, and the
        # b85 branch of _try_ascii85 must not emit a b85b result
        for _k35, _s35 in _cases35:
            if set(_s35) <= _chs35[_k35]:
                continue
            assert _fn35[_k35](_s35) is None, (_k35, repr(_s35[:40]))
            _hits35 = _det35._try_ascii85(_s35) if _k35 != "b92" else None
            if _hits35:
                assert all(_nm35 != "b85b" for _nm35, _o35 in _hits35)
        # (C) crafted reachability: each gate-passing encoding decodes to the
        # exact payload bytes
        _AL62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        _AL58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        _AL45 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"

        def _enc_big35(bs, alpha):
            v = int.from_bytes(bs, "big")
            out = ""
            while v:
                v, r = divmod(v, len(alpha))
                out = alpha[r] + out
            return out

        def _enc4535(bs):
            out = ""
            for _i in range(0, len(bs) - 1, 2):
                _u = bs[_i] * 256 + bs[_i + 1]
                out += _AL45[_u % 45] + _AL45[(_u // 45) % 45] + _AL45[_u // 2025]
            if len(bs) % 2:
                _u = bs[-1]
                out += _AL45[_u % 45] + _AL45[_u // 45]
            return out

        for _p35, _fn35x, _al35 in (
                (b"flag{base62_gate_rt} ok", _det35._try_base62, _AL62),
                (b"flag{base58_gate_rt} ok", _det35._try_base58, _AL58),
                (b"flag{base45_gate_rt} ok", _det35._try_base45, None)):
            _tok35 = (_enc4535(_p35) if _al35 is None
                      else _enc_big35(_p35, _al35))
            assert _fn35x(_tok35) == _p35, (_p35[:12], _tok35[:24])
        _p85 = b"flag{b85_gate_rt} ok"
        assert _det35._try_ascii85(
            _b35.b85encode(_p85).decode()) == [("b85b", _p85)]
        # (D) wall: on a 400 KB prose blob the C gate must beat set() >20x
        # (min-of-3), both sides must reject
        _blob35 = ("CTF archive leaderboard dump row. " * 13000)[:400000]
        assert _res35["b62"].fullmatch(_blob35) is None
        assert not (set(_blob35) <= _det35.B62_CHARS)
        _bt35 = _st35 = None
        for _ in range(3):
            _t0 = time.perf_counter()
            _res35["b62"].fullmatch(_blob35)
            _dt35 = time.perf_counter() - _t0
            _bt35 = _dt35 if _bt35 is None else min(_bt35, _dt35)
            _t0 = time.perf_counter()
            set(_blob35) <= _det35.B62_CHARS
            _dt35 = time.perf_counter() - _t0
            _st35 = _dt35 if _st35 is None else min(_st35, _dt35)
        assert _bt35 * 20 < _st35, \
            "C gate must win >20x on giant miss face (%r vs %r)" % (_bt35, _st35)
        mod_results.append(True)
    except Exception:
        log("selftest evolve35-basegates failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-36 / rsi-105] fastpath IDNA/punycode domain-label
        # arm (stage 1.3). RED (live, this feed round):
        # 2024-ShuXinCup-sjaq-IDN的绘画 描述.txt embeds
        # xn--7nraaa738k9tnba223fba479qe42gda whose stdlib idna decode is
        # 叁柒叁柒比幺零比玖叁零叁 (3737:10:9303, the key material before
        # the netdisk image); the engine ran 24 attempts all-miss -- the
        # chain battery covers b64/hex/b32/... but has no idna decoder.
        # Labels: lower-case 'xn--' only (stdlib ToUnicode requires the
        # exact prefix), domain-charset extraction [A-Za-z0-9.-] (the live
        # token is followed by a CJK 。 that would kill .encode('ascii')
        # under a naive [^\s]+ regex). Pool-wide trigger face (read-only
        # scan, 6822 files): 2 files / 2 tokens.
        from fastpath.detectors import idna_tokens as _it36
        from fastpath.detectors import run_fast_path as _rf36
        # (A) extractor battery: charset boundary at the CJK 。, dedup,
        #     per-file cap, upper-case XN-- not extracted
        _t36 = ("visit xn--7nraaa738k9tnba223fba479qe42gda。 and "
                "xn--e1aybc.test, XN--upper stays out, "
                "xn--7nraaa738k9tnba223fba479qe42gda again")
        _got36 = _it36(_t36)
        assert _got36 == ["xn--7nraaa738k9tnba223fba479qe42gda",
                          "xn--e1aybc.test"], _got36
        assert len(_it36(" ".join("xn--t%d" % i for i in range(30)))) == 8, \
            "per-file cap must hold at 8"
        # '{'/'}' stay in the charset: a constructed carrier embeds the
        # flag skeleton in the punycode basic part and the stdlib codec
        # accepts the label -- extraction must not truncate it
        _sk36 = "xn--" + "flag{进化三十五}".encode("punycode").decode("ascii")
        assert _it36("key %s here" % _sk36) == [_sk36], _it36(_sk36)
        # (B) strict-decode battery: RED token decodes; junk drops; a
        #     pure-ASCII label raises UnicodeError via codec round-trip
        assert ("xn--7nraaa738k9tnba223fba479qe42gda"
                .encode("ascii").decode("idna")) == "叁柒叁柒比幺零比玖叁零叁"
        _raised36 = False
        try:
            "xn--flag{pure}-".encode("ascii").decode("idna")
        except UnicodeError:
            _raised36 = True
        assert _raised36, "pure-ASCII round-trip must raise"
        # (C) hit reachability end-to-end: prose embedding a punycode
        #     label whose IDNA decode carries a CJK flag body SOLVES via
        #     the arm; the label's own basic part is 'flag{}' (empty body)
        #     so no earlier stage can credit a raw-echo hit
        _flag36 = "flag{进化三十五}"
        _label36 = "xn--" + _flag36.encode("punycode").decode("ascii")
        assert _label36.encode("ascii").decode("idna") == _flag36
        _d36 = os.path.join(os.path.dirname(PROJECT_ROOT), ".temp",
                            "rsi105-selftest")
        os.makedirs(_d36, exist_ok=True)
        _f36 = os.path.join(_d36, "prose.txt")
        with open(_f36, "w", encoding="utf-8") as _fh36:
            _fh36.write("赛题描述第一行\n")
            _fh36.write("访问 %s 获取密钥材料。\n" % _label36)
        _files36 = [{"abs": _f36, "path": "prose.txt",
                     "size": os.path.getsize(_f36), "magic": None}]
        _pats36 = compiled_flag_patterns(cfg)
        _chk36 = FlagChecker(_pats36)
        assert _chk36.find(_label36) is None, \
            "fixture label must stay unmatchable in raw form"
        _att36 = []
        _res36 = _rf36(_d36, _files36, _chk36, _att36)
        assert _res36 == _flag36, (_res36, [a.detail for a in _att36])
        assert [a for a in _att36 if a.detector == "idna" and a.success
                and a.flag == _flag36], "idna success attempt missing"
        # (D) miss face end-to-end: the RED carrier line decodes to
        #     evidence notes, verdict stays None
        _f36b = os.path.join(_d36, "miss.txt")
        with open(_f36b, "w", encoding="utf-8") as _fh36b:
            _fh36b.write("描述：密钥藏在 xn--7nraaa738k9tnba223fba479qe42gda 中。\n")
        _files36b = [{"abs": _f36b, "path": "miss.txt",
                      "size": os.path.getsize(_f36b), "magic": None}]
        _att36b = []
        _res36b = _rf36(_d36, _files36b, _chk36, _att36b)
        assert _res36b is None
        _ev36 = [a for a in _att36b if a.detector == "idna"]
        assert _ev36 and "叁柒叁柒比幺零比玖叁零叁" in _ev36[0].detail, \
            [a.detail for a in _ev36]
        # (E) no-xn-- file: gate short-circuits, zero idna attempts
        _f36c = os.path.join(_d36, "plain.txt")
        with open(_f36c, "w", encoding="utf-8") as _fh36c:
            _fh36c.write("没有任何域名标签的普通行\n")
        _files36c = [{"abs": _f36c, "path": "plain.txt",
                      "size": os.path.getsize(_f36c), "magic": None}]
        _att36c = []
        _res36c = _rf36(_d36, _files36c, _chk36, _att36c)
        assert _res36c is None
        assert not [a for a in _att36c if a.detector == "idna"]
        mod_results.append(True)
    except Exception:
        log("selftest evolve36-idna-arm failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-37 / rsi-108] anchor-gate early-break loop:
        # FlagChecker.find/find_all replaced
        # `any(a in text for a in self._anchors)` (per-call generator
        # object + per-item next() hops; on the 2023-ISCC-Analysis fast
        # face: 6.03M genexpr calls + 548K any() calls = 5.07 s of a
        # 23.1 s profiled wall) with an explicit for/break/else loop.
        # Predicate identity: a loop that never breaks falls into the
        # else branch exactly when any() would have returned False --
        # including the empty-anchor-list face, where BOTH forms
        # early-return before the pattern loop runs.
        import re as _re37
        from core.flagcheck import FlagChecker as _FC37
        _pats37 = [_re37.compile(r) for r in (
            r"flag\{(?!flag_not_here\}|test_flag\})[^\s{}]{4,256}\}",
            r"ctf\{[^\s{}]{4,256}\}",
            r"DASCTF\{[^\s{}]{4,256}\}")]
        _fc_on36 = _FC37(_pats37)
        _fc_off36 = _FC37([_re37.compile(r"(?i)flag\{[^}]{4,256}\}")])
        _fc_empty36 = _FC37([])
        _fc_extra36 = _FC37(_pats37, extra_regex=r"zzq\{[A-Za-z0-9_]{4,64}\}")
        # face construction: anchors active / disabled / empty / extra
        _anch37 = _fc_on36._anchors
        assert _anch37 == ["flag{", "ctf{", "DASCTF{"], _anch37
        assert _fc_off36._anchors is None
        assert _fc_empty36._anchors == []
        assert _fc_extra36._anchors == ["zzq{", "flag{", "ctf{", "DASCTF{"]
        # (A) predicate/behavior identity vs the any() reference: when no
        # anchor is present no anchored pattern can match, so find must
        # return None and find_all must return [] (the gate's own safety
        # proof, pinned); presence cases must stay solvable.
        _rnd37 = random.Random(3701)
        _AL37 = "abcXYZ0189{}_ :.-FLAGfasCTD\t"
        _tinj37 = _anch37 + ["flag", "{", "ctfs{", "ddflag{{", "Zflag{"]
        _texts37 = [
            "", " ", "}", "flag", "flag{",
            "flag{anchor_loop_rt}",
            "ctf{sh0rt} DASCTF{ok}",
            "flag{flag_not_here}", "flag{test_flag}",
            "noise zzq{extra_face_rt} tail",
            "prose line with neither anchor nor flag",
            "leading flag{mid_reach_v1} tail",
        ]
        for _ in range(3000):
            _ln37 = _rnd37.randint(0, 80)
            _t37 = "".join(_rnd37.choice(_AL37) for _ in range(_ln37))
            if _t37 and _rnd37.random() < 0.10:
                _pos37 = _rnd37.randrange(len(_t37))
                _a37 = _rnd37.choice(_tinj37)
                _t37 = _t37[:_pos37] + _a37 + _t37[_pos37 + len(_a37):]
            _texts37.append(_t37)
        for _t37 in _texts37:
            _gate37 = any(a in _t37 for a in _anch37)
            if not _gate37 and _t37:
                assert _fc_on36.find(_t37) is None, repr(_t37[:60])
                assert _fc_on36.find_all(_t37) == [], repr(_t37[:60])
        assert _fc_on36.find("flag{anchor_loop_rt}") == "flag{anchor_loop_rt}"
        assert _fc_on36.find("ctf{cas36}") == "ctf{cas36}"
        assert _fc_on36.find("DASCTF{ok36}") == "DASCTF{ok36}"
        assert _fc_on36.find("flag{test_flag}") is None  # negative lookahead
        # empty-anchor face: any([]) == False in the old code forced an
        # early None; the for/else form must agree on this degenerate face
        assert _fc_empty36.find("flag{x}") is None
        assert _fc_empty36.find_all("flag{x}") == []
        # anchors=None face: gate disabled, patterns still match
        assert _fc_off36.find("CASE FLAG{cased_match}") == "FLAG{cased_match}"
        # extra-regex face: prepended pattern extends the anchor set
        assert _fc_extra36.find("zzq{extra_face_rt}") == "zzq{extra_face_rt}"
        assert _fc_extra36.find("flag{anchor_loop_rt}") == "flag{anchor_loop_rt}"
        # (B) wall shape: on a mixed small-text stream the explicit loop
        # must not be slower than the any() reference gate (min-of-3,
        # tolerance for noise); the real win is the removed function-call
        # hops inside find, which this face only samples.
        _stream37 = [_rnd37.choice(_texts37) for _ in range(4000)]
        _t36b = _t36a37 = None
        for _ in range(3):
            _t0 = time.perf_counter()
            for _s37 in _stream37:
                _g37 = any(a in _s37 for a in _anch37)
            _dt37 = time.perf_counter() - _t0
            _t36a37 = _dt37 if _t36a37 is None else min(_t36a37, _dt37)
            _t0 = time.perf_counter()
            for _s37 in _stream37:
                _g37 = False
                for _aa37 in _anch37:
                    if _aa37 in _s37:
                        _g37 = True
                        break
            _dt37 = time.perf_counter() - _t0
            _t36b = _dt37 if _t36b is None else min(_t36b, _dt37)
        log("evolve37 gate micro: loop %.4fs vs any %.4fs (ratio %.3f)"
            % (_t36b, _t36a37, _t36b / _t36a37))
        assert _t36b <= _t36a37 * 1.10, \
            "explicit loop gate must not regress (%r vs %r)" % (_t36b, _t36a37)
        mod_results.append(True)
    except Exception:
        log("selftest evolve37-anchor-gate-loop failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-38 / rsi-109] per-line caesar subsumption. RED
        # (live, rsi-107-adopted fast path rerun through the rsi-105/rsi-108
        # adopted state, read-only): every per-line input paid the full
        # 25-shift sweep; carrier walls 2023-ISCC-Analysis 1.19 s per-line
        # of 8.1 s fast wall, DeadGame 4.39 s of 30.0 s, rank 0.14 s of
        # 1.3 s, MORSE 0.18 s of 1.8 s, AECBS 0.14 s of 2.8 s.
        # shifted(L) is a literal substring of shifted(F), so with F's
        # whole-file sweep present a per-line sweep can only add a hit when
        # the whole-file match was NOT credited: raw-echo (first match masks
        # a later line's derived flag at the same shift) or garbage-gated
        # (U+FFFD/control chars, _flag_text returns None and the later clean
        # line match is never seen). Clean whole files skip per-line passes;
        # raw/masked files keep the exact incumbent behavior.
        from fastpath.detectors import (run_fast_path as _rf38,
                                        _candidate_inputs as _ci38,
                                        caesar_sweep as _cs38,
                                        _flag_text as _ft38,
                                        _GARBAGE_FLAG_RE as _garbage38)
        _d38 = os.path.join(scratch, "evolve38-caesar")

        def _unshift38(txt, k):
            out = []
            for ch in txt:
                o = ord(ch)
                if 65 <= o <= 90:
                    out.append(chr(65 + (o - 65 - k) % 26))
                elif 97 <= o <= 122:
                    out.append(chr(97 + (o - 97 - k) % 26))
                else:
                    out.append(ch)
            return "".join(out)

        def _put38(name, lines):
            path38 = os.path.join(_d38, name)
            with open(path38, "w", encoding="utf-8") as fh38:
                fh38.write("\n".join(lines) + "\n")
            return [{"abs": path38, "path": name,
                     "size": os.path.getsize(path38), "magic": None}]

        def _run38(name, lines):
            files38 = _put38(name, lines)
            att38 = []
            flag38 = _rf38(_d38, files38, FlagChecker(compiled_flag_patterns(cfg)),
                           att38)
            return flag38, att38, files38

        os.makedirs(_d38, exist_ok=True)
        # (A) clean multi-line prose: no flag, per-line caesar passes are
        # skipped (exactly one whole-file caesar miss note, no #L notes)
        _f38, _a38, _files38 = _run38(
            "notes.txt",
            ["alpha bravo charlie delta", "echo foxtrot golf hotel",
             "india juliett kilo lima", "mike november oscar papa"])
        assert _f38 is None, _f38
        _notes38 = [a.detail for a in _a38
                    if a.detector == "caesar" and " #L" in a.detail]
        _whole38 = [a.detail for a in _a38
                    if a.detector == "caesar" and " #L" not in a.detail]
        assert _notes38 == [], _notes38
        assert _whole38 == ["notes.txt: 25 shifts no flag"], _whole38
        # (B) derived payload on a line still solves (whole-file sweep
        # subsumes the line) and is credited normally
        _flag38 = "flag{caesar_line_rt}"
        _f38, _a38, _ = _run38(
            "hit.txt",
            ["intro prose alpha", _unshift38(_flag38, 3), "tail prose beta"])
        assert _f38 == _flag38, _f38
        assert any(a.detector == "caesar" and a.success and a.flag == _flag38
                   for a in _a38), [a.detail for a in _a38]
        # (C) raw-echo fallback: at shift 1 the whole-file first match is a
        # raw-echo flag (literal in line 1) masking a later line's derived
        # flag; the fallback must still run the per-line passes
        _rawflag38 = "flag{raw_mask_first}"
        _derived38 = "flag{derived_after_mask}"
        _f38, _a38, _ = _run38(
            "caesar-raw.txt",
            [_rawflag38, _unshift38(_rawflag38, 1), _unshift38(_derived38, 1)])
        assert _f38 == _derived38, _f38
        assert any(a.detector == "provenance" and "raw-echo" in a.detail
                   for a in _a38), [a.detail for a in _a38]
        # (D) garbage-masked fallback: shift-1 first match is a U+FFFD
        # garbage flag that _flag_text rejects; the clean derived flag on a
        # later line must still be found through the per-line fallback
        _garb38 = "flag{bad\ufffdone}"
        _clean38 = "flag{clean_after_garbage}"
        assert _garbage38.search(_garb38), "fixture garbage must be gated"
        _f38, _a38, _ = _run38(
            "caesar-garbage.txt",
            [_unshift38(_garb38, 1), _unshift38(_clean38, 1)])
        assert _f38 == _clean38, _f38
        # (E) reference-implementation fuzz: the incumbent caesar stage
        # (all inputs swept, no skip) must agree with the candidate on 150
        # randomized line-domain fixtures, including raw literals
        import random as _rnd38

        def _ref_stage38(inputs, checker):
            rawtext = _rawtext38
            for src, data in inputs:
                if not isinstance(data, str):
                    continue
                for shift, dec in _cs38(data):
                    found = _ft38(checker, dec)
                    if found:
                        if found in rawtext:
                            continue
                        return found
            return None

        _alph38 = ("abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                   "0123456789{}_{}-prose")
        _rng38 = _rnd38.Random(20261117)
        _fail38 = 0
        for _i38 in range(150):
            _n38 = _rng38.randint(2, 6)
            _lines38 = []
            _rawtext38 = ""
            _inj38 = _rng38.random() < 0.6
            _inj_line38 = _rng38.randrange(_n38) if _inj38 else -1
            _inj_flag38 = "flag{fz%d_%d}" % (_i38, _rng38.randrange(1000))
            for _j38 in range(_n38):
                if _j38 == _inj_line38:
                    _ln38 = _unshift38(_inj_flag38, _rng38.randint(1, 25))
                elif _rng38.random() < 0.25:
                    _ln38 = "flag{raw_fz%d_%d}" % (_i38, _rng38.randrange(1000))
                else:
                    _ln38 = "".join(_rng38.choice(_alph38)
                                    for _ in range(_rng38.randint(8, 60)))
                _lines38.append(_ln38)
                _rawtext38 += _ln38
            _f38, _a38, _files38 = _run38("fz38_%03d.txt" % _i38, _lines38)
            _inputs38 = _ci38(_d38, _files38)
            _ref38 = _ref_stage38(_inputs38,
                                  FlagChecker(compiled_flag_patterns(cfg)))
            if _f38 != _ref38:
                _fail38 += 1
                log("fuzz38 mismatch %d: cand=%r ref=%r"
                    % (_i38, _f38, _ref38))
        assert _fail38 == 0, "%d/150 fuzz mismatches" % _fail38
        mod_results.append(True)
    except Exception:
        log("selftest evolve38-caesar-subsumption failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-39 / rsi-110] _looks_meaningful table-driven
        # predicate. RED (live cProfile on the entry-60 incumbent over full
        # run_fast_path): the reference per-byte genexpr + sum ran on every
        # decoder candidate -- the universal meaningful filter -- 190,034
        # calls / cum 3.807 s on DeadGame (45.7 s profiled), 61,166 calls /
        # 0.777 s on ISCC. The rewrite maps each byte to 1 iff NOT printable
        # via one 256-byte translate table and counts in C; the integer
        # printable count and the float division/comparison are identical,
        # so the predicate is bit-identical by construction.
        from fastpath.detectors import _looks_meaningful as _lm39
        from fastpath.detectors import _MEANINGFUL_COUNT_TABLE as _tbl39
        # (A) table content: 1 exactly for non-printable byte values,
        # 0 for printable ones (32..126 plus 9/10/13)
        for _b39 in range(256):
            _want39 = 0 if (32 <= _b39 < 127 or _b39 in (9, 10, 13)) else 1
            assert _tbl39[_b39] == _want39, "table byte %d" % _b39
        # (B) reference differential: same predicate as the pre-evolve39
        # genexpr + sum body, over boundary lengths, all 256 single-byte
        # values, threshold-exact ratios (strict > 0.85), seeded fuzz, and
        # bytearray inputs.

        def _ref39(data):
            if not data:
                return False
            _sample39 = data[:512]
            _pr39 = sum(1 for _b39 in _sample39
                        if 32 <= _b39 < 127 or _b39 in (9, 10, 13))
            return _pr39 / len(_sample39) > 0.85

        _fail39 = 0
        for _b39 in range(256):
            if _lm39(bytes([_b39])) != _ref39(bytes([_b39])):
                _fail39 += 1
        for _n39 in list(range(2, 41)) + [511, 512, 513, 1024]:
            for _mk39 in ("print", "nonprint", "mix"):
                if _mk39 == "print":
                    _d39 = bytes((32 + (_i39 % 95)) for _i39 in range(_n39))
                elif _mk39 == "nonprint":
                    _d39 = bytes((200 + (_i39 % 56)) for _i39 in range(_n39))
                else:
                    _d39 = bytes((32 + (_i39 % 95)) if (_i39 * 7919) % 100 < 85
                                 else 1 for _i39 in range(_n39))
                for _v39 in (_d39, bytearray(_d39)):
                    if _lm39(_v39) != _ref39(_v39):
                        _fail39 += 1
        for (_p39, _n39) in [(17, 20), (85, 100), (435, 512), (86, 100),
                              (436, 512), (34, 40)]:
            _d39 = bytearray(b"a" * _n39)
            for _i39 in range(_p39, _n39):
                _d39[_i39] = 1
            if _lm39(bytes(_d39)) != _ref39(bytes(_d39)):
                _fail39 += 1
        import random as _rnd39
        _rng39 = _rnd39.Random(20261117)
        for _i39 in range(2000):
            _n39 = _rng39.randint(0, 600)
            _ratio39 = _rng39.random()
            _d39 = bytes(_rng39.randrange(256) if _rng39.random() >= _ratio39
                         else _rng39.choice((9, 10, 13, 32, 64, 90, 126))
                         for _ in range(_n39))
            if _lm39(_d39) != _ref39(_d39):
                _fail39 += 1
                if _fail39 < 5:
                    log("evolve39 mismatch n=%d %r" % (_n39, _d39[:40]))
        assert _fail39 == 0, "%d evolve39 mismatches" % _fail39
        # (C) wall shape: on the typical short-garbage face the table form
        # must not be slower than the reference genexpr (min-of-3, noise
        # tolerance); live RED measured 4.4x-33.2x micro across shapes.
        _s39 = bytes(_rng39.randrange(256) for _ in range(22))
        _t39a = _t39b = None
        for _ in range(3):
            _t0 = time.perf_counter()
            for _ in range(20000):
                _ref39(_s39)
            _dt = time.perf_counter() - _t0
            _t39a = _dt if _t39a is None else min(_t39a, _dt)
            _t0 = time.perf_counter()
            for _ in range(20000):
                _lm39(_s39)
            _dt = time.perf_counter() - _t0
            _t39b = _dt if _t39b is None else min(_t39b, _dt)
        log("evolve39 meaningful micro: table %.4fs vs ref %.4fs (ratio %.3f)"
            % (_t39b, _t39a, _t39b / _t39a))
        assert _t39b <= _t39a * 1.10, \
            "table predicate must not regress (%r vs %r)" % (_t39b, _t39a)
        mod_results.append(True)
    except Exception:
        log("selftest evolve39-meaningful-table failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-40 / rsi-110] progressive-caesar anchor
        # back-solve arm: delta_i = a + b*i (mod 26), b != 0.
        # RED (live): 2024-newstar__Strange_King challenge.txt
        # "ksjr{EcxvpdErSvcDgdgEzxqjql}" (a=5, b=2) -> flag{PleaseDo
        # NotStopLearing}; incumbent 25-shift caesar cannot reach it
        # (engine-verified NOT SOLVED 0.94 s / 17 attempts pre-change).
        from fastpath.detectors import (run_fast_path as _rf40,
                                        progressive_caesar_sweep as _pg40)
        _d40 = os.path.join(scratch, "evolve40-progressive")

        def _enc40(txt, a, b):
            out = []
            for j, ch in enumerate(txt):
                o = ord(ch)
                s = (a + b * j) % 26
                if 97 <= o <= 122:
                    out.append(chr((o - 97 + s) % 26 + 97))
                elif 65 <= o <= 90:
                    out.append(chr((o - 65 + s) % 26 + 65))
                else:
                    out.append(ch)
            return "".join(out)

        def _put40(name, text):
            p40 = os.path.join(_d40, name)
            with open(p40, "w", encoding="utf-8") as fh40:
                fh40.write(text)
            return [{"abs": p40, "path": name,
                     "size": os.path.getsize(p40), "magic": None}]

        def _run40(name, text):
            files40 = _put40(name, text)
            att40 = []
            flag40 = _rf40(_d40, files40,
                           FlagChecker(compiled_flag_patterns(cfg)), att40)
            return flag40, att40

        os.makedirs(_d40, exist_ok=True)
        _ck40 = FlagChecker(compiled_flag_patterns(cfg))
        # (A) live RED shape byte-for-byte: Chinese prose + the Strange
        # King ciphertext must solve via the new arm
        _red40 = ("# Strange King (NewStar2024 Week1 Crypto) \r\n"
                  "\u67d0\u559c\u6b22\u62bd\u9510\u523b 5 \u7684\u7687\u5e1d"
                  "\u60f3\u6bcf\u5929\u8fdb\u6b65\u4e00\u4e9b\uff0c\u76f4\u5230"
                  "\u4ed6\u5a36\u4e86\u4e2a\u6a21\uff0c\u56de\u5230\u539f\u70b9"
                  "\uff0c\u5168\u90e8\u767d\u7ed9\U0001f605 \r\n"
                  "\u8fd9\u662f\u4ed6\u6700\u540e\u7559\u4e0b\u7684\u8baf\u606f"
                  "\uff1a \r\n"
                  "ksjr{EcxvpdErSvcDgdgEzxqjql} \r\n"
                  "flag \u5305\u88f9\u7684\u662f\u53ef\u8bfb\u7684\u660e\u6587\r\n")
        _f40, _a40 = _run40("challenge.txt", _red40)
        assert _f40 == "flag{PleaseDoNotStopLearing}", _f40
        _hit40 = [a for a in _a40 if a.detector == "progressive-caesar"
                  and a.success]
        assert len(_hit40) == 1 and "a=5 b=2" in _hit40[0].detail, \
            [x.detail for x in _a40 if x.detector == "progressive-caesar"]
        # (B) round-trip: random (a, b!=0) pairs + mixed-case payloads
        for (_a40v, _b40v, _fl40) in (
                (7, 11, "flag{round_trip_ok_40}"),
                (21, 24, "flag{MixedCase_Payload-42}"),
                (0, 5, "ctf{zeroA_positiveB}"),
                (3, 25, "flag{descending_step}")):
            _f40, _a40 = _run40(
                "rt40_%d_%d.txt" % (_a40v, _b40v), _enc40(_fl40, _a40v, _b40v))
            assert _f40 == _fl40, (_a40v, _b40v, _fl40, _f40)
            assert any(a.detector == "progressive-caesar" and a.success
                       and a.flag == _fl40 for a in _a40), \
                [x.detail for x in _a40 if x.detector == "progressive-caesar"]
        # (C) b == 0 stays the caesar stage's domain: a constant-shift
        # ciphertext must credit detector "caesar", never this arm
        _f40, _a40 = _run40("const40.txt", _enc40("flag{const_shift_only}", 9, 0))
        assert _f40 == "flag{const_shift_only}", _f40
        assert not [a for a in _a40
                    if a.detector == "progressive-caesar"], \
            [x.detail for x in _a40 if x.detector == "progressive-caesar"]
        assert any(a.detector == "caesar" and a.success for a in _a40)
        # (D) non-arithmetic ciphertexts never back-solve (150 fuzz);
        # green faces must add ZERO attempts of this detector
        import random as _rnd40
        _rng40 = _rnd40.Random(20261117)
        _alph40 = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for _i40 in range(150):
            _pre40 = "".join(_rng40.choice(_alph40)
                             for _ in range(_rng40.randint(3, 12)))
            _pay40 = "".join(_rng40.choice(_alph40)
                             for _ in range(_rng40.randint(4, 30)))
            _f40, _a40 = _run40("fz40_%03d.txt" % _i40,
                                _pre40 + "{" + _pay40 + "}")
            _pg40a = [a for a in _a40
                      if a.detector == "progressive-caesar"]
            assert _f40 is None or not _pg40a, (_i40, _f40,
                                                [x.detail for x in _pg40a])
            assert not _pg40a, (_i40, [x.detail for x in _pg40a])
        # (E) raw-echo gate: plaintext flag + progressive(flag) ciphertext
        # in one file -> the back-solved hit equals the literal flag, is
        # retracted by provenance, and the solve does NOT credit
        _lit40 = "flag{retracted_by_gate40}"
        _f40, _a40 = _run40("raw40.txt",
                            _lit40 + "\n" + _enc40(_lit40, 5, 2))
        assert _f40 is None, _f40
        assert any(a.detector == "provenance"
                   and "progressive" not in a.detail and "raw-echo" in a.detail
                   for a in _a40), [x.detail for x in _a40]
        # (F) plain prose green face: zero attempts of this detector,
        # whole pipeline unchanged verdict
        _f40, _a40 = _run40(
            "notes40.txt",
            "alpha bravo charlie delta\nif (x) { return y; }\n"
            "function(){ flag{nested_plain_no_brace\nplain prose here\n")
        assert _f40 is None, _f40
        assert not [a for a in _a40
                    if a.detector == "progressive-caesar"], \
            [x.detail for x in _a40 if x.detector == "progressive-caesar"]
        # (G) direct unit call sanity: no checker anchors -> no-op
        class _NoAnchor40:
            pass
        _v40, _n40 = _pg40(_ck40, "")
        assert _v40 is None and _n40 == ""
        _v40, _n40 = _pg40(_NoAnchor40(), "ksjr{EcxvpdErSvcDgdgEzxqjql}")
        assert _v40 is None
        mod_results.append(True)
    except Exception:
        log("selftest evolve40-progressive failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-41 / rsi-114] grouped fast pre-gate for the
        # anchor necessary-condition gate. RED (live): after rsi-104's
        # per-line dedup the find() call stream carries almost no duplicate
        # texts (0.0% on the ISCC face: 95,426 calls / 95,393 distinct), so
        # a result memo has no payoff; but 95,424 of those calls are
        # anchor-gate rejections and the gate itself was find's entire
        # remaining cost (1.135 s), each miss paying a full per-anchor
        # substring scan (10 live anchors x avg 1374 B). 80.9% of the misses
        # contained NO '{' at all. Live config fact: the anchor set is
        # ['flag:' + 9 brace literals] -- 'flag:' carries no '{', so the
        # set-wide intersection is EMPTY (v1 single-char design was
        # corrected live by its own selftest). The grouped form: non-empty
        # intersection -> one-char fast gate; empty intersection -> greedy
        # split char c carried by the MOST anchors (deterministic min tie);
        # when c is absent from text every c-carrying anchor is provably
        # absent and only the anchors LACKING c (plain group, ['flag:'] on
        # live) need scanning. Provable equivalence: c-miss => c-anchors
        # all absent => gate result == (any plain anchor in text); c-hit or
        # split disabled -> the exact rsi-108 full loop. Conservative:
        # fewer than 2 anchors carrying c disables the split (a 1-anchor
        # group would make the miss path pay an extra memchr for nothing).
        import random as _rnd41
        import re  # local: do_selftest has no module-level `re` binding
        _ck41 = FlagChecker(compiled_flag_patterns(cfg))
        # (A) derivation faces: live config = ['flag:' + 9 brace
        # literals] -> set-wide intersection EMPTY, greedy split char
        # '{' (9 carriers), plain group ['flag:']; single-anchor set ->
        # its own rarest char via pre; disjoint anchors -> both off;
        # IGNORECASE gate -> both off; empty patterns -> both off.
        assert _ck41._anchors and _ck41._anchors[0] == "flag:", \
            _ck41._anchors
        assert _ck41._anchor_pre is None and _ck41._split_char == "{" \
            and _ck41._plain_anchors == ["flag:"], \
            (_ck41._anchor_pre, _ck41._split_char, _ck41._plain_anchors)
        _kc41 = FlagChecker([re.compile(r"KEY-[0-9]{4}-[0-9]{4}")])
        assert _kc41._anchors == ["KEY-"] and _kc41._anchor_pre == "-", \
            (_kc41._anchors, _kc41._anchor_pre)
        _nc41 = FlagChecker([re.compile(r"ab[0-9]"), re.compile(r"cd[0-9]")])
        assert _nc41._anchors == ["ab", "cd"] and _nc41._anchor_pre is None \
            and _nc41._split_char is None, \
            (_nc41._anchors, _nc41._anchor_pre, _nc41._split_char)
        _sp41 = FlagChecker([re.compile(r"flag\{x+\}"), re.compile(r"flag\[x+\}"),
                            re.compile(r"KEY-[0-9]{4}")])
        assert _sp41._anchors == ["flag{", "flag[", "KEY-"] \
            and _sp41._anchor_pre is None and _sp41._split_char == "a" \
            and _sp41._plain_anchors == ["KEY-"], \
            (_sp41._anchors, _sp41._split_char, _sp41._plain_anchors)
        _ic41 = FlagChecker([re.compile(r"flag\{x+\}", re.IGNORECASE)])
        assert _ic41._anchors is None and _ic41._anchor_pre is None \
            and _ic41._split_char is None
        _ep41 = FlagChecker([])
        assert _ep41._anchors == [] and _ep41._anchor_pre is None \
            and _ep41._split_char is None
        # (B) behavior identity vs a full-scan reference: the pre-gate may
        # only reject texts the full gate would also reject; allow-side
        # outputs must be byte-identical to the reference.
        def _ref_gate41(chk, text):
            if not text:
                return False
            if chk._anchors is None:
                return True
            return any(a in text for a in chk._anchors)

        _faces41 = [
            _ck41,
            FlagChecker([re.compile(r"flag\{[^{}]{1,120}\}"),
                         re.compile(r"DASCTF\{[a-z]+\}"),
                         re.compile(r"hgame\{[^{}]{1,60}\}")]),
            _kc41, _nc41, _sp41, _ic41, _ep41,
        ]
        _texts41 = [
            "", "plain prose without special chars",
            "brace but no anchor {x}", '{"a": 1, "b": [2, 3]}',
            "flag{real_hit_41}", "DASCTF{Extra41}", "hgame{hyphen-ated-41}",
            "KEY-1234-5678", "ab9", "flAg{weird-case}",
            "\u4e2d\u6587{flag{inside_unicode_41}\u00e9", "a" * 5000,
            "x{" * 2000, "{", "}", "flag{",
            "nested flag{outer flag{inner41}}", "\x00\x01bin\xff{",
        ]
        for _ch41 in _faces41:
            for _t41 in _texts41:
                if not _ref_gate41(_ch41, _t41):
                    assert _ch41.find(_t41) is None, (_t41[:40])
                    assert _ch41.find_all(_t41) == [], (_t41[:40])
                _fa41 = _ch41.find_all(_t41)
                assert (_ch41.find(_t41) is None) == (not _fa41), (_t41[:40])
        _cklive41 = FlagChecker(compiled_flag_patterns(cfg))
        assert _cklive41.find("prefix flag{hit_ok_41} suffix") == \
            "flag{hit_ok_41}"
        # find_all follows PATTERN priority (config order), not text
        # position: SUCTF{ precedes hgame{ in the live pattern list; the
        # live bodies demand 4-256 non-space chars (short "h1"/"s2"
        # bodies do NOT match -- keep the fixtures realistic).
        assert _cklive41.find_all(
            "a hgame{long_enough_h1} b SUCTF{s2_is_long} c") == \
            ["SUCTF{s2_is_long}", "hgame{long_enough_h1}"]
        # (C) 500-case seeded fuzz across gate faces x text shapes: any
        # output difference vs the reference is a hard fail.
        _rng41 = _rnd41.Random(20261117)
        _alpha41 = "abcdefgl{}_-0123 \t\xff\u4e2d"
        for _i41 in range(500):
            _n41 = _rng41.randint(0, 200)
            _t41 = "".join(_rng41.choice(_alpha41) for _ in range(_n41))
            if _rng41.random() < 0.35:
                _t41 = (_t41[:_n41 // 2]
                        + _rng41.choice(["flag{", "FLAG{", "ctf{",
                                         "DASCTF{", "ctfshow{", "NSSCTF{",
                                         "SUCTF{", "hgame{", "KEY-",
                                         "{", "}"])
                        + _t41[_n41 // 2:])
            for _ch41 in _faces41:
                _g41 = _ref_gate41(_ch41, _t41)
                _f41 = _ch41.find(_t41)
                if not _g41:
                    assert _f41 is None and _ch41.find_all(_t41) == [], (_i41,)
                else:
                    _fa41 = _ch41.find_all(_t41)
                    assert (_f41 is None) == (not _fa41), (_i41, _f41)
        # (D) wall shape (min-of-5 against parallel-session CPU noise):
        # split-path live checker. The no-brace miss face pays one memchr
        # + the single plain anchor ('flag:') instead of the full
        # 10-anchor sweep -> must be clearly faster (<= 0.35x); the
        # brace-present miss face pays one extra memchr (~1.04-1.10x).
        # Reference = same live checker with BOTH gates disabled.
        # [evolve-20261117-48 / rsi-119] hardening: the bare ratio lines
        # flapped in three consecutive differential rounds (rsi-116
        # 'concurrent session flaky face', rsi-117, rsi-118 once per
        # root). Measured mechanism (rsi-119 probe, live checker):
        # with-brace ratio sits at ~1.04 with +-17% round-level noise on
        # ~17 ms walls, so the 1.15x line has only ~9% headroom and
        # fails 6/30 rounds on a QUIET machine; no-brace pregate wall is
        # ~3.5 ms (smaller than one scheduler quantum), so a single
        # ~20 ms descheduling burst across the interleaved reps
        # collapses the 0.35x line (rsi-118 live pair 21.1 ms vs
        # 20.2 ms, ratio 1.045). Fix, one variable -- this (D)
        # measurement: corpus x4 (additive scheduler noise stays
        # constant while the signal grows), min-of-5, and a 20 ms
        # absolute tolerance floor on both lines absorbing burst-class
        # additive waits. Teeth: the _wA41r > 0.031 assert keeps the A
        # floor from masking a gate-disabled (full-loop, ~_wA41r)
        # regression; the B line keeps failing structural breaks
        # >= ~1.45x at these sizes (battery-on-hit, O(n^2) gate) at
        # the price of missing sub-45% drift -- the bare line's 20%
        # quiet false-positive rate (probe, 6/30) had already made
        # this block untrustworthy.
        _alphaD41 = "abcdefghijklmnopqrstuvwxyz0123456789+/="
        _miss41 = ["".join(_rng41.choices(_alphaD41, k=1374))
                   for _ in range(12000)]
        _bmiss41 = [t[:1373] + "{" for t in _miss41[:9600]]
        # the A face must never reach the regex battery: every text
        # has to reject at the split gate (no '{', no plain anchor),
        # else a gate-passer would pay battery time inside the pregate
        # wall and silently destroy the 0.35x ratio's meaning.
        for _t41 in _miss41:
            assert "{" not in _t41 and "flag:" not in _t41, \
                "A-face corpus polluted"

        def _wall41(texts):
            _t0 = time.perf_counter()
            for _t41 in texts:
                if _cklive41.find(_t41) is not None:
                    raise AssertionError("unexpected hit")
            return time.perf_counter() - _t0

        _ckref41 = FlagChecker(compiled_flag_patterns(cfg))
        _ckref41._anchor_pre = None
        _ckref41._split_char = None

        def _wall41r(texts):
            _t0 = time.perf_counter()
            for _t41 in texts:
                if _ckref41.find(_t41) is not None:
                    raise AssertionError("unexpected ref hit")
            return time.perf_counter() - _t0

        _wA41 = _wA41r = None
        _wB41 = _wB41r = None
        for _ in range(5):
            _dt = _wall41(_miss41)
            _wA41 = _dt if _wA41 is None else min(_wA41, _dt)
            _dt = _wall41r(_miss41)
            _wA41r = _dt if _wA41r is None else min(_wA41r, _dt)
            _dt = _wall41(_bmiss41)
            _wB41 = _dt if _wB41 is None else min(_wB41, _dt)
            _dt = _wall41r(_bmiss41)
            _wB41r = _dt if _wB41r is None else min(_wB41r, _dt)
        log("evolve41 pregate micro: no-brace %.4fs vs ref %.4fs (ratio "
            "%.3f), with-brace %.4fs vs ref %.4fs (ratio %.3f)"
            % (_wA41, _wA41r, _wA41 / _wA41r,
               _wB41, _wB41r, _wB41 / _wB41r))
        # teeth guard: with the x4 corpus the reference wall must stay
        # big enough that the A-line floor can never absorb a
        # gate-disabled regression: 0.35x _wA41r + 0.020 < _wA41r
        # <=> _wA41r > 30.8 ms.
        assert _wA41r > 0.031, ("A-line teeth scale", _wA41r)
        # 0.35x + 20 ms floor: burst-class additive waits (<= one
        # scheduler quantum) can no longer fail the block; a broken
        # gate pays ~_wA41r and stays caught while the teeth assert
        # holds.
        assert _wA41 <= _wA41r * 0.35 + 0.020, (_wA41, _wA41r)
        # 1.15x + 20 ms floor: the with-brace face stays one extra
        # memchr (~1.04-1.10x); the floor absorbs the descheduling
        # noise that made the bare 1.15x line flap 6/30 rounds on a
        # quiet machine (rsi-119 probe) and once per root under the
        # rsi-118 differential load. Structural breaks (>= ~1.45x at
        # these sizes) still fail hard.
        assert _wB41 <= _wB41r * 1.15 + 0.020, (_wB41, _wB41r)
        mod_results.append(True)
    except Exception:
        log("selftest evolve41-anchor-pregate failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-42 / rsi-113] _try_base64 isascii pre-gate.
        # RED (live faces, entry-61 incumbent, wrapper counting): the str
        # branch paid strip + two full replace scans + a partial encode
        # scan + a UnicodeEncodeError on every non-ASCII node, and the
        # raise share was 33.4-100% of str calls (ISCC 44.1% avg 321
        # chars, DeadGame 67.5%, jksn-attachments726 96.5% avg 802
        # chars, the would-raise nodes far longer than the ASCII ones).
        # s.isascii() after strip() is the EXACT same rejection predicate
        # as encode('ascii') raising (strip removes only whitespace, the
        # replaces remove only '\n'/' ' -- no new chars are introduced, so
        # non-ASCII survives to raise and all-ASCII stays all-ASCII).
        # This selftest pins that equivalence permanently against the
        # embedded pre-evolve42 reference implementation.
        from fastpath.detectors import _try_base64 as _tb42
        import base64 as _b42
        import binascii as _ba42
        from fastpath.detectors import _looks_meaningful as _lm42

        def _ref42(data):
            # verbatim pre-evolve42 str gate + decode loop (entry-61)
            if isinstance(data, str):
                _s42 = data.strip().replace("\n", "").replace(" ", "")
                try:
                    _raw42 = _s42.encode("ascii")
                except UnicodeEncodeError:
                    return None
            else:
                _raw42 = data
            _padb42 = b"=" * (-len(_raw42) % 4)
            _pads42 = (b"",) if not _padb42 else (b"", _padb42)
            _alts42 = (False, True) if (b"-" in _raw42 or b"_" in _raw42) \
                else (False,)
            for _pad42 in _pads42:
                for _alt42 in _alts42:
                    try:
                        if _alt42:
                            _out42 = _b42.urlsafe_b64decode(_raw42 + _pad42)
                        else:
                            _out42 = _b42.b64decode(_raw42 + _pad42,
                                                    validate=False)
                        if _out42 and _lm42(_out42):
                            return _out42
                    except (_ba42.Error, ValueError):
                        continue
            return None

        _fail42 = 0
        _FACES42 = []
        for _cp42 in range(0x100):
            _FACES42.append(chr(_cp42))
        for _n42 in [0, 1, 2, 3, 4, 7, 8, 63, 64, 255, 256, 511, 512, 513]:
            _FACES42.extend(["A" * _n42, "SGVsbG8=" * (_n42 // 8 + 1),
                             ("B" * _n42) + "中" + ("C" * _n42),
                             "中" * _n42])
        for _ws42 in ["\xa0", "\u2028", "\u3000", "\u2000", "\t", "\n",
                      "  ", " \t\n\r "]:
            _FACES42.append(_ws42 + "SGVsbG8=" + _ws42)
            _FACES42.append(_ws42 * 3 + "ZmxhZ3tiYXNlNjR9" + _ws42)
        _FACES42.extend(["SGVs\nbG8=", "SGVs bG8=", "SG\nVs\nbG8=\n",
                         "  SGVs  bG8=  ", "SGVs\tbG8=", "SGVsb\u4e2dG8=",
                         "SGVsb\xa0G8=", "SGV" + "🚩" + "bG8=", "",
                         " ", "\n", "   \n  ", "\xa0", "\u3000\xa0",
                         "ZmxhZ3tYWX0", "ZmxhZ3tYWX0=", "ZmxhZ3tYWX0-_=",
                         b"SGVsbG8=", b"SGVs\nbG8=", b"", b"\x00\xff\xfe",
                         bytes(range(256))])
        for _x42 in _FACES42:
            if _tb42(_x42) != _ref42(_x42):
                _fail42 += 1
                if _fail42 < 5:
                    log("evolve42 static mismatch: %r -> live=%r ref=%r"
                        % (_x42[:40], _tb42(_x42), _ref42(_x42)))
        # (B) seeded fuzz: ascii/b64/cjk-inject/bytes families
        import random as _rnd42
        _rng42 = _rnd42.Random(2026111742)
        _B64C42 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        _NONA42 = ["中", "文", "🚩", "\xa0", "\u3000", "é", "Ω", "한", "\u2028"]
        for _i42 in range(3000):
            _k42 = _rng42.randrange(5)
            if _k42 == 0:
                _x42 = "".join(_rng42.choice(_B64C42 + " \n")
                               for _ in range(_rng42.randrange(0, 150)))
            elif _k42 == 1:
                _pay42 = _b42.b64encode(bytes(_rng42.randrange(256)
                                                 for _ in range(
                                                     _rng42.randrange(1, 30)))
                                          ).decode("ascii")
                _x42 = (_rng42.choice(["", "  \n ", "\xa0", "\u3000"])
                        + _pay42 + _rng42.choice(["", " \t", "\n"]))
            elif _k42 == 2:
                _base42 = "".join(_rng42.choice(_B64C42)
                                  for _ in range(_rng42.randrange(1, 60)))
                _p42 = _rng42.randrange(len(_base42) + 1)
                _x42 = _base42[:_p42] + _rng42.choice(_NONA42) + _base42[_p42:]
            elif _k42 == 3:
                _x42 = bytes(_rng42.randrange(256)
                             for _ in range(_rng42.randrange(0, 50)))
            else:
                _x42 = "".join(_rng42.choice(_NONA42 + list("中文abc123"))
                               for _ in range(_rng42.randrange(0, 100)))
            if _tb42(_x42) != _ref42(_x42):
                _fail42 += 1
                if _fail42 < 8:
                    log("evolve42 fuzz mismatch %d: %r -> live=%r ref=%r"
                        % (_i42, _x42[:40], _tb42(_x42), _ref42(_x42)))
        assert _fail42 == 0, "%d evolve42 mismatches" % _fail42
        # (C) wall shape on the raise face (802-char CJK prose, the jksn
        # RED average): the gated form must not be slower than the ref.
        # (The short-ASCII face pays one extra C-level isascii scan,
        # measured -12% at 25 chars / -7% at 136 chars -- an accepted
        # trade because the raise share on live faces is 33-100% and the
        # raise nodes are the long ones; net verdict is the carrier A/B.)
        _raise42 = ("2025年应急通信演练数据：本次演练覆盖全国三十二个省级单位，"
                    "重点验证短波、超短波、卫星、互联网等多手段融合通信能力，" * 8)[:802]
        _t42a = _t42b = None
        for _ in range(3):
            _t0 = time.perf_counter()
            for _ in range(5000):
                _ref42(_raise42)
            _dt = time.perf_counter() - _t0
            _t42a = _dt if _t42a is None else min(_t42a, _dt)
            _t0 = time.perf_counter()
            for _ in range(5000):
                _tb42(_raise42)
            _dt = time.perf_counter() - _t0
            _t42b = _dt if _t42b is None else min(_t42b, _dt)
        log("evolve42 b64-isascii micro: gated %.4fs vs ref %.4fs "
            "(ratio %.3f)" % (_t42b, _t42a, _t42b / _t42a))
        assert _t42b <= _t42a * 1.10, \
            "isascii gate must not regress the raise face (%r vs %r)" \
            % (_t42b, _t42a)
        mod_results.append(True)
    except Exception:
        log("selftest evolve42-b64-isascii failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-43 / rsi-114] production replay guard (rsi-111
        # lesson, live row 964): a same-(name, dir_path, flag) re-solve of
        # an ALREADY-CREDITED row is a duplicate credit, not a fresh
        # outcome. The guard detects it at write time (finish-point) and
        # suppresses THIS row in place -- the exact adjudicate-db surgery
        # that had to be run by hand on row 964, now automatic. RED (live,
        # dual-root A/B on a scratch main DB): the incumbent wrote TWO
        # solved=1 rows with identical flag for one replayed challenge dir
        # (the row-964 shape); the candidate writes the second row as
        # solved=0 with an audited semantic:replay-duplicate attempt.
        # Everything else passes through untouched: a DIFFERENT flag
        # (post-fix re-solve), an adjudicated-away prior (solved no
        # longer 1), a different directory (republished mirror), an
        # empty flag, a first solve, and any store failure (fail-open:
        # the guard must never mask a real solve).
        _r4142 = os.path.join(scratch, "evolve43-replayguard")
        if os.path.isdir(_r4142):
            shutil.rmtree(_r4142)
        os.makedirs(_r4142)
        _db42 = os.path.join(_r4142, "traj.sqlite")
        _st42 = TrajStore(_db42)
        _nm42 = "2024-newstar-week"
        _dir42 = os.path.join(_r4142, "mirror", _nm42)
        _fl42 = "flag{B@sE_0f_CrYpt0_N0W}"
        _seed42 = _st42.new_challenge(_nm42, "misc", _dir42, "auto", "orig")
        _st42.finish_challenge(_seed42, True, _fl42)
        # (A) same (name, dir, flag) -> suppressed in place, fully audited
        _cid42 = _st42.new_challenge(_nm42, "misc", _dir42, "auto", "replay")
        _prov42 = {"status": "derived"}
        _logs42 = []
        assert _replay_guard_suppress(_st42, _cid42, _nm42, _dir42, _fl42,
                                      _logs42.append, _prov42) is True
        _row42 = _st42.conn.execute(
            "SELECT solved, flag FROM challenges WHERE id=?",
            (_cid42,)).fetchone()
        assert _row42[0] == 0 and _row42[1] == "", _row42
        assert _prov42["status"] == "replay-suppressed" \
            and _prov42["replay_prior"] == _seed42, _prov42
        _att42 = _st42.conn.execute(
            "SELECT detail, error_class, success, flag FROM attempts "
            "WHERE challenge_id=? ORDER BY id", (_cid42,)).fetchall()
        assert len(_att42) == 1 and _att42[0][1] == \
            "semantic:replay-duplicate" and _att42[0][2] == 0 \
            and _att42[0][3] == _fl42, _att42
        assert any("row id=%d" % _seed42 in m for m in _logs42), _logs42
        # (B) different flag = legitimate post-fix re-solve -> passes
        _cid42 = _st42.new_challenge(_nm42, "misc", _dir42, "auto", "refix")
        _fl42b = "flag{c0rr3ct3d_cr3d1t_42}"
        assert _st42.find_replay_prior(_nm42, _dir42, _fl42b, _cid42) is None
        _prov42 = {"status": "derived"}
        assert _replay_guard_suppress(_st42, _cid42, _nm42, _dir42,
                                      _fl42b, _logs42.append,
                                      _prov42) is False
        _st42.finish_challenge(_cid42, True, _fl42b)
        _row42 = _st42.conn.execute(
            "SELECT solved, flag FROM challenges WHERE id=?",
            (_cid42,)).fetchone()
        assert _row42[0] == 1 and _row42[1] == _fl42b, _row42
        # (C) adjudicated-away prior (solved flipped to 0) -> passes
        _st42.finish_challenge(_seed42, False, "")
        _cid42 = _st42.new_challenge(_nm42, "misc", _dir42, "auto", "readj")
        assert _st42.find_replay_prior(_nm42, _dir42, _fl42, _cid42) is None
        # (D) different directory (republished mirror) -> passes
        _dir42d = os.path.join(_r4142, "mirror2", _nm42)
        assert _st42.find_replay_prior(_nm42, _dir42d, _fl42, _cid42) is None
        # (E) empty flag -> query short-circuits, guard no-op
        assert _st42.find_replay_prior(_nm42, _dir42, "", _cid42) is None
        assert _replay_guard_suppress(_st42, _cid42, _nm42, _dir42, "",
                                      _logs42.append, None) is False
        # (F) broken store (find raises) -> fail-open, never mask a solve
        class _BrokenStore42:
            def find_replay_prior(self, *a, **k):
                raise RuntimeError("store down")
        assert _replay_guard_suppress(_BrokenStore42(), _cid42, _nm42,
                                      _dir42, _fl42, _logs42.append,
                                      None) is False
        # (G) brand-new triple (first solve) -> passes
        assert _st42.find_replay_prior("fresh-never-seen-42",
                                       _dir42, "flag{first_42}", 999) is None
        # (H) end-to-end through do_solve on an explicit scratch main DB:
        # run 1 = fresh credit (rc 0, row solved=1); run 2 on the SAME
        # dir = replay (row written then suppressed solved=0, rc 1).
        _e2e42 = os.path.join(_r4142, "e2e")
        _ch42 = os.path.join(_e2e42, "chall")
        os.makedirs(_ch42)
        for _src42 in ("challenge.txt", "message.txt"):
            shutil.copyfile(os.path.join(PROJECT_ROOT, "tests", "challs",
                                        "01_b64chain", _src42),
                            os.path.join(_ch42, _src42))
        _saved_db42 = os.environ.get("CTFPILOT_DB")
        try:
            import sqlite3 as _sq42
            os.environ["CTFPILOT_DB"] = os.path.join(_e2e42, "main.sqlite")
            _args42 = _NS(dir=_ch42, mode="fast", write_trace=False,
                          benchmark=False, json=False, flag_regex=None,
                          name=None)
            _rc42a = do_solve(_args42, cfg)
            assert _rc42a == 0, _rc42a
            _con42 = _sq42.connect(os.environ["CTFPILOT_DB"])
            _rows42a = _con42.execute(
                "SELECT id, solved, flag FROM challenges "
                "ORDER BY id").fetchall()
            assert len(_rows42a) == 1 and _rows42a[0][1] == 1 \
                and _rows42a[0][2] == "flag{f4st_p4th_b64_w1n}", _rows42a
            _con42.close()
            _rc42b = do_solve(_args42, cfg)
            assert _rc42b == 1, _rc42b
            _con42 = _sq42.connect(os.environ["CTFPILOT_DB"])
            _rows42b = _con42.execute(
                "SELECT id, solved, flag FROM challenges "
                "ORDER BY id").fetchall()
            assert len(_rows42b) == 2 and _rows42b[0][1] == 1 \
                and _rows42b[1][1] == 0 and _rows42b[1][2] == "", _rows42b
            _guard42 = _con42.execute(
                "SELECT error_class FROM attempts a JOIN challenges c "
                "ON c.id=a.challenge_id WHERE c.id=? AND a.error_class=?",
                (_rows42b[1][0], "semantic:replay-duplicate")).fetchone()
            assert _guard42 is not None
            _con42.close()
            # (I) --allow-replay is the informed exit: the same replay with
            # allow_replay=True skips the guard entirely and keeps the exact
            # incumbent write behavior (third row credited solved=1, rc 0).
            _args42r = _NS(dir=_ch42, mode="fast", write_trace=False,
                           benchmark=False, json=False, flag_regex=None,
                           name=None, allow_replay=True)
            _rc42c = do_solve(_args42r, cfg)
            assert _rc42c == 0, _rc42c
            _con42 = _sq42.connect(os.environ["CTFPILOT_DB"])
            _rows42c = _con42.execute(
                "SELECT id, solved, flag FROM challenges "
                "ORDER BY id").fetchall()
            assert len(_rows42c) == 3 and _rows42c[2][1] == 1 \
                and _rows42c[2][2] == "flag{f4st_p4th_b64_w1n}", _rows42c
            _con42.close()
        finally:
            if _saved_db42 is None:
                os.environ.pop("CTFPILOT_DB", None)
            else:
                os.environ["CTFPILOT_DB"] = _saved_db42
        mod_results.append(True)
    except Exception:
        log("selftest evolve43-replayguard failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-44 / rsi-116] PDF FlateDecode text extraction
        # (writeup-in-archive family). RED (live): 2024-OMCTF writeup.pdf
        # 1.37MB, engine 1.87s / 15 attempts NOT SOLVED while flag
        # flag{WelcOmeTO} rides a CID-encoded hex-Tj operand inside a
        # FlateDecode stream -- structurally invisible to every byte-level
        # detector (probe D: all needles ABSENT from all 57 inflated
        # spans); the model channel needed pypdf + ~60s. GREEN (live,
        # clean carrier copy): pdf_text_scan reassembles 9 ToUnicode
        # CMaps + hex-Tj ops and solves in 0.95 s / 10 attempts. Faces:
        # zero-trigger on non-PDF, pipeline-A plain-ASCII stream, CID
        # bfchar mapping, bfrange triple + bfchar mixed CMap, literal-Tj
        # octal escapes WITHOUT any CMap, decompression-bomb cap,
        # endstream-phantom isolation across two real streams, and the
        # live-carrier wall (workspace positions only).
        from fastpath.detectors import pdf_text_scan
        import zlib as _z44
        _t44 = os.path.join(scratch, "evolve44-pdf")
        if os.path.isdir(_t44):
            shutil.rmtree(_t44)
        os.makedirs(_t44)
        _gc44 = FlagChecker(compiled_flag_patterns(cfg))

        def _mkpdf44(path, payloads):
            pdf = b"%PDF-1.4\n"
            for _n44, _pl44 in enumerate(payloads, 1):
                pdf += (b"%d 0 obj\n<< /Length %d >>\nstream\n"
                        % (_n44, len(_pl44))) + _pl44 \
                    + b"\nendstream\nendobj\n"
            with open(path, "wb") as fh:
                fh.write(pdf)

        # (A) non-PDF input: zero-trigger, note is the no-candidate string
        _txt44 = os.path.join(_t44, "note.txt")
        with open(_txt44, "wb") as fh:
            fh.write(b"plain text challenge with flag{plain_txt_44} inside")
        _fA44, _nA44 = pdf_text_scan(_gc44, [{"path": "note.txt",
                                               "abs": _txt44, "size": 46,
                                               "magic": None}])
        assert _fA44 is None and _nA44 == "no PDF text-stream candidate", \
            (_fA44, _nA44)
        # (B) pipeline A: plain-ASCII FlateDecode stream carries the flag
        _payB44 = _z44.compress(b"BT /F1 12 Tf (flag{pdf_pl41n_44}) Tj ET")
        _pB44 = os.path.join(_t44, "plain.pdf")
        _mkpdf44(_pB44, [_payB44])
        _fB44, _nB44 = pdf_text_scan(_gc44, [{"path": "plain.pdf",
                                               "abs": _pB44, "size": 1,
                                               "magic": "pdf"}])
        assert _fB44 == "flag{pdf_pl41n_44}", (_fB44, _nB44)
        # (C) pipeline B: CID hex-Tj operands through a bfchar CMap
        _flC44 = "flag{pdf_c1d_44}"
        _ent44 = []
        _ops44 = []
        for _i44, _ch44 in enumerate(_flC44):
            _cid44 = 0x20 + _i44
            _ent44.append(b"<%04x> <%s>" % (
                _cid44, _ch44.encode("utf-16-be").hex().encode()))
            _ops44.append(b"<%04x> Tj" % _cid44)
        _cmapC44 = (b"/CIDInit begincmap beginbfchar\n" + b"\n".join(_ent44)
                    + b"\nendbfchar endcmap\n")
        _csC44 = b"BT /F1 12 Tf " + b" ".join(_ops44) + b" ET"
        _pC44 = os.path.join(_t44, "cid.pdf")
        _mkpdf44(_pC44, [_z44.compress(_cmapC44), _z44.compress(_csC44)])
        _fC44, _nC44 = pdf_text_scan(_gc44, [{"path": "cid.pdf",
                                               "abs": _pC44, "size": 1,
                                               "magic": "pdf"}])
        assert _fC44 == _flC44, (_fC44, _nC44)
        # (D) mixed CMap: bfchar head/tail + bfrange triple middle
        # ('flag{' and '}' via bfchar, contiguous 'hijkl' via bfrange)
        _flD44 = "flag{hijkl}"
        _cmapD44 = (b"begincmap beginbfchar\n"
                    b"<0040> <0066>\n<0041> <006c>\n<0042> <0061>\n"
                    b"<0043> <0067>\n<0044> <007b>\n<0050> <007d>\n"
                    b"endbfchar beginbfrange\n"
                    b"<0100> <0104> <0068>\nendbfrange endcmap\n")
        _opsD44 = (b"<0040> Tj <0041> Tj <0042> Tj <0043> Tj <0044> Tj "
                   b"<0100> Tj <0101> Tj <0102> Tj <0103> Tj <0104> Tj "
                   b"<0050> Tj")
        _csD44 = b"BT /F1 12 Tf " + _opsD44 + b" ET"
        _pD44 = os.path.join(_t44, "range.pdf")
        _mkpdf44(_pD44, [_z44.compress(_cmapD44), _z44.compress(_csD44)])
        _fD44, _nD44 = pdf_text_scan(_gc44, [{"path": "range.pdf",
                                               "abs": _pD44, "size": 1,
                                               "magic": "pdf"}])
        assert _fD44 == _flD44, (_fD44, _nD44)
        # (E) literal-Tj escapes with NO CMap: octal \147 = 'g',
        # backslash-escaped braces; unescape-only reassembly must hit
        _csE44 = b"BT /F1 12 Tf (fla\\147\\{esc_l1t_44\\}) Tj ET"
        _pE44 = os.path.join(_t44, "esc.pdf")
        _mkpdf44(_pE44, [_z44.compress(_csE44)])
        _fE44, _nE44 = pdf_text_scan(_gc44, [{"path": "esc.pdf",
                                              "abs": _pE44, "size": 1,
                                              "magic": "pdf"}])
        assert _fE44 == "flag{esc_l1t_44}", (_fE44, _nE44)
        # (F) decompression-bomb cap: a span inflating past
        # PDF_SPAN_DEC_CAP is dropped; scan finishes with the honest note
        _bomb44 = _z44.compress(b"A" * (68 * 1024 * 1024))
        _pF44 = os.path.join(_t44, "bomb.pdf")
        _mkpdf44(_pF44, [_bomb44])
        _fF44, _nF44 = pdf_text_scan(_gc44, [{"path": "bomb.pdf",
                                              "abs": _pF44, "size": 1,
                                              "magic": "pdf"}])
        assert _fF44 is None and "no inflatable stream" in _nF44, \
            (_fF44, _nF44)
        # (G) endstream-phantom isolation: two real streams; every
        # 'endstream\nendobj' tail spawns a phantom match and the SECOND
        # real stream must still inflate and solve
        _s1G44 = _z44.compress(b"BT /F1 12 Tf (no flag here at all) Tj ET")
        _s2G44 = _z44.compress(b"BT /F1 12 Tf (flag{ph4nt0m_1s0l4t3d_44}) Tj ET")
        _pG44 = os.path.join(_t44, "two.pdf")
        _mkpdf44(_pG44, [_s1G44, _s2G44])
        _fG44, _nG44 = pdf_text_scan(_gc44, [{"path": "two.pdf",
                                              "abs": _pG44, "size": 1,
                                              "magic": "pdf"}])
        assert _fG44 == "flag{ph4nt0m_1s0l4t3d_44}", (_fG44, _nG44)
        # (H) live-carrier wall (workspace positions only; isolated
        # copies skip): the 1.37 MB real writeup must reassemble the
        # flag in < 2.5 s
        _car44 = os.path.join(os.path.dirname(PROJECT_ROOT), "ctf-feed",
                              "2024-OMCTF", "writeup.pdf")
        if os.path.exists(_car44):
            import time as _tm44
            _t0H44 = _tm44.perf_counter()
            _fH44, _nH44 = pdf_text_scan(_gc44, [
                {"path": "writeup.pdf", "abs": _car44,
                 "size": os.path.getsize(_car44), "magic": "pdf"}])
            _dtH44 = _tm44.perf_counter() - _t0H44
            assert _fH44 == "flag{WelcOmeTO}", (_fH44, _nH44)
            assert _dtH44 < 2.5, \
                "carrier wall %.3fs must stay < 2.5s" % _dtH44
            log("evolve44 pdf carrier wall: %.3fs" % _dtH44)
        mod_results.append(True)
    except Exception:
        log("selftest evolve44-pdf-text failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261117-46 / rsi-118] engine-authored sidecar
        # isolation, both sides of the boundary. RED (live finding B,
        # entry-66): the incumbent solved 2024-OMCTF via pdf-text
        # CID-reassembly and the raw-echo compare face retracted the
        # DERIVED credit against the account-model sidecar (a flag
        # parked by the model channel, verbatim-absent from all real
        # challenge material); RED (hole-A shape): the deep printable
        # strings scan still READ the sidecar bytes and manufactured a
        # fake candidate only the exit guard caught.
        # (1) input side: every mock recon strategy that prints file
        # content must filter _ctfpilot paths (read+strings already
        # did; the printable strings scan gap is closed this round) --
        # a scanner must never read the answer sheet in the first place.
        from deep import planner as _pl46
        _r46 = {"category": "misc", "confidence": 0.5, "evidence": [],
                "files": [], "summary": ""}
        # (2) probe dir FIRST: the strategy code globs the chall_dir
        # baked into its source string, so the dir must exist before
        # the subprocess behavioral proof runs.
        import subprocess as _sp46
        _t46 = os.path.join(scratch, "evolve46-probe")
        if os.path.isdir(_t46):
            shutil.rmtree(_t46)
        os.makedirs(_t46)
        with open(os.path.join(_t46, "readme46.txt"), "w",
                  encoding="utf-8") as _f46:
            _f46.write("dumb statement line for the probe dir\n")
        with open(os.path.join(_t46, "_ctfpilot_model_result.json"),
                  "w", encoding="utf-8") as _f46:
            json.dump({"solved": True, "flag": "flag{never_read_46}",
                       "time_seconds": 1.0}, _f46)
        # (1) source-level input face: the printable strings scan (the
        # one gap, live RED this round) and read+strings (pre-existing
        # filter) must both carry the _ctfpilot path filter.
        _a46, _e46 = _pl46._mock_strategy("misc", _r46, 1, None, [],
                                          _t46)
        assert _a46 and _a46.get("action") == "python", \
            "mock step-1 (printable strings scan) must yield a python action"
        assert "'_ctfpilot' not in p" in _a46["code"], \
            "printable strings scan must filter _ctfpilot paths (rsi-118)"
        _a0_46, _ = _pl46._mock_strategy("misc", _r46, 0, None, [],
                                         _t46)
        assert "'_ctfpilot' not in p" in _a0_46["code"], \
            "read+strings all files must keep its _ctfpilot filter"
        # (2b) behavioral input proof: run the strings-scan code in the
        # probe dir; the printed stream must contain the attachment path
        # and must NOT contain the sidecar path nor its flag bytes.
        _p46 = _sp46.run([sys.executable, "-X", "utf8", "-c",
                          _a46["code"]], capture_output=True,
                          timeout=60)
        _o46 = _p46.stdout.decode("utf-8", "replace")
        assert "readme46.txt" in _o46, "strings scan must read real material"
        assert "_ctfpilot_model_result.json" not in _o46 and \
            "flag{never_read_46}" not in _o46, \
            "strings scan must not read the sidecar (rsi-118 input face)"
        # (3) compare side: flag_in_raw exempts the sidecar exactly like
        # the other _ctfpilot* byproducts, while REAL challenge material
        # in the same dir still raw-echoes (the honest-credit face is
        # unchanged: a verbatim flag in an original file stays a retract).
        from core.flagcheck import flag_in_raw
        _hitB46, _srcB46 = flag_in_raw(_t46, "flag{never_read_46}")
        assert not _hitB46, \
            "sidecar must be exempt from the raw-echo compare face"
        with open(os.path.join(_t46, "orig46.txt"), "w",
                  encoding="utf-8") as _f46:
            _f46.write("the real material ships flag{or1g_m4t_46} verbatim\n")
        _hitC46, _srcC46 = flag_in_raw(_t46, "flag{or1g_m4t_46}")
        assert _hitC46 and _srcC46 == "orig46.txt", \
            "a verbatim flag in ORIGINAL material must still raw-echo"
        mod_results.append(True)
    except Exception:
        log("selftest evolve46-sidecar failed:"); traceback.print_exc()
        mod_results.append(False)
    try:
        # [evolve-20261118-03 / rsi-122] compressed-tarball carrier
        # recognition + tar-slip guard in the deep unpack strategy.
        # RED (live, this feed round): n1ctf-2025-bridge 214KB
        # bridge.tar.gz (21 members: Anchor Rust source, server flag
        # service, challenge.so ELF) was never opened -- the strategy's
        # elif arm tested p.endswith('.tar') only, so .tar.gz/.tgz/
        # .tar.bz2/.tar.xz carriers were silently skipped (unpack step
        # out=19 chars; the wrap guard then halted the ladder on the
        # unchanged file set). The gap is wider than the extension
        # face: an isolated-root probe shows the incumbent fastpath
        # decompress+chain does NOT solve a .tar.gz with a PLAIN-TEXT
        # flag member either (U/10at; the decompressed tar stream is
        # not chain-decoded to a flag), so the whole compressed-tarball
        # carrier family was invisible. Second RED in the same arm:
        # bare extractall had no traversal filter, so a plain .tar with
        # a '../' member escaped the outdir on the incumbent (live
        # probe: escaped_49.txt landed one level above _extracted/).
        # GREEN faces: (A) end-to-end solve flips on a .tar.gz carrier
        # whose flag rides a nested-zip member (raw tar stream carries
        # zip binary, so only real extraction exposes it); (B) is_tarfile
        # recognition matrix incl. non-tar .gz False and zip False;
        # (C) tar-slip members fail closed on BOTH arms under
        # filter='data' -- benign members still extract, nothing
        # escapes the outdir.
        import io as _io49
        import tarfile as _tf49
        import gzip as _gz49
        import zipfile as _zf49
        d49 = os.path.join(scratch, "evolve49-targz")
        os.makedirs(d49, exist_ok=True)

        def _mk49(path, members, mode="w:gz"):
            with _tf49.open(path, mode) as t49:
                for name49, data49 in members:
                    inf49 = _tf49.TarInfo(name49)
                    inf49.size = len(data49)
                    t49.addfile(inf49, _io49.BytesIO(data49))

        # (A) end-to-end flip: nested-zip member carrier
        dA49 = os.path.join(d49, "carrier-targz")
        os.makedirs(dA49, exist_ok=True)
        zb49 = _io49.BytesIO()
        with _zf49.ZipFile(zb49, "w", _zf49.ZIP_DEFLATED) as z49:
            z49.writestr("dist/secret.txt",
                         b"layer two\nflag{t4rgz_c4rr13r_49}\n")
        _mk49(os.path.join(dA49, "handout.tar.gz"),
              [("dist/payload.zip", zb49.getvalue()),
               ("dist/readme.txt", b"unzip the payload for the secret\n")])
        rc49 = do_solve(_NS(dir=dA49, mode="auto", brain="mock",
                            name="evolve49-targz", flag_regex=None,
                            write_trace=False, allow_replay=True), cfg)
        assert rc49 == 0, "tar.gz member flag must derive end-to-end"
        row49 = get_stores(cfg).prior_attempt("evolve49-targz")
        assert row49 and row49["solved"] == 1, "derived flag credited"
        assert row49["flag"] == "flag{t4rgz_c4rr13r_49}", "derived flag value"
        # (B) recognition matrix on the REAL stdlib helper the strategy
        # now calls: compressed tarball family True, plain .tar True,
        # non-tar .gz False (stays on the fastpath decompress face),
        # zip False (stays on the is_zipfile arm ahead of the tar arm)
        mat49 = os.path.join(d49, "matrix")
        os.makedirs(mat49, exist_ok=True)
        _mk49(os.path.join(mat49, "a.tgz"), [("x.txt", b"x")], "w:gz")
        _mk49(os.path.join(mat49, "b.tar"), [("x.txt", b"x")], "w")
        _mk49(os.path.join(mat49, "c.tar.bz2"), [("x.txt", b"x")], "w:bz2")
        _mk49(os.path.join(mat49, "d.tar.xz"), [("x.txt", b"x")], "w:xz")
        with open(os.path.join(mat49, "e.gz"), "wb") as f49:
            f49.write(_gz49.compress(b"plain gzip handout, not a tar"))
        with _zf49.ZipFile(os.path.join(mat49, "f.zip"), "w") as z49:
            z49.writestr("x.txt", b"x")
        for fname49, want49 in (("a.tgz", True), ("b.tar", True),
                                ("c.tar.bz2", True), ("d.tar.xz", True),
                                ("handout.tar.gz", True),
                                ("e.gz", False), ("f.zip", False)):
            p49 = os.path.join(dA49 if fname49 == "handout.tar.gz"
                               else mat49, fname49)
            assert _tf49.is_tarfile(p49) is want49, \
                "is_tarfile(%s) must be %s" % (fname49, want49)
        # (C) tar-slip fail-closed on BOTH tar arms: the evil member
        # raises under filter='data', benign members still extract,
        # nothing escapes the outdir (incumbent wrote the escape live)
        for sub49, fname49, esc49, mode49 in (
                ("slip-tar", "evil.tar", "esc49a.txt", "w"),
                ("slip-tgz", "evil.tar.gz", "esc49b.txt", "w:gz")):
            dC49 = os.path.join(d49, sub49)
            os.makedirs(dC49, exist_ok=True)
            _mk49(os.path.join(dC49, fname49),
                  [("good.txt", b"benign\n"),
                   ("../" + esc49, b"must not land outside")], mode49)
            out49 = os.path.join(dC49, "_extracted")
            raised49 = False
            try:
                _tf49.open(os.path.join(dC49, fname49)).extractall(
                    out49, filter="data")
            except Exception:
                raised49 = True
            assert raised49, \
                "tar-slip member must raise under filter='data' (%s)" % sub49
            assert not os.path.exists(os.path.join(dC49, esc49)), \
                "no member may escape the outdir (%s)" % sub49
            assert os.path.isfile(os.path.join(out49, "good.txt")), \
                "benign member still extracts before the evil one"
        mod_results.append(True)
    except Exception:
        log("selftest evolve49-targz failed:"); traceback.print_exc()
        mod_results.append(False)
    # [evolve-20261118-05 / rsi-124] harvest window: the unpack _peek
    # face printed 'EXTRACTED' lines and 4000B content per member in
    # glob order, so on a many-member archive the sandbox's 20000-char
    # step window truncated before a late flag member's content could
    # enter the observation stream (wrap read+strings burns its window
    # on the first ~10 members the same way). RED (isolated inc root,
    # this round): a 25-member tar.gz AND zip whose only flag member
    # sorts last (z25.txt) stay NOT SOLVED end-to-end (rc=1, 14
    # attempts each). GREEN: _peek now prints cheap census lines first,
    # then content in flag-pattern-hit-first order (ordering only,
    # never a filter -- no-hit members keep exact glob order). Faces:
    # (A) tar.gz carrier flip, (B) zip carrier flip, (C) benign
    # single-member zip zero-regression (stored UNCOMPRESSED zip would
    # carry the flag verbatim in the archive bytes -> raw-echo guard
    # retract; all fixtures use ZIP_DEFLATED like real handouts).
    try:
        import io as _io50
        import tarfile as _tf50
        import zipfile as _zf50
        _FLAG50 = b"flag{h4rv3st_w1nd0w_50}\n"

        def _mem50():
            ms50 = []
            for _i50 in range(1, 25):
                ms50.append(("m%02d.txt" % _i50,
                             (("F" * 660 + "\n").encode()) * 3))
            ms50.append(("z25.txt", _FLAG50 + b"Z" * 200))
            return ms50

        d50 = os.path.join(scratch, "evolve50-harvest")
        # (A) tar.gz carrier flip
        dA50 = os.path.join(d50, "targz")
        os.makedirs(dA50, exist_ok=True)
        with _tf50.open(os.path.join(dA50, "handout.tar.gz"), "w:gz") as t50:
            for _n50, _b50 in _mem50():
                _in50 = _tf50.TarInfo(_n50)
                _in50.size = len(_b50)
                t50.addfile(_in50, _io50.BytesIO(_b50))
        rc50 = do_solve(_NS(dir=dA50, mode="auto", brain="mock",
                            name="evolve50-harvest-targz", flag_regex=None,
                            write_trace=False, allow_replay=True), cfg)
        assert rc50 == 0, \
            "25-member tar.gz late flag member must survive the window"
        row50 = get_stores(cfg).prior_attempt("evolve50-harvest-targz")
        assert row50 and row50["solved"] == 1, "derived flag credited (targz)"
        assert row50["flag"] == "flag{h4rv3st_w1nd0w_50}", "flag value (targz)"
        # (B) zip carrier flip
        dB50 = os.path.join(d50, "zip")
        os.makedirs(dB50, exist_ok=True)
        with _zf50.ZipFile(os.path.join(dB50, "handout.zip"), "w",
                           _zf50.ZIP_DEFLATED) as z50:
            for _n50, _b50 in _mem50():
                z50.writestr(_n50, _b50)
        rc50b = do_solve(_NS(dir=dB50, mode="auto", brain="mock",
                             name="evolve50-harvest-zip", flag_regex=None,
                             write_trace=False, allow_replay=True), cfg)
        assert rc50b == 0, \
            "25-member zip late flag member must survive the window"
        row50b = get_stores(cfg).prior_attempt("evolve50-harvest-zip")
        assert row50b and row50b["solved"] == 1, "derived flag credited (zip)"
        assert row50b["flag"] == "flag{h4rv3st_w1nd0w_50}", "flag value (zip)"
        # (C) benign single-member zip: the priority reorder must not
        # move a flag out of a window it already fit in
        dC50 = os.path.join(d50, "benign")
        os.makedirs(dC50, exist_ok=True)
        with _zf50.ZipFile(os.path.join(dC50, "handout.zip"), "w",
                           _zf50.ZIP_DEFLATED) as z50:
            z50.writestr("dist/secret.txt",
                         b"plain handout\nflag{b3n1gn_z1p_50}\n")
        rc50c = do_solve(_NS(dir=dC50, mode="auto", brain="mock",
                             name="evolve50-harvest-benign", flag_regex=None,
                             write_trace=False, allow_replay=True), cfg)
        assert rc50c == 0, "benign single-member zip must stay solved"
        row50c = get_stores(cfg).prior_attempt("evolve50-harvest-benign")
        assert row50c and row50c["solved"] == 1, "benign flag credited"
        assert row50c["flag"] == "flag{b3n1gn_z1p_50}", "benign flag value"
        mod_results.append(True)
    except Exception:
        log("selftest evolve50-harvest failed:"); traceback.print_exc()
        mod_results.append(False)
    # [evolve-20261118-06 / rsi-125] pull-chain fail-fast budget: api.github.com
    # latency blips made bench-pull (30s x3-retry per request worst case,
    # 6-8 serial requests) blow through the 300s outer tool timeout with
    # zero progress output, killing the process AFTER bytes landed but
    # BEFORE the ledger write -- two consecutive rounds (n1flgsrv
    # 2026-11-18 00:23, N1drone 01:01) each needed a manual ledger repair.
    # GREEN faces (all zero-network, ms-scale): (A) _budget_exceeded
    # three-state boundary; (B) _api refuses a fresh round trip once the
    # deadline is past (raise BEFORE urlopen, no wire time); (C) budget
    # exhaustion propagates OUT of _walk_files' tolerance except (it must
    # not read as an empty walk -> 'no downloadable files'); (D) gh_pull
    # pre-flight check refuses an already-expired deadline without
    # touching the network; (E) _pull_log appends a timestamped line that
    # survives outer-timeout stdout loss; (F) deadline threads unchanged
    # through _pick_challenge -> _entries (mock capture).
    try:
        from core import ghsource as GH51
        # (A) boundary: no deadline -> never exceeded; future -> no;
        # past -> yes.
        assert GH51._budget_exceeded(None) is False
        assert GH51._budget_exceeded(time.time() + 60) is False
        assert GH51._budget_exceeded(time.time() - 60) is True
        # (B) _api fails fast BEFORE any wire time once past deadline.
        _err51 = None
        try:
            GH51._api("/repos/x/y/contents", deadline=time.time() - 1)
        except GH51.SourceError as e51:
            _err51 = str(e51)
        assert _err51 and "budget" in _err51.lower(), \
            "expired deadline must fail fast with a budget error"
        # (C) budget exhaustion propagates out of _walk_files tolerance:
        # a SourceError under an expired deadline re-raises instead of
        # reading as an empty walk.
        _orig_e51 = GH51._entries
        def _boom51(owner_repo, subpath, token="", deadline=None):
            raise GH51.SourceError("simulated wire failure")
        GH51._entries = _boom51
        try:
            _prop51 = None
            try:
                GH51._walk_files("o/r", "p", "tok",
                                 deadline=time.time() - 1)
            except GH51.SourceError as e51:
                _prop51 = str(e51)
            assert _prop51, \
                "budget-timeout walk failure must propagate, not swallow"
        finally:
            GH51._entries = _orig_e51
        # (C2) plain wire failure under a LIVE deadline still tolerates
        # (returns empty -- original semantics preserved).
        GH51._entries = _boom51
        try:
            assert GH51._walk_files("o/r", "p", "tok",
                                    deadline=time.time() + 60) == []
        finally:
            GH51._entries = _orig_e51
        # (D) gh_pull pre-flight refuses an expired deadline with zero
        # network calls (no _token subprocess either, thanks to ordering:
        # the check runs first).
        _err51b = None
        try:
            GH51.gh_pull("gh:o/r", os.path.join(scratch, "ev51-nodest"),
                         set(), os.path.join(scratch, "ev51-cache"),
                         precheck=None, deadline=time.time() - 1)
        except GH51.SourceError as e51:
            _err51b = str(e51)
        assert _err51b and "budget" in _err51b.lower() and \
            "pre-flight" in _err51b, \
            "expired deadline must fail fast at pre-flight"
        # (E) _pull_log appends a timestamped line (survives outer-timeout
        # stdout loss; IO failure is best-effort and must not raise).
        _d51 = os.path.join(scratch, "ev51-logdest")
        os.makedirs(_d51, exist_ok=True)
        _pull_log(_d51, "verify rsi-125 line")
        _logpath51 = os.path.join(_d51, "_bench_pull.log")
        assert os.path.isfile(_logpath51), "pull log must land on disk"
        _line51 = open(_logpath51, encoding="utf-8").read().strip()
        assert _line51.endswith("verify rsi-125 line") and \
            len(_line51) > len("verify rsi-125 line"), \
            "log line must carry a timestamp prefix"
        # (F) deadline threads through _pick_challenge -> _entries.
        _cap51 = {}
        def _cap_entries51(owner_repo, subpath, token="", deadline=None):
            _cap51["deadline"] = deadline
            return []
        GH51._entries = _cap_entries51
        try:
            _dl51 = time.time() + 120
            GH51._pick_challenge("o/r", set(), "tok", deadline=_dl51)
            assert _cap51.get("deadline") == _dl51, \
                "deadline must pass through _pick_challenge untouched"
        finally:
            GH51._entries = _orig_e51
        mod_results.append(True)
    except Exception:
        log("selftest evolve51-pullbudget failed:"); traceback.print_exc()
        mod_results.append(False)

    # [evolve-20261118-07 / rsi-126] category drain rotation: a drained
    # category must rotate to the next category, not exhaust the whole
    # repo. Live-fire 2026-11-18 01:21 on Nu1LCTF/n1ctf-2025: with
    # misc(1)/pwn(2) pulled and crypto(3)/web(4) fresh, a random pick that
    # landed on a drained category returned None at the fresh_sub check
    # and bench-pull burned the whole pull to a false 'repo exhausted'
    # (2s, attempt=0, no precheck-duplicate evidence); a 400-seed replay
    # on the real repo shape measured 49% false-None on the incumbent
    # while 7 challenges stayed fresh. GREEN faces (zero-network):
    # (A) partial drain: 120-seed sweep never returns None and every key
    # lands inside the 7 fresh challenges; (B) full drain keeps the true
    # exhausted verdict; (C) single-category repo: drained -> None,
    # fresh -> picked (old semantics kept); (D) non-category root (root
    # dirs are the challenges) still filters used keys and picks fresh.
    try:
        from core import ghsource as GH52
        _orig_e52, _orig_w52 = GH52._entries, GH52._walk_files
        _REPO52 = "Nu1LCTF/n1ctf-2025"
        _L52 = {
            "": [
                {"type": "dir", "name": "crypto", "path": "crypto", "size": 0},
                {"type": "dir", "name": "misc", "path": "misc", "size": 0},
                {"type": "dir", "name": "pwn", "path": "pwn", "size": 0},
                {"type": "dir", "name": "web", "path": "web", "size": 0},
            ],
            "crypto": [
                {"type": "dir", "name": "n1fhe", "path": "crypto/n1fhe", "size": 0},
                {"type": "dir", "name": "n1fsr", "path": "crypto/n1fsr", "size": 0},
                {"type": "dir", "name": "n1share", "path": "crypto/n1share", "size": 0},
            ],
            "misc": [{"type": "dir", "name": "bridge", "path": "misc/bridge", "size": 0}],
            "pwn": [
                {"type": "dir", "name": "N1drone", "path": "pwn/N1drone", "size": 0},
                {"type": "dir", "name": "n1flgsrv", "path": "pwn/n1flgsrv", "size": 0},
            ],
            "web": [
                {"type": "dir", "name": "eezzjs", "path": "web/eezzjs", "size": 0},
                {"type": "dir", "name": "n1cat", "path": "web/n1cat", "size": 0},
                {"type": "dir", "name": "n1saml", "path": "web/n1saml", "size": 0},
                {"type": "dir", "name": "this-challenge-has-been-ruined",
                 "path": "web/this-challenge-has-been-ruined", "size": 0},
            ],
        }
        for _s52 in ("crypto/n1fhe", "crypto/n1fsr", "crypto/n1share",
                     "misc/bridge", "pwn/N1drone", "pwn/n1flgsrv",
                     "web/eezzjs", "web/n1cat", "web/n1saml",
                     "web/this-challenge-has-been-ruined"):
            _L52[_s52] = [{"type": "file", "name": "task.txt",
                           "path": _s52 + "/task.txt", "size": 123}]
        GH52._entries = lambda owner_repo, subpath, token="", deadline=None: \
            [dict(e) for e in _L52[subpath]]
        GH52._walk_files = lambda owner_repo, subpath, token="", depth=0, \
            budget=None, deadline=None: [dict(e) for e in _L52.get(subpath, [])]
        try:
            _used52 = {_REPO52 + "::bridge", _REPO52 + "::n1flgsrv",
                       _REPO52 + "::N1drone"}
            _fresh52 = {"n1fhe", "n1fsr", "n1share", "eezzjs", "n1cat",
                        "n1saml", "this-challenge-has-been-ruined"}
            # (A) partial drain: the sweep must never report exhausted
            # while 7 fresh challenges remain, and keys must stay fresh.
            for _seed in range(120):
                random.seed(_seed)
                _r52 = GH52._pick_challenge(_REPO52, set(_used52), "tok")
                assert _r52 is not None, (
                    "seed %d: false exhausted with 7 fresh challenges"
                    % _seed)
                assert _r52[0].split("::")[1] in _fresh52, (
                    "seed %d: key %r escaped the fresh set"
                    % (_seed, _r52[0]))
            # (B) full drain: the true exhausted verdict must survive.
            _full52 = set(_used52) | {"%s::%s" % (_REPO52, n) for n in _fresh52}
            for _seed in range(30):
                random.seed(_seed)
                assert GH52._pick_challenge(_REPO52, set(_full52), "tok") \
                    is None, "seed %d: full drain must exhaust" % _seed
        finally:
            GH52._entries = _orig_e52
            GH52._walk_files = _orig_w52
        # (C) single-category repo: drained -> None (old semantics),
        # fresh -> picked.
        _S1_52 = {
            "": [{"type": "dir", "name": "misc", "path": "misc", "size": 0}],
            "misc": [{"type": "dir", "name": "bridge", "path": "misc/bridge",
                       "size": 0}],
            "misc/bridge": [{"type": "file", "name": "task.txt",
                             "path": "misc/bridge/task.txt", "size": 123}],
        }
        GH52._entries = lambda owner_repo, subpath, token="", deadline=None: \
            [dict(e) for e in _S1_52[subpath]]
        try:
            random.seed(0)
            assert GH52._pick_challenge(_REPO52, {_REPO52 + "::bridge"},
                                        "tok") is None, \
                "drained single-category repo must stay exhausted"
            random.seed(1)
            _r52 = GH52._pick_challenge(_REPO52, set(), "tok")
            assert _r52 is not None and _r52[0] == _REPO52 + "::bridge", (
                "fresh single-category pick broke: %r" % (_r52,))
        finally:
            GH52._entries = _orig_e52
        # (D) non-category root (root dirs are the challenges): used
        # keys are filtered at the root level and fresh ones picked.
        _S2_52 = {
            "": [{"type": "dir", "name": "chall1", "path": "chall1", "size": 0},
                 {"type": "dir", "name": "chall2", "path": "chall2", "size": 0}],
            "chall1": [{"type": "file", "name": "f.txt",
                        "path": "chall1/f.txt", "size": 5}],
            "chall2": [{"type": "file", "name": "g.txt",
                        "path": "chall2/g.txt", "size": 5}],
        }
        GH52._entries = lambda owner_repo, subpath, token="", deadline=None: \
            [dict(e) for e in _S2_52[subpath]]
        try:
            for _seed in range(20):
                random.seed(_seed)
                _r52 = GH52._pick_challenge(_REPO52,
                                             {_REPO52 + "::chall1"}, "tok")
                assert _r52 is not None and _r52[0] == _REPO52 + "::chall2", (
                    "seed %d: non-category rotation broke: %r" % (_seed, _r52))
        finally:
            GH52._entries = _orig_e52
        mod_results.append(True)
    except Exception:
        log("selftest evolve52-category-skip failed:"); traceback.print_exc()
        mod_results.append(False)

    # [evolve-20261118-08 / rsi-128] XXL-archive census windowing: entry-76's
    # two-pass _peek prints an UNBOUNDED census (one line per member) before
    # the hit-first content pass; on an XXL tree (live round-95 n1cat WAR:
    # 3512 members, ~140KB census alone) the 20000-char step window is gone
    # before the flag-hit content gets a single line out (isolated w1800-xxl
    # fixture: incumbent unpack out=20001 chars, 41.9s/16 attempts NOT
    # SOLVED; cand 13.3s/8 SOLVED; w25-regress + benign faces zero drift).
    # GREEN faces: (A) 600-filler zip whose only flag member sorts LAST must
    # stay solved -- census now orders flag-hit members first, caps at 80
    # lines, emits one summary line (80 x ~150-char paths + 4000B hit head
    # ~= 16KB worst case < the 20000-char window; cap=120 measurably overflowed
    # on this scratch path); (B) a >cap benign XXL must return honest rc=1
    # (truncation is fail-safe, never a crash).
    try:
        import zipfile as _zf53
        _FLAG53 = b"flag{xxl_c3nsus_w1nd0w_53}\n"
        d53 = os.path.join(scratch, "evolve53-census")
        # (A) 601-member flip: the census alone (~36KB) burns the window on
        # the incumbent; the flag member zflaghost.txt sorts LAST.
        dA53 = os.path.join(d53, "xxl")
        os.makedirs(dA53, exist_ok=True)
        with _zf53.ZipFile(os.path.join(dA53, "handout.zip"), "w",
                           _zf53.ZIP_DEFLATED) as z53:
            for _i53 in range(600):
                z53.writestr("m%04d.txt" % _i53, b"filler member %04d\n" % _i53)
            z53.writestr("zflaghost.txt", _FLAG53 + b"Z" * 200)
        rc53 = do_solve(_NS(dir=dA53, mode="auto", brain="mock",
                            name="evolve53-census-xxl", flag_regex=None,
                            write_trace=False, allow_replay=True), cfg)
        assert rc53 == 0, \
            "XXL 601-member zip late flag member must survive the window"
        row53 = get_stores(cfg).prior_attempt("evolve53-census-xxl")
        assert row53 and row53["solved"] == 1, "derived flag credited (xxl)"
        assert row53["flag"] == "flag{xxl_c3nsus_w1nd0w_53}", "flag value (xxl)"
        # (B) >cap benign XXL: truncation path must fail safe (honest rc=1)
        dB53 = os.path.join(d53, "benign")
        os.makedirs(dB53, exist_ok=True)
        with _zf53.ZipFile(os.path.join(dB53, "handout.zip"), "w",
                           _zf53.ZIP_DEFLATED) as z53b:
            for _i53 in range(130):
                z53b.writestr("b%03d.txt" % _i53,
                              b"benign filler %03d\n" % _i53)
        rc53b = do_solve(_NS(dir=dB53, mode="auto", brain="mock",
                             name="evolve53-census-benign", flag_regex=None,
                             write_trace=False, allow_replay=True), cfg)
        assert rc53b == 1, "benign XXL must stay honest rc=1 (no crash)"
        mod_results.append(True)
    except Exception:
        log("selftest evolve53-census failed:"); traceback.print_exc()
        mod_results.append(False)

    # [evolve-20261118-09 / rsi-129] deferred _peek: one peek per out-dir,
    # at the END of the carrier loop, over the FULL tree. RED (live, feed
    # round-95 n1ctf-2025-n1cat + measured in .temp/rsi-129-xxl-io): the
    # round-2 unpack walk matches 9 nested zip carriers sharing TWO out-dirs,
    # and the incumbent peeks the growing tree once per carrier -- 31512
    # file-visits (46.6s read+regex, Defender first-open included) against
    # the 60s sandbox budget -> second unpack rc=-1, the whole step's
    # observation lost on every wrap round (inc 83.96s/38 attempts vs cand
    # 63.02s/38 attempts on the identity-pair roots; inc unpack #2 detail
    # 'rc=-1 err=timeout', cand unpack #2 'rc=0 err=ok'). The candidate
    # registers each out-dir once and peeks after the loop -- coverage is
    # the final cumulative census (every incumbent peek saw a strict
    # prefix of it), never a filter.
    # GREEN faces: (A) two-level nesting flip (the n1cat shape): outer.zip
    # holds a_inner.zip (500 fillers) + b_inner.zip (500 fillers + the
    # flag member). Every wrap round the incumbent peeks in carrier order,
    # a_inner's peek burns the 20000-char step window and the b_inner
    # full-tree peek is always truncated -- isolated inc root 11.53s/16
    # attempts honest NOT SOLVED; deferred candidate peeks each out-dir
    # once at the end, the hit-first census lands the flag inside the
    # window -- 8.16s/13 attempts SOLVED (flip_test2). (B) shared-out
    # safety: two same-dir zips, the flag rides the SECOND zip -- a
    # first-peek-then-skip regression would lose it; the deferred final
    # census must still see every later-extracted member (0.92s/10
    # attempts SOLVED).
    try:
        import zipfile as _zf54
        import io as _io54
        _FLAG54 = b"flag{p33k_d3dup_54}"
        d54 = os.path.join(scratch, "evolve54-peek")
        # (A) two-level nesting flip: deferred hit-first census must
        # solve what the per-carrier incumbent never saw.
        dA54 = os.path.join(d54, "nested")
        os.makedirs(dA54, exist_ok=True)
        _a54 = _io54.BytesIO()
        with _zf54.ZipFile(_a54, "w", _zf54.ZIP_DEFLATED) as z54:
            for _i54 in range(500):
                z54.writestr("m%04d.txt" % _i54,
                             b"filler member %04d\n" % _i54 * 200)
        _b54 = _io54.BytesIO()
        with _zf54.ZipFile(_b54, "w", _zf54.ZIP_DEFLATED) as z54:
            for _i54 in range(500):
                z54.writestr("n%04d.txt" % _i54,
                             b"filler member %04d\n" % _i54 * 200)
            z54.writestr("zflaghost.txt", _FLAG54 + b"\n" + b"Z" * 100)
        with _zf54.ZipFile(os.path.join(dA54, "outer.zip"), "w",
                           _zf54.ZIP_DEFLATED) as z54:
            z54.writestr("a_inner.zip", _a54.getvalue())
            z54.writestr("b_inner.zip", _b54.getvalue())
        rc54 = do_solve(_NS(dir=dA54, mode="auto", brain="mock",
                            name="evolve54-peek-nested", flag_regex=None,
                            write_trace=False, allow_replay=True), cfg)
        assert rc54 == 0, "nested shared-out tree must solve via deferred peek"
        row54 = get_stores(cfg).prior_attempt("evolve54-peek-nested")
        assert row54 and row54["solved"] == 1, "derived flag credited (nested)"
        assert row54["flag"] == "flag{p33k_d3dup_54}", "flag value (nested)"
        # (B) shared-out safety: flag in the SECOND same-dir zip must stay
        # visible to the deferred final census.
        dB54 = os.path.join(d54, "shared")
        os.makedirs(dB54, exist_ok=True)
        with _zf54.ZipFile(os.path.join(dB54, "a.zip"), "w",
                           _zf54.ZIP_DEFLATED) as z54:
            z54.writestr("a1.txt", b"plain first carrier member\n")
        with _zf54.ZipFile(os.path.join(dB54, "b.zip"), "w",
                           _zf54.ZIP_DEFLATED) as z54:
            z54.writestr("zflag.txt", _FLAG54 + b"\n")
        rc54b = do_solve(_NS(dir=dB54, mode="auto", brain="mock",
                             name="evolve54-peek-shared", flag_regex=None,
                             write_trace=False, allow_replay=True), cfg)
        assert rc54b == 0, "flag in the second same-dir zip must stay visible"
        row54b = get_stores(cfg).prior_attempt("evolve54-peek-shared")
        assert row54b and row54b["flag"] == "flag{p33k_d3dup_54}", \
            "flag value (shared)"
        mod_results.append(True)
    except Exception:
        log("selftest evolve54-peek failed:"); traceback.print_exc()
        mod_results.append(False)

    # [evolve-20261118-10 / rsi-130] caesar stage total-attempt cap: the
    # rsi-109 per-line subsumption only skips the line sweep of a
    # whole-file CLEAN input -- a masked input (whole-file sweep hit a
    # raw-echo or garbage-gated match) falls back to the full per-line
    # sweep with NO bound, and every swept input appends 1 miss note plus
    # up to 25 provenance rows. Live-fire: 2023-ISCC-Analysis 35045
    # caesar rows per solve (x3 replays), 2024-CCB lwewl 29974, 2025-jksn
    # attachments726 16470+14576 -- the 332k-attempt table is 96%+ caesar
    # junk and the inflated counts bred the 01:32:44 rsi-101..105
    # pseudo-batch. The cap charges every caesar/provenance miss row (a
    # credited find returns before any charge), and when dry leaves ONE
    # visible `budget` attempt. Distribution check: median challenge
    # spends 41 caesar+provenance rows, p75=156, 86% stay under 500 --
    # only the explosion family is cut.
    try:
        from fastpath.detectors import (run_fast_path, CAESAR_MAX_ATTEMPTS,
                                         _CAESAR_SHIFT_TABLES)
        assert CAESAR_MAX_ATTEMPTS == 500, "cap constant drifted"
        bb55 = FlagChecker(compiled_flag_patterns(cfg))
        # (a) explosion shape: cipher lines + plaintext bait in ONE file.
        # The whole-file sweep hits a raw-echo find, so rsi-109 marks the
        # input masked and every line falls back to the sweep -- the
        # incumbent writes an unbounded miss+provenance flood; the cap
        # must stop the stage with a single budget note.
        d55 = os.path.join(scratch, "evolve55-caesar-cap")
        os.makedirs(d55, exist_ok=True)
        _tbl55 = _CAESAR_SHIFT_TABLES[1]
        _inv55 = bytearray(range(256))
        for _j55 in range(256):
            _inv55[_tbl55[_j55]] = _j55
        _inv55 = bytes(_inv55)

        def _enc55(s55):
            return s55.encode("utf-8").translate(_inv55).decode("utf-8")
        N55 = 300
        _content55 = "\n".join(_enc55("flag{noise_%d}" % _i55)
                               for _i55 in range(N55)) + "\n" + \
            "\n".join("flag{noise_%d}" % _i55 for _i55 in range(N55)) + "\n"
        _p55 = os.path.join(d55, "big.txt")
        with open(_p55, "w", encoding="utf-8") as _f55:
            _f55.write(_content55)
        _files55 = [{"path": "big.txt", "abs": _p55,
                     "size": len(_content55), "magic": None}]
        _att55 = []
        _got55 = run_fast_path(d55, _files55, bb55, _att55)
        assert _got55 is None, "raw-echo bait must not credit, got %r" % _got55
        _cae55 = [a for a in _att55 if a.detector == "caesar"]
        _bud55 = [a for a in _att55 if a.detector == "budget"]
        # caesar-stage provenance rows carry ' shift=N'; chain/token/idna
        # stage rows carry their own notes instead.
        _cprov55 = [a for a in _att55 if a.detector == "provenance"
                    and " shift=" in a.detail]
        assert len(_bud55) == 1 and "caesar" in _bud55[0].detail, \
            "exhausted cap must leave exactly one caesar budget attempt"
        assert len(_cae55) + len(_cprov55) <= CAESAR_MAX_ATTEMPTS, \
            "caesar stage must stop at the cap (got %d rows)" % \
            (len(_cae55) + len(_cprov55))
        # (b) credit path: a cipher-only fixture must still credit through
        # the capped stage (a real find returns before any charge).
        d55b = os.path.join(scratch, "evolve55-caesar-credit")
        os.makedirs(d55b, exist_ok=True)
        _content55b = "\n".join(
            [_enc55("flag{caesar_credit_path_ok}")] +
            [_enc55("filler line %d aabbcc" % _i55) for _i55 in range(20)]
        ) + "\n"
        _p55b = os.path.join(d55b, "cipher.txt")
        with open(_p55b, "w", encoding="utf-8") as _f55:
            _f55.write(_content55b)
        _files55b = [{"path": "cipher.txt", "abs": _p55b,
                      "size": len(_content55b), "magic": None}]
        _att55b = []
        _got55b = run_fast_path(d55b, _files55b, bb55, _att55b)
        assert _got55b == "flag{caesar_credit_path_ok}", \
            "credit path must survive the cap, got %r" % _got55b
        assert not any(a.detector == "budget" for a in _att55b), \
            "well-under-cap input must not fire the budget note"
        assert any(a.detector == "caesar" and a.success for a in _att55b), \
            "the credited find must be a caesar attempt"
        # (c) normal-size input: clean whole-file sweep keeps rsi-109 line
        # skipping and exact per-candidate miss notes -- no cut.
        d55c = os.path.join(scratch, "evolve55-caesar-normal")
        os.makedirs(d55c, exist_ok=True)
        _p55c = os.path.join(d55c, "small.txt")
        with open(_p55c, "w", encoding="utf-8") as _f55:
            _f55.write("just a few plain lines\nnothing to decode here\n")
        _files55c = [{"path": "small.txt", "abs": _p55c,
                      "size": 52, "magic": None}]
        _att55c = []
        run_fast_path(d55c, _files55c, bb55, _att55c)
        assert not any(a.detector == "budget" for a in _att55c), \
            "normal input must not fire the budget note"
        _miss55 = [a for a in _att55c
                   if a.detector == "caesar" and not a.success]
        assert len(_miss55) == 1, \
            "clean whole-file keeps its ONE miss note and the lines stay " \
            "skipped (rsi-109), got %d" % len(_miss55)
        mod_results.append(True)
    except Exception:
        log("selftest evolve55-caesar-cap failed:"); traceback.print_exc()
        mod_results.append(False)

    # [evolve-20261118-10 / rsi-105 detector / rsi-130 round] small-prime
    # strip arm in fastpath/rsa_numeric.py: N with a labeled prime factor
    # (r4q family, case-d53631fd: r = 128-bit label, q = 12-bit in-table)
    # and N as an all-small-prime product (odd_factor family) must now
    # factor -> phi -> decrypt -> flag; a composite with NO in-table and NO
    # labeled factor must stay an honest miss with a strip note.
    try:
        from fastpath.detectors import run_fast_path
        bb56 = FlagChecker(compiled_flag_patterns(cfg))

        def _mk56(name, body):
            d56 = os.path.join(scratch, name)
            os.makedirs(d56, exist_ok=True)
            p56 = os.path.join(d56, "task.py")
            with open(p56, "w", encoding="utf-8") as f56:
                f56.write(body)
            return d56, [{"path": "task.py", "abs": p56,
                          "size": len(body), "magic": None}]

        def _e56(r56, f56s, phi56, tag56, msg56):
            d56 = pow(65537, -1, phi56)
            c56 = pow(int.from_bytes(msg56, "big"), 65537, r56)
            return _mk56(tag56, f56s + "n = %d\ne = 65537\nc = %d\n" % (r56, c56))

        # (a) r4q family: r = 2^127-1 (Mersenne, deterministic) labeled;
        #     q = 3533 in the 16-bit table, never labeled.
        _r56 = (1 << 127) - 1
        _q56 = 3533
        _N56 = _r56 ** 4 * _q56
        _phi56 = _r56 ** 3 * (_r56 - 1) * (_q56 - 1)
        _msg56 = b"flag{r4q_selftest}"
        d56a, files56a = _e56(_N56, "r = %d\n" % _r56, _phi56,
                              "evolve56-rsasprime-r4q", _msg56)
        att56 = []
        got56 = run_fast_path(d56a, files56a, bb56, att56)
        assert got56 == "flag{r4q_selftest}", "r4q face: %r" % got56
        assert any(a.detector == "rsa-numeric" and a.success for a in att56)

        # (b) odd_factor family: five 40-bit primes labeled in source
        #     (deterministic search), flag fits the 200-bit product.
        _ps56 = []
        _v56 = (1 << 40) + 221
        while len(_ps56) < 5:
            if all(_v56 % _x for _x in range(2, 40)) and \
                    pow(2, _v56 - 1, _v56) == 1 and pow(3, _v56 - 1, _v56) == 1:
                _ps56.append(_v56)
            _v56 += 2
        _N56b = 1
        _phi56b = 1
        for _p56 in _ps56:
            _N56b *= _p56
            _phi56b *= _p56 - 1
        body56b = "p1 = %d\np2 = %d\np3 = %d\np4 = %d\np5 = %d\n" % tuple(_ps56)
        d56b, files56b = _e56(_N56b, body56b, _phi56b,
                              "evolve56-rsasprime-smallprod", b"flag{5pr1m}")
        att56b = []
        got56b = run_fast_path(d56b, files56b, bb56, att56b)
        assert got56b == "flag{5pr1m}", "smallprod face: %r" % got56b

        # (c) cost/miss face: two 33-bit primes (product > 2^63 passes the
        #     scan gate, each > 65536 so in-table never hits, no labels)
        #     -> honest strip miss note, nothing credited.
        _s56 = (1 << 33) + 221        # fixed starting point, deterministic search
        while True:
            if all(_s56 % _x for _x in range(2, 40)) and \
                    pow(2, _s56 - 1, _s56) == 1 and pow(3, _s56 - 1, _s56) == 1:
                break
            _s56 += 2
        _t56 = _s56 + 2000000
        while True:
            if all(_t56 % _x for _x in range(2, 40)) and \
                    pow(2, _t56 - 1, _t56) == 1 and pow(3, _t56 - 1, _t56) == 1:
                break
            _t56 += 2
        d56c, files56c = _mk56("evolve56-rsasprime-miss",
                               "n = %d\ne = 65537\nc = 12345\n" % (_s56 * _t56))
        att56c = []
        got56c = run_fast_path(d56c, files56c, bb56, att56c)
        assert got56c is None, "miss face must not credit: %r" % got56c
        assert any(a.detector == "rsa-numeric" and "strip" in a.detail
                   and not a.success for a in att56c), \
            "miss face must keep its strip note"
        mod_results.append(True)
    except Exception:
        log("selftest evolve56-rsasprime failed:"); traceback.print_exc()
        mod_results.append(False)

    # [evolve-20261118-13 / rsi-133] chain stage-1 total-row cap: stage 1
    # chain-unwraps every text input (whole file + each line) and a flag-
    # shaped plaintext line is found at depth 0 -- a raw-echo find (the bait
    # sits verbatim in the file) appends one provenance row per line with
    # NO bound, the same unbounded shape rsi-130 capped in the caesar stage
    # (the evolve55 600-bait face alone drives 601 stage-1 rows on the
    # incumbent). The cap charges every appended stage-1 row; a credited
    # find returns before any charge; when dry ONE visible `budget`
    # attempt records the cut with the charged-row count.
    try:
        from fastpath.detectors import run_fast_path, CHAIN1_MAX_ATTEMPTS
        assert CHAIN1_MAX_ATTEMPTS == 500, "chain1 cap constant drifted"
        bb58 = FlagChecker(compiled_flag_patterns(cfg))

        def _s1prov58(att58):
            return [a for a in att58
                    if a.detector == "provenance"
                    and "(fastpath skip)" in a.detail
                    and "#tok" not in a.detail and "#xn" not in a.detail
                    and "shift=" not in a.detail and "idna" not in a.detail]

        # (a) explosion face: 600 plaintext bait lines in ONE file -- every
        # text input is found at depth 0 and raw-echo retracted; the
        # incumbent writes an unbounded 601-row provenance flood, the cap
        # must stop the stage at exactly 500 charged rows + ONE budget
        # note, verdict honest NOT SOLVED.
        N58 = 600
        d58 = os.path.join(scratch, "evolve58-chain1cap")
        os.makedirs(d58, exist_ok=True)
        _content58 = "\n".join("flag{noise_%d}" % _i58
                               for _i58 in range(N58)) + "\n"
        _p58 = os.path.join(d58, "big.txt")
        with open(_p58, "w", encoding="utf-8") as _f58:
            _f58.write(_content58)
        _files58 = [{"path": "big.txt", "abs": _p58,
                     "size": len(_content58), "magic": None}]
        _att58 = []
        _got58 = run_fast_path(d58, _files58, bb58, _att58)
        assert _got58 is None, \
            "raw-echo bait must not credit, got %r" % _got58
        _prov58 = _s1prov58(_att58)
        _bud58 = [a for a in _att58
                  if a.detector == "budget" and "chain" in a.detail]
        assert len(_prov58) == 500, \
            "cap must leave exactly 500 charged stage-1 rows, got %d" % len(_prov58)
        assert len(_bud58) == 1, \
            "exhausted cap must leave exactly one chain budget attempt"
        assert "500 row(s) charged" in _bud58[0].detail, \
            "budget note must carry the charged-row count"
        assert not [a for a in _att58
                    if a.detector == "chain-decode" and not a.success], \
            "bait face must produce provenance rows, not miss notes"

        # (b) credit path: a base64 real flag mid-file must still credit
        # through the capped stage (a real find returns before any charge)
        # with zero budget attempts.
        d58b = os.path.join(scratch, "evolve58-chain1cap-credit")
        os.makedirs(d58b, exist_ok=True)
        _lines58b = [
            "ZmlsbGVyIGxpbmUgMCBhYWJiY2M=",
            "ZmlsbGVyIGxpbmUgMSBhYWJiY2M=",
            "ZmlsbGVyIGxpbmUgMiBhYWJiY2M=",
            "ZmxhZ3tjaGFpbjFjYXBfY3JlZGl0X29rfQ==",
            "ZmlsbGVyIGxpbmUgMyBkZGRlZWU=",
            "ZmlsbGVyIGxpbmUgNCBkZGRlZWU=",
            "ZmlsbGVyIGxpbmUgNSBkZGRlZWU=",
        ]
        _content58b = "\n".join(_lines58b) + "\n"
        _p58b = os.path.join(d58b, "cipher.txt")
        with open(_p58b, "w", encoding="utf-8") as _f58:
            _f58.write(_content58b)
        _files58b = [{"path": "cipher.txt", "abs": _p58b,
                      "size": len(_content58b), "magic": None}]
        _att58b = []
        _got58b = run_fast_path(d58b, _files58b, bb58, _att58b)
        assert _got58b == "flag{chain1cap_credit_ok}", \
            "credit path must survive the cap, got %r" % _got58b
        assert not any(a.detector == "budget" for a in _att58b), \
            "well-under-cap input must not fire the budget note"
        assert any(a.detector == "chain-decode" and a.success
                   for a in _att58b), \
            "the credited find must be a chain-decode attempt"

        # (c) normal-size input: the #L1/whole-file miss-note shape must be
        # identical to the incumbent (the counter only bookkeeps, it never
        # rewrites the note conditions).
        d58c = os.path.join(scratch, "evolve58-chain1cap-normal")
        os.makedirs(d58c, exist_ok=True)
        _content58c = "just a few plain lines\nnothing to decode here\n"
        _p58c = os.path.join(d58c, "small.txt")
        with open(_p58c, "w", encoding="utf-8") as _f58:
            _f58.write(_content58c)
        _files58c = [{"path": "small.txt", "abs": _p58c,
                      "size": len(_content58c), "magic": None}]
        _att58c = []
        run_fast_path(d58c, _files58c, bb58, _att58c)
        assert not any(a.detector == "budget" for a in _att58c), \
            "normal input must not fire the budget note"
        _miss58c = [a for a in _att58c
                    if a.detector == "chain-decode" and not a.success]
        assert len(_miss58c) == 3, \
            "clean 2-line file keeps whole + #L1 + #L2 miss notes, got %d" \
            % len(_miss58c)
        mod_results.append(True)
    except Exception:
        log("selftest evolve58-chain1cap failed:"); traceback.print_exc()
        mod_results.append(False)

    # [evolve-20261118-12 / rsi-132] wrap-round extraction idempotence: after a
    # SUCCESSFUL extractall the unpack step writes a per-carrier marker
    # <out>/.__x_done__<md5(abs path)[:12]> holding "size,mtime_ns"; wrap
    # rounds SKIP re-extracting unchanged carriers with an honest
    # 'ZIP/TAR/RAR SKIP (extracted before, unchanged)' line and still
    # register the out-dir, so the deferred _peek census stays byte-identical
    # (dotfile markers are invisible to every glob face); a mutated carrier
    # fails the fingerprint and re-extracts. RED (measured on the isolated
    # identity pair this round): wrap rounds re-pay extractall + Windows
    # Defender first-open on every member every round (flip fixtures
    # outer-skip-nested / tar-skip-nested: inc 29.35s/30.44s vs cand
    # 17.68s/18.11s, attempts 11=11, flags identical), and the mtime churn
    # defeats the wrap guard's no-new-files halt (live n1cat honest 38
    # attempts of re-extract cycling). Faces: (A) nested zip solves, markers
    # land, and a re-solve of the SAME dir still solves with the same flag
    # (markers never block coverage); (B) flagless carrier v1 stays honest
    # rc=1 with its marker, then a mutated v2 carrying a flag member must
    # defeat the fingerprint, re-extract and solve (a stale skip would stay
    # rc=1); (C) the tar arm gets the same skip treatment -- nested tar
    # round-2 emits TAR SKIP for the outer carrier while the new inner
    # carrier still extracts, and the re-solve keeps solving.
    try:
        import zipfile as _zf57
        import io as _io57
        import glob as _glob57
        _F57A = "flag{idem_wrap_keep}"
        _F57B = "flag{idem_mutate_flip}"
        _F57C = "flag{idem_tar_keep}"
        d57 = os.path.join(scratch, "evolve57-idem")
        # (A) nested zip: solve-1 extracts outer (r1) + inner (r2) -> solved;
        # markers prove _mark ran; solve-2 on the same dir still solves.
        dA57 = os.path.join(d57, "nested")
        os.makedirs(dA57, exist_ok=True)
        _in57 = _io57.BytesIO()
        with _zf57.ZipFile(_in57, "w", _zf57.ZIP_DEFLATED) as z57:
            z57.writestr("zflaghost.txt", (_F57A + "\n").encode() + b"A" * 90)
            for _i57 in range(12):
                z57.writestr("n%02d.txt" % _i57, b"inner filler %02d\n" % _i57)
        with _zf57.ZipFile(os.path.join(dA57, "outer.zip"), "w",
                          _zf57.ZIP_DEFLATED) as z57:
            for _i57 in range(20):
                z57.writestr("m%02d.txt" % _i57, b"outer filler %02d\n" % _i57)
            z57.writestr("inner.zip", _in57.getvalue())
        rc57a1 = do_solve(_NS(dir=dA57, mode="auto", brain="mock",
                              name="evolve57-idem-nested-1", flag_regex=None,
                              write_trace=False, allow_replay=True), cfg)
        assert rc57a1 == 0, "nested solve-1 must solve"
        row57a1 = get_stores(cfg).prior_attempt("evolve57-idem-nested-1")
        assert row57a1 and row57a1["solved"] == 1 \
            and row57a1["flag"] == _F57A, "solve-1 flag"
        _mk57 = _glob57.glob(os.path.join(dA57, "_extracted", ".__x_done__*"))
        assert _mk57, "successful extractall must leave per-carrier markers"
        rc57a2 = do_solve(_NS(dir=dA57, mode="auto", brain="mock",
                              name="evolve57-idem-nested-2", flag_regex=None,
                              write_trace=False, allow_replay=True), cfg)
        assert rc57a2 == 0, "marker-skip re-solve must still solve"
        row57a2 = get_stores(cfg).prior_attempt("evolve57-idem-nested-2")
        assert row57a2 and row57a2["flag"] == _F57A, "re-solve flag kept"
        # (B) mutation honesty: v1 carries no flag (honest rc=1 + marker);
        # v2 adds a flag member -- the size/mtime_ns change must defeat the
        # fingerprint, re-extract and solve (stale-skip stays rc=1 forever).
        dB57 = os.path.join(d57, "mutated")
        os.makedirs(dB57, exist_ok=True)
        _c57 = os.path.join(dB57, "carrier.zip")
        with _zf57.ZipFile(_c57, "w", _zf57.ZIP_DEFLATED) as z57:
            for _i57 in range(10):
                z57.writestr("f%02d.txt" % _i57, b"benign %02d\n" % _i57)
        rc57b1 = do_solve(_NS(dir=dB57, mode="auto", brain="mock",
                              name="evolve57-idem-mutated-1", flag_regex=None,
                              write_trace=False, allow_replay=True), cfg)
        assert rc57b1 == 1, "flagless v1 must stay honestly unsolved"
        _mk57b = _glob57.glob(os.path.join(dB57, "_extracted", ".__x_done__*"))
        assert _mk57b, "v1 extraction must leave its marker"
        with _zf57.ZipFile(_c57, "w", _zf57.ZIP_DEFLATED) as z57:
            for _i57 in range(10):
                z57.writestr("f%02d.txt" % _i57, b"benign %02d\n" % _i57)
            z57.writestr("flag_z.txt", (_F57B + "\n").encode() + b"B" * 80)
        rc57b2 = do_solve(_NS(dir=dB57, mode="auto", brain="mock",
                              name="evolve57-idem-mutated-2", flag_regex=None,
                              write_trace=False, allow_replay=True), cfg)
        assert rc57b2 == 0, "mutated carrier must re-extract and solve"
        row57b2 = get_stores(cfg).prior_attempt("evolve57-idem-mutated-2")
        assert row57b2 and row57b2["flag"] == _F57B, "mutation flag"
        # (C) tar arm: nested tar.gz mirrors the zip shape -- round-2 TAR
        # SKIPs the unchanged outer while the new inner carrier extracts
        # and surfaces the flag; the re-solve keeps solving.
        dC57 = os.path.join(d57, "tarred")
        os.makedirs(dC57, exist_ok=True)
        import tarfile as _tf57
        _inn57 = os.path.join(dC57, "_staging-inner.tar.gz")
        with _tf57.open(_inn57, "w:gz") as t57:
            _fb57 = (_F57C + "\n").encode() + b"C" * 80
            _ti57 = _tf57.TarInfo("zflaghost.txt")
            _ti57.size = len(_fb57)
            t57.addfile(_ti57, _io57.BytesIO(_fb57))
            for _i57 in range(10):
                _pl57 = b"inner tar filler %02d\n" % _i57
                _ti57 = _tf57.TarInfo("n%02d.txt" % _i57)
                _ti57.size = len(_pl57)
                t57.addfile(_ti57, _io57.BytesIO(_pl57))
        with _tf57.open(os.path.join(dC57, "outer.tar.gz"), "w:gz") as t57:
            t57.add(_inn57, arcname="inner.tar.gz")
            for _i57 in range(12):
                _pl57 = b"outer tar filler %02d\n" % _i57
                _ti57 = _tf57.TarInfo("m%02d.txt" % _i57)
                _ti57.size = len(_pl57)
                t57.addfile(_ti57, _io57.BytesIO(_pl57))
        os.remove(_inn57)
        rc57c1 = do_solve(_NS(dir=dC57, mode="auto", brain="mock",
                              name="evolve57-idem-tar-1", flag_regex=None,
                              write_trace=False, allow_replay=True), cfg)
        assert rc57c1 == 0, "nested tar solve-1 must solve"
        row57c1 = get_stores(cfg).prior_attempt("evolve57-idem-tar-1")
        assert row57c1 and row57c1["flag"] == _F57C, "tar flag"
        rc57c2 = do_solve(_NS(dir=dC57, mode="auto", brain="mock",
                              name="evolve57-idem-tar-2", flag_regex=None,
                              write_trace=False, allow_replay=True), cfg)
        assert rc57c2 == 0, "tar marker-skip re-solve must still solve"
        row57c2 = get_stores(cfg).prior_attempt("evolve57-idem-tar-2")
        assert row57c2 and row57c2["flag"] == _F57C, "tar re-solve flag"
        mod_results.append(True)
    except Exception:
        log("selftest evolve57-idem failed:"); traceback.print_exc()
        mod_results.append(False)

    # [evolve-20261118-14 / rsi-134] stage-1.2 wrapped-token consumption
    # ORDER bias: asset-shaped files (.min.js/.min.css/.map/.css/node_modules)
    # consume their wrapped tokens LAST, non-asset source/prose groups first
    # (stable first-come inside each tier, collection/dedup/caps/credits
    # byte-identical to the incumbent). Faces: (a) asset-flood flip -- 6
    # asset files x 48 tokens = 288 > TOK_MAX_TOTAL=256 burn the budget in
    # first-come order and the trailing prose carrier never gets scanned by
    # the incumbent; the ordered consumer visits z_note.md first and solves;
    # (b) no-drop -- a real payload token living INSIDE an asset file must
    # still solve when the tree total stays under budget (asset tokens are
    # never discarded, only reordered); (c) order parity -- a flagless
    # small tree keeps every per-file miss note (full consumption) with
    # prose notes first and asset notes after, zero budget noise.
    try:
        import base64 as _b64_59
        _F59A = "flag{wtp_pr10_rsi134}"
        _chain59 = _b64_59.b64encode(
            _b64_59.b64encode(_F59A.encode())).decode().encode()
        _tok59a = _chain59.hex().upper()

        def _asset_js59(seed, spans):
            lines = []
            for li in range(3):
                parts = []
                for si in range(spans):
                    n = 16 + (seed * 7 + li * 3 + si) % 25
                    if n % 2:
                        parts.append("q%d=\"%s\"" % (si, "f" + ("%x" % (seed * 1000 + li * 40 + si))[:1] + ("%032x" % (seed * 104729 + li * 4099 + si))[: n - 2]))
                    else:
                        parts.append("q%d=\"%s\"" % (si, ("A" + ("%x" % (seed * 15485863 + li * 7919 + si)).upper() + "Zz9+/") * 2)[: n])
                lines.append("var " + ",".join(parts) + ";")
            return "\n".join(lines) + "\n"

        # (a) flip face: 6 asset files x >=48 token spans, prose carrier last
        d59 = os.path.join(scratch, "evolve59-tokorder")
        os.makedirs(d59, exist_ok=True)
        _files59 = []
        for _i59 in range(6):
            _n59 = "a%d.min.js" % (_i59 + 1)
            _p59 = os.path.join(d59, _n59)
            with open(_p59, "w", encoding="utf-8") as _f59:
                _f59.write(_asset_js59(_i59 + 1, 17))
            _files59.append({"path": _n59, "abs": _p59,
                            "size": os.path.getsize(_p59), "magic": None})
        _note59 = (
            "# brief\n\nThe handler receives one packed string per session; "
            "unpack it with the standard chain to recover the token.\n\n"
            "`" + _tok59a + "`\n\n"
            "See the server rotation notes for details.\n")
        _p59n = os.path.join(d59, "z_note.md")
        with open(_p59n, "w", encoding="utf-8") as _f59:
            _f59.write(_note59)
        _files59.append({"path": "z_note.md", "abs": _p59n,
                         "size": len(_note59), "magic": None})
        _att59 = []
        _got59 = run_fast_path(d59, _files59, bb58, _att59)
        assert _got59 == _F59A, \
            "ordered consumer must reach the trailing prose token, got %r" % _got59
        _hit59 = [a for a in _att59 if a.detector == "chain-decode" and a.success]
        assert _hit59 and _hit59[0].detail.startswith("z_note.md #tok"), \
            "credited find must name the prose carrier: %r" % (_hit59[:1],)

        # (b) no-drop face: real payload inside an ASSET file, tree under budget
        d59b = os.path.join(scratch, "evolve59-tokorder-nodrop")
        os.makedirs(d59b, exist_ok=True)
        _F59B = "flag{nodrop_ass5t_1n_123}"
        _files59b = []
        _p59b1 = os.path.join(d59b, "a1.min.js")
        with open(_p59b1, "w", encoding="utf-8") as _f59:
            _f59.write('var x="00112233445566",y="8899aabbccddeeff";\n')
        _files59b.append({"path": "a1.min.js", "abs": _p59b1,
                          "size": os.path.getsize(_p59b1), "magic": None})
        _p59bz = os.path.join(d59b, "z_note.md")
        with open(_p59bz, "w", encoding="utf-8") as _f59:
            _f59.write("plain prose only\nnothing packed here\n")
        _files59b.append({"path": "z_note.md", "abs": _p59bz,
                          "size": os.path.getsize(_p59bz), "magic": None})
        _p59b2 = os.path.join(d59b, "a2.min.js")
        with open(_p59b2, "w", encoding="utf-8") as _f59:
            _f59.write('var p="deadbeef0011223344";'
                       'real="' + _b64_59.b64encode(_F59B.encode()).decode() + '";\n')
        _files59b.append({"path": "a2.min.js", "abs": _p59b2,
                          "size": os.path.getsize(_p59b2), "magic": None})
        _att59b = []
        _got59b = run_fast_path(d59b, _files59b, bb58, _att59b)
        assert _got59b == _F59B, \
            "asset-tier tokens are reordered, never dropped, got %r" % _got59b

        # (c) order-parity face: flagless tree keeps every note, prose first
        d59c = os.path.join(scratch, "evolve59-tokorder-parity")
        os.makedirs(d59c, exist_ok=True)
        _files59c = []
        _p59c1 = os.path.join(d59c, "a1.min.js")
        with open(_p59c1, "w", encoding="utf-8") as _f59:
            _f59.write('var x="0011223344556677",y="8899aabbccddeeff";\n')
        _files59c.append({"path": "a1.min.js", "abs": _p59c1,
                          "size": os.path.getsize(_p59c1), "magic": None})
        _p59cz = os.path.join(d59c, "z_note.md")
        with open(_p59cz, "w", encoding="utf-8") as _f59:
            _f59.write('see `aabbccddeeff0011223` and rotate\n')
        _files59c.append({"path": "z_note.md", "abs": _p59cz,
                          "size": os.path.getsize(_p59cz), "magic": None})
        _p59cb = os.path.join(d59c, "b_note.md")
        with open(_p59cb, "w", encoding="utf-8") as _f59:
            _f59.write('token `0011aabbccddeeff22` logged\n')
        _files59c.append({"path": "b_note.md", "abs": _p59cb,
                          "size": os.path.getsize(_p59cb), "magic": None})
        _att59c = []
        _got59c = run_fast_path(d59c, _files59c, bb58, _att59c)
        assert _got59c is None, "flagless face must stay unsolved"
        _toknotes59c = [a for a in _att59c
                        if a.detector == "chain-decode" and "#tok" in a.detail]
        _srcs59c = [a.detail.split(" #tok")[0] for a in _toknotes59c]
        assert _srcs59c == ["z_note.md", "b_note.md", "a1.min.js"], \
            "prose groups must consume before asset groups, got %r" % _srcs59c
        assert len(_toknotes59c) == 3, \
            "every token-carrying file keeps exactly one note, got %d" % len(_toknotes59c)
        mod_results.append(True)
    except Exception:
        log("selftest evolve59-tokorder failed:"); traceback.print_exc()
        mod_results.append(False)

    # [evolve-20261118-16 / rsi-136] stage-4 xor-token POOL tier bias: when
    # the token pool exceeds the 64-key cap, letter/underscore-initial
    # tokens (the "hint words" this bridge strategy exists for) consume the
    # slots first, punctuation/digit-initial noise (ASCII sorts before
    # letters) fills the remainder; pools of <= 64 tokens stay
    # byte-identical to the incumbent (full lexicographic list). Faces:
    # (a) letter-key flip -- 70 noise tokens flood the incumbent
    # lexicographic cut, the hint key enters via the letter tier and the
    # xor-token stage solves with the credited carrier named; (b)
    # small-pool parity -- a <=64-token mixed tree returns the exact full
    # lexicographic list (no reordering for small pools); (c) no-drop --
    # a punctuation-initial true key with a starved letter tier still
    # enters through the noise-tier spill and solves (a reorder, never a
    # filter).
    try:
        from fastpath.detectors import extract_tokens as _et60
        _K60 = "s3cret_passw0rd"
        _F60 = "flag{x0r_l3tt3r_k3y_p00l}"
        _noise60 = []
        for _p60 in ("!", "#", "%", "&", "*", "+", "=", "0", "1", "3", "5",
                     "7", "9"):
            for _j60 in range(6):
                _t60 = "%s%d_junk%d" % (_p60, _j60, len(_noise60))
                _noise60.append(_t60)
                if len(_noise60) >= 70:
                    break
            if len(_noise60) >= 70:
                break
        assert len(_noise60) == 70

        # (a) letter-key flip face
        d60 = os.path.join(scratch, "evolve60-xortoktier")
        os.makedirs(d60, exist_ok=True)
        _p60n = os.path.join(d60, "noise.txt")
        with open(_p60n, "w", encoding="utf-8") as _f60:
            _f60.write("environment dump:\n" + " ".join(_noise60) + "\n")
        _p60h = os.path.join(d60, "hint.md")
        with open(_p60h, "w", encoding="utf-8") as _f60:
            _f60.write("# session note\n\nthe operator keeps the channel key "
                       "`" + _K60 + "` in the drawer.\nrotate weekly.\n")
        _kb60 = _K60.encode("utf-8")
        _ct60 = bytes(b ^ _kb60[i % len(_kb60)]
                      for i, b in enumerate(_F60.encode("utf-8")))
        _p60s = os.path.join(d60, "secret.bin")
        with open(_p60s, "wb") as _f60:
            _f60.write(_ct60)
        _files60 = []
        for _fn60 in ("noise.txt", "hint.md", "secret.bin"):
            _pa60 = os.path.join(d60, _fn60)
            _files60.append({"path": _fn60, "abs": _pa60,
                             "size": os.path.getsize(_pa60), "magic": None})
        _pool60 = _et60(_files60)
        assert len(_pool60) == 64, "cap pool expected, got %d" % len(_pool60)
        assert _K60 in _pool60, \
            "letter-initial hint key must enter the tiered pool, got %r" % (_pool60[:8],)
        _att60 = []
        _got60 = run_fast_path(d60, _files60, bb58, _att60)
        assert _got60 == _F60, "tiered pool must solve the flip face, got %r" % _got60
        _hit60 = [a for a in _att60
                  if a.detector == "xor-token" and a.success]
        assert _hit60 and _hit60[0].detail.startswith("secret.bin"), \
            "credited xor-token find must name the ciphertext carrier: %r" % (_hit60[:1],)

        # (b) small-pool parity face: <= 64 tokens -> full lexicographic list
        d60b = os.path.join(scratch, "evolve60-xortoktier-parity")
        os.makedirs(d60b, exist_ok=True)
        _p60b = os.path.join(d60b, "words.txt")
        with open(_p60b, "w", encoding="utf-8") as _f60:
            _f60.write("alpha zeta 0x11 !odd 9nine _under Beta\n")
        _files60b = [{"path": "words.txt", "abs": _p60b,
                      "size": os.path.getsize(_p60b), "magic": None}]
        _pool60b = _et60(_files60b)
        assert _pool60b == sorted({"alpha", "zeta", "0x11", "!odd", "9nine",
                                   "_under", "Beta"}), \
            "small pool must stay the full lexicographic list, got %r" % (_pool60b,)

        # (c) no-drop face: punctuation-initial true key + starved letter
        # tier -> key still enters through the noise-tier spill and solves
        d60c = os.path.join(scratch, "evolve60-xortoktier-nodrop")
        os.makedirs(d60c, exist_ok=True)
        _K60C = "!sh4dow_9key"
        _F60C = "flag{n0dr0p_t13r_sp11}"
        _p60cn = os.path.join(d60c, "noise.txt")
        with open(_p60cn, "w", encoding="utf-8") as _f60:
            _f60.write("environment dump:\n" + " ".join(_noise60) + "\n")
        _p60ch = os.path.join(d60c, "hint.md")
        with open(_p60ch, "w", encoding="utf-8") as _f60:
            _f60.write("the drawer key `" + _K60C + "` opens the vault\n")
        _kb60c = _K60C.encode("utf-8")
        _ct60c = bytes(b ^ _kb60c[i % len(_kb60c)]
                       for i, b in enumerate(_F60C.encode("utf-8")))
        _p60cs = os.path.join(d60c, "secret.bin")
        with open(_p60cs, "wb") as _f60:
            _f60.write(_ct60c)
        _files60c = []
        for _fn60 in ("noise.txt", "hint.md", "secret.bin"):
            _pa60 = os.path.join(d60c, _fn60)
            _files60c.append({"path": _fn60, "abs": _pa60,
                              "size": os.path.getsize(_pa60), "magic": None})
        _pool60c = _et60(_files60c)
        assert _K60C in _pool60c, \
            "noise-tier spill must admit the punctuation-initial key, got %r" % (_pool60c[:8],)
        _att60c = []
        _got60c = run_fast_path(d60c, _files60c, bb58, _att60c)
        assert _got60c == _F60C, \
            "noise-tier key must still solve (reorder, not filter), got %r" % _got60c
        _hit60c = [a for a in _att60c
                   if a.detector == "xor-token" and a.success]
        assert _hit60c and _hit60c[0].detail.endswith("key=" + _K60C), \
            "credited find must name the noise-tier key: %r" % (_hit60c[:1],)
        mod_results.append(True)
    except Exception:
        log("selftest evolve60-xortoktier failed:"); traceback.print_exc()
        mod_results.append(False)

    # evolve61-aeskey (rsi-139): known-key AES decrypt stage. Faces:
    # (a) FIPS-197 KATs for the stdlib AES core (128/192/256 ECB + CBC);
    # (b) flip: labeled hex key in config-style text + 3-block binary
    #     CBC-IV-prefix ciphertext -> run_fast_path solves and credits
    #     the aes-known-key detector;
    # (c) quiet: no labeled key -> ZERO stage attempts (attempt-stream
    #     parity for every non-AES challenge);
    # (d) miss: labeled key + undecryptable 48B blob -> exactly ONE
    #     honest miss row, verdict NOT SOLVED.
    try:
        from fastpath.detectors import (_aes_ecb_decrypt, _aes_cbc_decrypt,
                                        aes_known_key_scan, AES_MAX_KEYS)
        assert AES_MAX_KEYS == 8, "aes key cap constant drifted"
        # (a) FIPS-197 appendix C known answers (re-verified against
        # pycryptodome this round, not transcribed from memory).
        _pt61 = bytes.fromhex("00112233445566778899aabbccddeeff")
        assert _aes_ecb_decrypt(bytes(range(16)), bytes.fromhex(
            "69c4e0d86a7b0430d8cdb78070b4c55a")) == _pt61, "AES-128 KAT"
        assert _aes_ecb_decrypt(bytes(range(24)), bytes.fromhex(
            "dda97ca4864cdfe06eaf70a0ec0d7191")) == _pt61, "AES-192 KAT"
        assert _aes_ecb_decrypt(bytes(range(32)), bytes.fromhex(
            "8ea2b7ca516745bfeafc49904b496089")) == _pt61, "AES-256 KAT"
        _iv61 = bytes.fromhex("deadbeefcafebabe0123456789abcdef")
        assert _aes_cbc_decrypt(bytes.fromhex(
            "0f1e2d3c4b5a69788796a5b4c3d2e1f0"), _iv61, bytes.fromhex(
            "5b058f9adf10ce2bcebdded79c8f9260"
            "47f2e46f1134977e52c1c2b6d8a7d9ee"
        )) == b"flag{aes_known_key_flip61}\x06\x06\x06\x06\x06\x06", \
            "AES-128-CBC KAT"
        # (b) flip face: config-style labeled hex key + binary carrier
        d61 = os.path.join(scratch, "evolve61-aeskey")
        os.makedirs(d61, exist_ok=True)
        _p61c = os.path.join(d61, "config.json")
        with open(_p61c, "w", encoding="utf-8") as _f61:
            _f61.write('{\n  "crypto": {\n    "aes_key_hex": '
                       '"0f1e2d3c4b5a69788796a5b4c3d2e1f0"\n  }\n}\n')
        _p61s = os.path.join(d61, "flag.enc")
        with open(_p61s, "wb") as _f61:
            _f61.write(bytes.fromhex(
                "deadbeefcafebabe0123456789abcdef"
                "5b058f9adf10ce2bcebdded79c8f9260"
                "47f2e46f1134977e52c1c2b6d8a7d9ee"))
        _files61 = []
        for _fn61 in ("config.json", "flag.enc"):
            _pa61 = os.path.join(d61, _fn61)
            _files61.append({"path": _fn61, "abs": _pa61,
                             "size": os.path.getsize(_pa61), "magic": None})
        _att61 = []
        _got61 = run_fast_path(d61, _files61, bb58, _att61)
        assert _got61 == "flag{aes_known_key_flip61}", \
            "known-key AES flip face must solve, got %r" % _got61
        _hit61 = [a for a in _att61
                  if a.detector == "aes-known-key" and a.success]
        assert _hit61, "credited row must exist: %r" % (_att61[-3:],)
        assert "CBC" in _hit61[0].detail and "flag.enc" in _hit61[0].detail, \
            "credited row must name mode and carrier: %r" % (_hit61[:1],)
        # (c) quiet face: no labeled key -> zero stage rows
        d61q = os.path.join(scratch, "evolve61-aeskey-quiet")
        os.makedirs(d61q, exist_ok=True)
        _p61q = os.path.join(d61q, "note.txt")
        with open(_p61q, "w", encoding="utf-8") as _f61:
            _f61.write("travel log: nothing secret here, just sea shanties\n")
        _files61q = [{"path": "note.txt", "abs": _p61q,
                      "size": os.path.getsize(_p61q), "magic": None}]
        _att61q = []
        _got61q = run_fast_path(d61q, _files61q, bb58, _att61q)
        assert _got61q is None
        assert not [a for a in _att61q if a.detector == "aes-known-key"], \
            "quiet face must add zero aes rows: %r" % (_att61q,)
        # (d) miss face: labeled key + undecryptable blob -> one honest row
        d61m = os.path.join(scratch, "evolve61-aeskey-miss")
        os.makedirs(d61m, exist_ok=True)
        _p61mk = os.path.join(d61m, "hint.txt")
        with open(_p61mk, "w", encoding="utf-8") as _f61:
            _f61.write("device config: secret key value is "
                       "0f1e2d3c4b5a69788796a5b4c3d2e1f0 ok\n")
        _p61ms = os.path.join(d61m, "payload.bin")
        with open(_p61ms, "wb") as _f61:
            _f61.write(bytes.fromhex(
                "ffeeddccbbaa99887766554433221100"
                "00112233445566778899aabbccddeeff"
                "deadbeefdeadbeefdeadbeefdeadbeef"))
        _files61m = []
        for _fn61 in ("hint.txt", "payload.bin"):
            _pa61 = os.path.join(d61m, _fn61)
            _files61m.append({"path": _fn61, "abs": _pa61,
                              "size": os.path.getsize(_pa61), "magic": None})
        _att61m = []
        _got61m = run_fast_path(d61m, _files61m, bb58, _att61m)
        assert _got61m is None, "miss face must stay NOT SOLVED"
        _miss61 = [a for a in _att61m
                   if a.detector == "aes-known-key" and not a.success]
        assert len(_miss61) == 1, \
            "exactly one honest miss row expected: %r" % (_miss61,)
        mod_results.append(True)
    except Exception:
        log("selftest evolve61-aeskey failed:"); traceback.print_exc()
        mod_results.append(False)
    # [evolve-20261118-17 / rsi-140] decode-family flag plausibility gate
    try:
        from fastpath.detectors import _implausible_flag as _if61
        # (a) census no-drop: every REAL flag from the 2026-11-18 store
        # census (46 solved rows, distinct values) must pass the gate
        for _real61 in (
            "ctf{W~am}",
            "NSSCTF{133716e2-c985-46f7-81d6-dae5b7c460ab}",
            "flag{MTczMDc4MzQ2Ng==}",
            "flag{test_flag}",
            "CTF{6859ccfb6ccc528520463fae732743c0}",
            "flag{WelcOmeTO}",
            "flag{b57758d5acc923137eef453239ba685b}",
            "flag{B@sE_0f_CrYpt0_N0W}",
            "flag4{f4096bc5-880e-61a6-3b74-c2f8aa14990c}",
            "DASCTF{welcome_to_zjctf_2024}",
            "ctf{5xxf}",
            "flag{part1_part2_part3}",
            "flag{d39691fd3467e11c5c4443e65a93ab37}",
            "flag{fc3f4ce8dc3eaca8807812b8c0435cd4}",
            "flag:{ErLaiSiWanBaQianSui}",
            "FLAG{Android_NoSugar_Challenge_2025}",
            "NCO26{xxxxxxxxx}",
            "NCO26{Gh0st_P4rt1t10n_D1sk_F0r3ns1cs}",
            "flag{flag_not_here}",
            "flag{F1l3_Upl04d_&_1nclud3_Ch41n_Vuln}",
            "flag{F1l3_0p3r4t10ns_C4n_B3_D4ng3r0us}",
            "flag{pump_zeroed}",
            "n1ctf{4lgEbra_he1p_m3_sOber_up~}",
            "flag{B@C#}",
        ):
            assert not _if61(_real61), \
                "census real flag must pass the plausibility gate: %r" % _real61
        # Unicode / non-ASCII real flags pass the mask rule via the
        # Unicode-aware word-char class (evolve36 selftest regression)
        for _uni61 in ("flag{进化三十五}", "ctf{日本語テスト}",
                       "flag{emoji_🎉_ok}"):
            assert not _if61(_uni61),                 "unicode real flag must pass the word-char rule: %r" % _uni61
        for _bare61 in ("C", "AB", "BCD",
                        "flag:LSB_is_easy_2026:", "flag:local_test_flag:"):
            assert not _if61(_bare61), \
                "non-brace find must be untouched: %r" % _bare61
        # (b) garbage specimens from the live store must all reject
        for _garb61, _why61 in (
            ("ctf{p|>bp|}", "xor-single key=0x11 over go.mod 'crewjam/saml' (n1saml live RED)"),
            ("ctf{dcemitqwpf-eqnqt:jun(xct(--ectf))}",
             "caesar shift+2 over .card{background-color:hsl(var(--carp))}"),
            ('flag{"<<s<<number<<"}', "C++ stream template fragment"),
            ("CTF{******}", "asterisk mask"),
            ("NCO26{.*}", "regex fragment mask"),
            ("flag{????????}", "question-mark mask"),
        ):
            assert _if61(_garb61), \
                "garbage must be rejected: %r (%s)" % (_garb61, _why61)

        # (c) e2e xor-single flip: the go.mod dependency garbage that the
        # incumbent credits as a solve must yield NO flag and NO success
        # attempt under the candidate bytes
        _d61 = os.path.join(scratch, "evolve62-flagplaus-xorgo")
        os.makedirs(_d61, exist_ok=True)
        _p61 = os.path.join(_d61, "go.mod")
        with open(_p61, "w", encoding="utf-8") as _f61:
            _f61.write("module sp\n\ngo 1.25.3\n\nrequire (\n"
                       "\tgithub.com/crewjam/saml v0.5.1\n"
                       "\tgithub.com/gin-gonic/gin v1.11.0\n)\n")
        _files61 = [{"path": "go.mod", "abs": _p61,
                     "size": os.path.getsize(_p61), "magic": None}]
        _att61 = []
        _got61 = run_fast_path(_d61, _files61, bb58, _att61)
        assert _got61 is None, \
            "go.mod dependency garbage must not solve, got %r" % _got61
        assert not [a for a in _att61 if a.success], \
            "no success attempt may credit the go.mod garbage: %r" % _att61[:3]

        # (d) e2e caesar flip: '.card{...}' CSS shifted +2 produces the
        # store's exact caesar garbage -> candidate must not credit it
        _d61b = os.path.join(scratch, "evolve62-flagplaus-css")
        os.makedirs(_d61b, exist_ok=True)
        _p61b = os.path.join(_d61b, "style.css")
        with open(_p61b, "w", encoding="utf-8") as _f61:
            _f61.write(".card{background-color:hsl(var(--carp))}\n")
        _files61b = [{"path": "style.css", "abs": _p61b,
                      "size": os.path.getsize(_p61b), "magic": None}]
        _att61b = []
        _got61b = run_fast_path(_d61b, _files61b, bb58, _att61b)
        assert _got61b is None, \
            "CSS caesar garbage must not solve, got %r" % _got61b
        assert not [a for a in _att61b if a.success], \
            "no success attempt may credit the CSS garbage: %r" % _att61b[:3]

        # (e) e2e no-drop: a REAL single-byte-XOR-encoded flag still
        # solves end-to-end under the candidate gate
        _d61c = os.path.join(scratch, "evolve62-flagplaus-real")
        os.makedirs(_d61c, exist_ok=True)
        _F61C = "flag{r34l_x0r_p4ss_th3_g4t3}"
        _p61c = os.path.join(_d61c, "secret.bin")
        with open(_p61c, "wb") as _f61:
            _f61.write(bytes(b ^ 0x5c for b in _F61C.encode("utf-8")))
        _files61c = [{"path": "secret.bin", "abs": _p61c,
                      "size": os.path.getsize(_p61c), "magic": None}]
        _att61c = []
        _got61c = run_fast_path(_d61c, _files61c, bb58, _att61c)
        assert _got61c == _F61C, \
            "real XOR-encoded flag must still solve, got %r" % _got61c
        _hit61c = [a for a in _att61c
                   if a.detector == "xor-single" and a.success]
        assert _hit61c and "key=0x5c" in _hit61c[0].detail, \
            "credited find must name the real key: %r" % (_hit61c[:1],)
        mod_results.append(True)
    except Exception:
        log("selftest evolve62-flagplaus failed:"); traceback.print_exc()
        mod_results.append(False)

    # [evolve-20261118-19 / rsi-142] hprof-literal-triage stage: four
    # faces mirroring the evolve61-aeskey block -- (a) BE-lane flip,
    # (b) hi-byte-lane flip (the live specimen's encoding trap), (c)
    # quiet face: a non-hprof challenge must add ZERO hprof rows, (d)
    # miss face: an hprof without flag material stays NOT SOLVED with
    # exactly ONE honest hprof row. Both flip fixtures are real record
    # streams (JAVA PROFILE 1.0.1 header + STRING records + HEAP_DUMP
    # primitive-array records); the flags never appear as verbatim
    # byte sequences inside the dumps (raw-echo gate stays green).
    try:
        from fastpath.detectors import (HPROF_MAGIC, hprof_literal_scan,
                                        run_fast_path)
        import struct as _st63

        def _h63_record(tag, body, ts=1):
            return _st63.pack(">BII", tag, ts, len(body)) + body

        def _h63_challenge(dirname, flag_text=None, mode=None):
            _d63 = os.path.join(scratch, dirname)
            os.makedirs(_d63, exist_ok=True)
            _recs = [b"JAVA PROFILE 1.0.1\x00"
                     + _st63.pack(">III", 8, 0x1234, 0x56789ABC)]
            for _i63, _s63 in enumerate(("java/lang/Object",
                                         "java/lang/String", "Test")):
                _recs.append(_h63_record(
                    0x01, _st63.pack(">Q", 0x1000 + _i63)
                    + _s63.encode("utf-8")))
            _entries = []
            if mode == "be" and flag_text:
                _raw63 = flag_text.encode("utf-16-be")
                _entries.append(_st63.pack(">BQIIB", 0x23, 0x2000, 1,
                                           len(_raw63) // 2, 5) + _raw63)
                _entries.append(_st63.pack(
                    ">BQIIB", 0x23, 0x2001, 2, 7, 5)
                    + "Stream closed".encode("utf-16-be"))
            elif mode == "hibyte" and flag_text:
                _jk63 = (0xCC, 0x00, 0x20, 0x1F)
                _raw63 = b"".join(bytes([_jk63[i % 4], ord(_c63)])
                                  for i, _c63 in enumerate(flag_text))
                _entries.append(_st63.pack(">BQIIB", 0x23, 0x2000, 1,
                                           len(_raw63) // 2, 5) + _raw63)
            else:
                _entries.append(_st63.pack(
                    ">BQIIB", 0x23, 0x2000, 1, 7, 5)
                    + "Stream closed".encode("utf-16-be"))
            _recs.append(_h63_record(0x0C, b"".join(_entries)))
            _blob63 = b"".join(_recs)
            _p63 = os.path.join(_d63, "heap.hprof")
            with open(_p63, "wb") as _f63:
                _f63.write(_blob63)
            if flag_text:
                assert flag_text.encode() not in _blob63, \
                    "fixture must not carry the flag verbatim"
            return ([{"path": "heap.hprof", "abs": _p63,
                      "size": os.path.getsize(_p63), "magic": None}], _d63)

        # (a) BE-lane flip: incumbent had zero hprof coverage; candidate
        # must credit the BE lane solve
        _files63a, _d63a = _h63_challenge("evolve63-hprof-be",
                                          "flag{hpr0f_b3_l1t3r4l_2024}", "be")
        _att63a = []
        _got63a = run_fast_path(_d63a, _files63a, bb58, _att63a)
        assert _got63a == "flag{hpr0f_b3_l1t3r4l_2024}", \
            "BE-lane flip must solve, got %r" % _got63a
        _hit63a = [a for a in _att63a
                   if a.detector == "hprof-literal" and a.success]
        assert _hit63a and "BE lane" in _hit63a[0].detail, \
            "credited row must name the BE lane: %r" % (_hit63a[:1],)
        # (b) hi-byte-lane flip: the live specimen's encoding trap
        _files63b, _d63b = _h63_challenge("evolve63-hprof-hibyte",
                                          "flag{h1_byt3_str34m_2024}",
                                          "hibyte")
        _att63b = []
        _got63b = run_fast_path(_d63b, _files63b, bb58, _att63b)
        assert _got63b == "flag{h1_byt3_str34m_2024}", \
            "hi-byte-lane flip must solve, got %r" % _got63b
        _hit63b = [a for a in _att63b
                   if a.detector == "hprof-literal" and a.success]
        assert _hit63b and "hi-byte lane" in _hit63b[0].detail, \
            "credited row must name the hi-byte lane: %r" % (_hit63b[:1],)
        # (c) quiet face: a plain-text challenge must add ZERO hprof rows
        _d63q = os.path.join(scratch, "evolve63-hprof-quiet")
        os.makedirs(_d63q, exist_ok=True)
        _p63q = os.path.join(_d63q, "note.txt")
        with open(_p63q, "w", encoding="utf-8") as _f63:
            _f63.write("plain text: nothing java here, sea shanties\n")
        _files63q = [{"path": "note.txt", "abs": _p63q,
                      "size": os.path.getsize(_p63q), "magic": None}]
        _att63q = []
        _got63q = run_fast_path(_d63q, _files63q, bb58, _att63q)
        assert _got63q is None
        assert not [a for a in _att63q
                    if a.detector == "hprof-literal"], \
            "quiet face must add zero hprof rows: %r" % (_att63q,)
        # (d) miss face: hprof without flag material -> exactly ONE row
        _files63m, _d63m = _h63_challenge("evolve63-hprof-miss")
        _att63m = []
        _got63m = run_fast_path(_d63m, _files63m, bb58, _att63m)
        assert _got63m is None, "miss face must stay NOT SOLVED"
        _miss63 = [a for a in _att63m
                   if a.detector == "hprof-literal" and not a.success]
        assert len(_miss63) == 1, \
            "exactly one honest hprof miss row expected: %r" % (_miss63,)
        assert "no flag" in _miss63[0].detail, \
            "miss row must be honest: %r" % (_miss63[0].detail,)
        mod_results.append(True)
    except Exception:
        log("selftest evolve63-hproftriage failed:"); traceback.print_exc()
        mod_results.append(False)

    # [evolve-20261118-20 / rsi-143] Baijiaxing 64-surname custom-base64
    # codec stage: six faces -- (a) synthetic-table flip, (b) the live
    # specimen's real 56-char surname ciphertext (ledger-114 payload),
    # (c) raw-echo provenance guard (flag verbatim in-tree must NOT be
    # credited), (d) garbage-reject: a qualifying all-surname run that
    # decodes to junk stays NOT SOLVED with exactly ONE honest miss
    # row, (e) CJK-quiet: Chinese prose without qualifying runs adds
    # ZERO baijiaxing rows (byte-identical attempt stream for the
    # common real-world Chinese challenge shape), (f) co-existence: a
    # CJK-prose tree with an XOR-encoded flag still solves through the
    # incumbent xor-single lane with no interference. The two flip
    # flags never appear verbatim inside the fixtures (raw-echo gate
    # stays green).
    try:
        from fastpath.detectors import (BAIJIAXING_TABLE,
                                        baijiaxing_codec_scan,
                                        run_fast_path)

        def _bjx64_encode(plain):
            _tbl = BAIJIAXING_TABLE
            _bits = 0
            _n = 0
            _out = []
            for _b in plain.encode("utf-8"):
                _bits = (_bits << 8) | _b
                _n += 8
                while _n >= 6:
                    _n -= 6
                    _out.append(_tbl[(_bits >> _n) & 0x3F])
            if _n:
                _out.append(_tbl[(_bits << (6 - _n)) & 0x3F])
            return "".join(_out)

        # (a) synthetic flip
        _F64A = "flag{b41j1a_c0d3c_dr1ll_2026}"
        _d64a = os.path.join(scratch, "evolve64-baijiaxing-synth")
        os.makedirs(_d64a, exist_ok=True)
        _p64a = os.path.join(_d64a, "secret.txt")
        with open(_p64a, "w", encoding="utf-8") as _f64:
            _f64.write("码表：\n" + BAIJIAXING_TABLE + "\n密文：\n"
                       + _bjx64_encode(_F64A) + "\n")
        assert _F64A.encode() not in open(_p64a, "rb").read(), \
            "fixture must not carry the flag verbatim"
        _files64a = [{"path": "secret.txt", "abs": _p64a,
                      "size": os.path.getsize(_p64a), "magic": None}]
        _att64a = []
        _got64a = run_fast_path(_d64a, _files64a, bb58, _att64a)
        assert _got64a == _F64A, \
            "synthetic flip must solve, got %r" % _got64a
        _hit64a = [a for a in _att64a
                   if a.detector == "baijiaxing-codec" and a.success]
        assert _hit64a and "baijiaxing lane" in _hit64a[0].detail, \
            "credited row must name the baijiaxing lane: %r" % (_hit64a[:1],)

        # (b) real payload flip (the ledger-114 specimen's literals)
        _F64B = "flag{aa534ac5-2f1a-466c-baf7-1ad7edb6adb2}"
        _C64B = ("曹窦韦谢曹方范谢孔许何马沈郑吴喻沈尤苗昌曹喻吴谢卫许朱"
                 "花沈窦蒋范孔窦吴窦沈昌苗韦孔施朱方曹施秦邹沈窦吴柏孔喻陈鲍")
        _d64b = os.path.join(scratch, "evolve64-baijiaxing-real")
        os.makedirs(_d64b, exist_ok=True)
        _p64b = os.path.join(_d64b, "secret.txt")
        with open(_p64b, "w", encoding="utf-8") as _f64:
            _f64.write("想跟你说一个小秘密：我把码表修改成了\n"
                       + BAIJIAXING_TABLE + "费\n"
                       "真的旗帜怎么会这么轻易就让你拿到,但你都找到我了"
                       "那我给你一个密文吧：\n" + _C64B + "\n")
        assert _F64B.encode() not in open(_p64b, "rb").read()
        _files64b = [{"path": "secret.txt", "abs": _p64b,
                      "size": os.path.getsize(_p64b), "magic": None}]
        _att64b = []
        _got64b = run_fast_path(_d64b, _files64b, bb58, _att64b)
        assert _got64b == _F64B, \
            "real ledger-114 payload must solve, got %r" % _got64b
        _hit64b = [a for a in _att64b
                   if a.detector == "baijiaxing-codec" and a.success]
        assert _hit64b and "baijiaxing lane" in _hit64b[0].detail, \
            "real-payload credit must name the baijiaxing lane: %r" % (_hit64b[:1],)

        # (c) raw-echo guard: flag verbatim in-tree -> provenance only
        _d64c = os.path.join(scratch, "evolve64-baijiaxing-rawecho")
        os.makedirs(_d64c, exist_ok=True)
        _p64c = os.path.join(_d64c, "secret.txt")
        with open(_p64c, "w", encoding="utf-8") as _f64:
            _f64.write("密文：\n" + _bjx64_encode(_F64A) + "\n")
        _p64c2 = os.path.join(_d64c, "leak.txt")
        with open(_p64c2, "w", encoding="utf-8") as _f64:
            _f64.write("oops the answer is " + _F64A + "\n")
        _files64c = [{"path": "secret.txt", "abs": _p64c,
                      "size": os.path.getsize(_p64c), "magic": None},
                     {"path": "leak.txt", "abs": _p64c2,
                      "size": os.path.getsize(_p64c2), "magic": None}]
        _att64c = []
        _got64c = run_fast_path(_d64c, _files64c, bb58, _att64c)
        assert _got64c is None, \
            "raw-echo flag must not be credited as a solve: %r" % _got64c
        assert not [a for a in _att64c if a.success], \
            "no success attempt may credit a verbatim flag: %r" % (_att64c,)

        # (d) garbage-reject: qualifying run decoding to junk -> ONE honest row
        _d64d = os.path.join(scratch, "evolve64-baijiaxing-garbage")
        os.makedirs(_d64d, exist_ok=True)
        _p64d = os.path.join(_d64d, "names.txt")
        with open(_p64d, "w", encoding="utf-8") as _f64:
            _f64.write("名册：\n" + BAIJIAXING_TABLE[:24] + "\n")
        _files64d = [{"path": "names.txt", "abs": _p64d,
                      "size": os.path.getsize(_p64d), "magic": None}]
        _att64d = []
        _got64d = run_fast_path(_d64d, _files64d, bb58, _att64d)
        assert _got64d is None, "garbage decode must stay NOT SOLVED"
        _miss64d = [a for a in _att64d
                    if a.detector == "baijiaxing-codec" and not a.success]
        assert len(_miss64d) == 1, \
            "exactly one honest baijiaxing miss row expected: %r" % (_miss64d,)
        assert "no flag" in _miss64d[0].detail, \
            "miss row must be honest: %r" % (_miss64d[0].detail,)

        # (e) CJK-quiet: Chinese prose, no qualifying run -> ZERO rows
        _d64e = os.path.join(scratch, "evolve64-baijiaxing-cjkquiet")
        os.makedirs(_d64e, exist_ok=True)
        _p64e = os.path.join(_d64e, "note.txt")
        with open(_p64e, "w", encoding="utf-8") as _f64:
            _f64.write("模型投毒是一种对抗性攻击方法，攻击者通过操纵训练数据"
                       "破坏模型性能。参赛者需要补全检测算法，在给定数据集上"
                       "识别全部被投毒图像，评分由赛场平台完成。\n")
        _files64e = [{"path": "note.txt", "abs": _p64e,
                      "size": os.path.getsize(_p64e), "magic": None}]
        _att64e = []
        _got64e = run_fast_path(_d64e, _files64e, bb58, _att64e)
        assert _got64e is None
        assert not [a for a in _att64e
                     if a.detector == "baijiaxing-codec"], \
            "CJK-quiet face must add zero baijiaxing rows: %r" % (_att64e,)

        # (f) co-existence: XOR-encoded flag + CJK prose still solves
        _F64F = "flag{c0ex1st_x0r_2026}"
        _d64f = os.path.join(scratch, "evolve64-baijiaxing-coexist")
        os.makedirs(_d64f, exist_ok=True)
        _p64f = os.path.join(_d64f, "enc.bin")
        with open(_p64f, "wb") as _f64:
            _f64.write(bytes(b ^ 0x5c for b in _F64F.encode("utf-8")))
        _p64f2 = os.path.join(_d64f, "note.txt")
        with open(_p64f2, "w", encoding="utf-8") as _f64:
            _f64.write("投毒检测靶场说明：本任务要求设计检测算法。\n")
        _files64f = [{"path": "enc.bin", "abs": _p64f,
                      "size": os.path.getsize(_p64f), "magic": None},
                     {"path": "note.txt", "abs": _p64f2,
                      "size": os.path.getsize(_p64f2), "magic": None}]
        _att64f = []
        _got64f = run_fast_path(_d64f, _files64f, bb58, _att64f)
        assert _got64f == _F64F, \
            "XOR flag must still solve alongside CJK prose, got %r" % _got64f
        _hit64f = [a for a in _att64f if a.success]
        assert _hit64f and _hit64f[0].detector == "xor-single", \
            "solve must credit the incumbent xor-single lane: %r" % (_hit64f[:1],)
        mod_results.append(True)
    except Exception:
        log("selftest evolve64-baijiaxing failed:"); traceback.print_exc()
        mod_results.append(False)

    log("module selftests: %d/%d" % (sum(mod_results), len(mod_results)))
    rc = 0 if (ok == len(results) and results and all(mod_results)) else 1
    return rc


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
    p.add_argument("--allow-replay", dest="allow_replay",
                   action="store_true",
                   help="[evolve-20261117-43] skip the production replay "
                        "guard: a same-(name,dir,flag) re-solve of an "
                        "already-credited row is normally suppressed in "
                        "place (rsi-111 lesson, row 964); set this only "
                        "for self-certified deliberate replays (fixture "
                        "re-runs, reproduction experiments)")

    p = sub.add_parser("bench-pull")
    p.add_argument("--source", default="",
                   help="local:<pool_dir> | gh-org:<org> (e.g. "
                        "gh-org:CTF-Archives) | gh:<owner>/<repo>")
    p.add_argument("--pool", default="",
                   help="legacy: local challenge pool dir (one challenge per "
                        "subdir); same as --source local:<dir>")
    p.add_argument("--dest", required=True,
                   help="destination dir (pulled challenges + ledger land here)")
    p.add_argument("--tag", default="",
                   help="batch tag recorded in the ledger")

    p = sub.add_parser("dedup-diff")
    p.add_argument("--force", action="store_true",
                   help="preview a FORCED rebuild diff (still read-only; "
                        "the mutating path is load_index/force=True)")

    p = sub.add_parser("learn-writeup")
    p.add_argument("file")
    p.add_argument("--name")

    p = sub.add_parser("recall")
    p.add_argument("--category")
    p.add_argument("--query", help="signal keywords, e.g. 'NTRU lattice LLL'")

    p = sub.add_parser("rebuild-knowledge")
    p.add_argument("dir", help="writeups directory (repo ctfpilot/writeups)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--exclude", default="",
                   help="holdout JSON (lab holdout.json): listed writeups are "
                        "held out of the build -- writes to "
                        "skills/knowledge-holdout + isolated lessons DB; "
                        "production KB and recall are not touched")

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

    p = sub.add_parser("account-model")
    p.add_argument("dir", help="challenge directory (sidecar lives here)")
    p.add_argument("--name", default="", help="challenge name (default: dir basename)")
    p.add_argument("--flag", default="", help="solved flag (marks the round as solved)")
    p.add_argument("--category", default="", help="category label (default: sidecar/misc)")
    p.add_argument("--time-s", dest="time_s", type=float, default=None,
    help="wall-clock seconds the model channel spent")
    p.add_argument("--cost", dest="cost_tokens", type=int, default=None,
    help="LLM tokens the model channel spent (cost axis, evolve-20261116-05)")
    p.add_argument("--summary", default="", help="one-line solve summary")
    p.add_argument("--dead-end", dest="dead_end", action="append",
    help="repeatable 'tool:seconds[:note]' measurable dead end")

    p = sub.add_parser("adjudicate")
    p.add_argument("dir", help="challenge directory (result file lives there)")
    p.add_argument("--true-status", dest="true_status", required=True,
                   help="adjudicated verdict, e.g. 'NOT SOLVED (dynamic "
                        "$FLAG, offline-unverifiable)'")
    p.add_argument("--reason", required=True,
                   help="why the engine verdict is overridden")
    p.add_argument("--by", default="",
                   help="adjudicator identity line (default: timestamp)")
    p.add_argument("--set-solved", dest="set_solved", choices=["0", "1"],
                   help="also rewrite the top-level solved verdict")
    p.add_argument("--set-flag", dest="set_flag", default=None,
                   help="also rewrite the top-level flag ('' clears it)")
    p.add_argument("--original-solved", dest="original_solved", default=None,
                   help="pre-adjudication solved (default: auto-captured)")
    p.add_argument("--original-flag", dest="original_flag", default=None,
                   help="pre-adjudication flag, JSON-escaped safely "
                        "(default: auto-captured)")
    p.add_argument("--field", action="append",
                   help="repeatable extra adjudication field key=value")

    # [evolve-20261117-18] DB-row adjudication face: rows whose dir holds no
    # _ctfpilot_result.json (the rsi-086 row-496 selftest-leak precedent)
    # were unreachable by the dir face; this runs the identical surgery
    # discipline straight against the challenges row.
    p = sub.add_parser("adjudicate-db")
    p.add_argument("--db-row", dest="db_row", type=int, required=True,
                   help="challenges row id (ledger id, see stats/dashboard)")
    p.add_argument("--true-status", dest="true_status", required=True,
                   help="adjudicated verdict, e.g. 'NOT SOLVED (selftest "
                        "fixture leak, not a challenge)'")
    p.add_argument("--reason", required=True,
                   help="why the stored verdict is overridden")
    p.add_argument("--by", default="",
                   help="adjudicator identity line (default: timestamp)")
    p.add_argument("--set-solved", dest="set_solved", choices=["0", "1"],
                   help="also rewrite the row's solved verdict")
    p.add_argument("--set-flag", dest="set_flag", default=None,
                   help="also rewrite the row's flag ('' clears it)")
    p.add_argument("--field", action="append",
                   help="repeatable extra adjudication field key=value")

    p = sub.add_parser("challenge-detail")
    p.add_argument("--id", type=int, default=None, help="challenge ledger id")
    p.add_argument("--name", default=None, help="challenge name (latest record)")


    p = sub.add_parser("eval-suite")
    p.add_argument("--save-baseline", action="store_true",
                   help="record the current snapshot as the new baseline "
                        "(after intentional improvements)")

    p = sub.add_parser("rsi")
    p.add_argument("action", nargs="?", default="cycle",
                   choices=["cycle", "list", "approve", "reject", "adopt"])
    p.add_argument("pid", nargs="?", default=None,
                   help="proposal id (for approve/reject/adopt)")
    p.add_argument("--status", help="list filter: proposed|approved|rejected|adopted")
    p.add_argument("--note", default="", help="free-text note recorded in history")
    p.add_argument("--chain-json", dest="chain_json", default=None,
                    help="adopt: JSON object with version-chain payload "
                         "(winner/rollback/evidence/next_candidates); "
                         "ledger+chain commit together or not at all")
    # [evolve-20261117-16] adoption snapshots: the CLI hashes the round's REAL
    # files into skills/rsi/snapshots/<pid>/ and guards every sha256 the
    # rollback text claims against them -- replaces the hand-computed hash
    # that produced the rsi-083 first-pass errata.
    p.add_argument("--candidate-file", dest="candidate_files_cli",
                   action="append", default=None,
                   help="adopt: an engine file this round CHANGED; hashed, "
                        "snapshotted durably, and guard-checked against the "
                        "rollback text (repeatable)")
    p.add_argument("--incumbent-file", dest="incumbent_files_cli",
                   action="append", default=None,
                   help="adopt: a pre-round snapshot file for rollback; "
                        "hashed + guard-checked the same way (repeatable)")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("arena")
    p.add_argument("action", nargs="?", default="stats",
    choices=["register", "pull", "record", "stats"])
    p.add_argument("--name", help="register/record: challenge name")
    p.add_argument("--category", help="register/record: category")
    p.add_argument("--source", default="",
    help="register: source tag (pool dir / gh:owner/repo / manual)")
    p.add_argument("--tier", choices=["easy", "normal", "hard"],
    help="register: manual tier override")
    p.add_argument("--dest", help="pull: destination dir (same as bench-pull)")
    p.add_argument("--fail", action="store_true",
    help="record: outcome was a failure")
    p.add_argument("--fast", action="store_true",
    help="record: solved via fast path only")
    p.add_argument("--time", dest="time_s", type=float, default=None,
    help="record: wall-clock seconds (practice clock, cost axis companion)")
    p.add_argument("--cost", dest="cost_tokens", type=int, default=None,
    help="record: LLM tokens spent (cost axis, evolve-20261116-05)")

    # [redrill] k-fold 交叉重刷训练协议 (详见 redrill.py 模块头注释的
    # 设计溯源; agent 侧只允许 runner 动作词, 面板/审计走全部动作)
    p = sub.add_parser("redrill")
    p.add_argument("action", nargs="?", default="status",
                   choices=["plan", "begin", "verify", "fast-try", "hint",
                            "record", "fold-done", "control", "status",
                            "finalize", "abandon", "audit", "diag",
                            "selftest"])
    p.add_argument("--slot", help="agent action: slot id (S01..)")
    p.add_argument("--flag", default="",
                   help="verify: candidate flag; engine never echoes the truth")
    p.add_argument("--via", choices=["fast", "manual", "fail", "missing"],
                   help="record: outcome path; fast/manual require engine "
                        "verify evidence")
    p.add_argument("--note", default="", help="record: optional note")
    p.add_argument("--level", type=int, default=0, help="hint: force level 1..3")
    p.add_argument("--seed", type=int, default=42, help="plan: fold shuffle seed")
    p.add_argument("--folds", type=int, default=5, help="plan: fold count")
    p.add_argument("--fold-size", dest="fold_size", type=int, default=10,
                   help="plan: max challenges per fold")
    p.add_argument("--sandbox", default="", help="plan/selftest: sandbox root override")
    p.add_argument("--registry", default="", help="plan/selftest: arena registry override")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="plan: preview folds/groups/missing WITHOUT creating the campaign state")
    p.add_argument("--pause", action="store_true", help="control")
    p.add_argument("--abort", action="store_true", help="control")
    p.add_argument("--continue", dest="continue_", action="store_true", help="control")
    p.add_argument("--skip-slot", dest="skip_slot", default="", help="control: mark slot skipped")
    p.add_argument("--as-json", dest="as_json", action="store_true",
                   help="status/audit: machine-readable output")
    p.add_argument("--sessions", nargs="*", default=[],
                   help="audit: session jsonl paths")

    p = sub.add_parser("research")
    p.add_argument("action", nargs="?", default="list",
                   choices=["add", "evidence", "ingest", "list"])
    p.add_argument("topic", nargs="?", default=None, help="topic title")
    p.add_argument("--category", default="misc", help="add: category tag")
    p.add_argument("--source", default="manual", help="add: origin tag")
    p.add_argument("--pointer", default="", help="evidence: source pointer")
    p.add_argument("--kind", default="local-file",
                   help="evidence: local-file | url | writeup")
    p.add_argument("--status", help="list filter: queued|drafting|ready-ingest|ingested")
    p.add_argument("--limit", type=int, default=50)


    args = ap.parse_args()
    cfg = load_config()
    if args.cmd == "solve":
        sys.exit(do_solve(args, cfg))
    elif args.cmd == "bench-pull":
        sys.exit(do_bench_pull(args, cfg))
    elif args.cmd == "dedup-diff":
        sys.exit(do_dedup_diff(args, cfg))
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
    elif args.cmd == "account-model":
        sys.exit(do_account_model(args, cfg))
    elif args.cmd == "adjudicate":
        sys.exit(do_adjudicate(args, cfg))
    elif args.cmd == "adjudicate-db":
        sys.exit(do_adjudicate_db(args, cfg))
    elif args.cmd == "challenge-detail":
        sys.exit(do_challenge_detail(args, cfg))
    elif args.cmd == "eval-suite":
        sys.exit(do_eval_suite(args, cfg))
    elif args.cmd == "rsi":
        sys.exit(do_rsi(args, cfg))
    elif args.cmd == "arena":
        sys.exit(do_arena(args, cfg))
    elif args.cmd == "redrill":
        sys.exit(do_redrill(args, cfg))
    elif args.cmd == "research":
        sys.exit(do_research(args, cfg))


def do_redrill(args, cfg):
    """k-fold 交叉重刷训练协议入口; 实现全部在 redrill.py。

    运维（2026-11-26 用户令「交付可运维」）：本入口是所有 redrill 调用的
    唯一收口（面板命令 / Host staging / agent runner / 人工 CLI），统一写
    <memory>/redrill-ops.log（JSONL：start→ok/error + 耗时毫秒；参数脱敏，
    flag 只记 sha8；异常经 except 落 error 后原样上抛，不吞错）。
    日志写入自身失败不阻断主流程（ops_log 内部已隔离）。
    """
    import redrill
    store = get_stores(cfg)
    a = args.action
    fn = {"plan": redrill.do_plan, "begin": redrill.do_begin,
          "verify": redrill.do_verify, "fast-try": redrill.do_fast_try,
          "hint": redrill.do_hint, "record": redrill.do_record,
          "fold-done": redrill.do_fold_done, "control": redrill.do_control,
          "status": redrill.do_status, "finalize": redrill.do_finalize,
          "abandon": redrill.do_abandon, "audit": redrill.do_audit,
          "diag": redrill.do_diag, "selftest": redrill.do_selftest}.get(a)
    if not fn:
        redrill.ops_log(a, "error", {"error": "unknown action"})
        log("[redrill][error] unknown action: %s" % a)
        return 2
    t0 = time.time()
    redrill.ops_log(a, "start", redrill.ops_snapshot_args(args))
    try:
        rc = fn(store, args)
    except SystemExit as e:
        redrill.ops_log(a, "error", {
            "error": "SystemExit", "rc": e.code,
            "ms": round((time.time() - t0) * 1000)})
        raise
    except Exception as e:
        redrill.ops_log(a, "error", {
            "error": "%s: %s" % (type(e).__name__, e),
            "ms": round((time.time() - t0) * 1000)})
        raise
    redrill.ops_log(a, "ok" if rc == 0 else "error",
                    {"rc": rc, "ms": round((time.time() - t0) * 1000)})
    return rc


if __name__ == "__main__":
    main()
