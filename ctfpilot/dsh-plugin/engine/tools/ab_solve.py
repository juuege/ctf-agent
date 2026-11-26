#!/usr/bin/env python3
"""ab_solve.py -- parameterized dual-engine-root A/B solve harness.

[evolve-20261118-02 / rsi-121] arena-reusable-ab-harness-v1 (entry-69
next_candidates #4, open since rsi-119): promote the per-round hand-rolled
dual-root ab_run.py one-offs (rsi-117 proof driver, rsi-118 twin-pool
workaround, rsi-119 runner A/B, rsi-121 rsi121_rg_solve.py) into one
engine-side tool so perf rounds stop re-deriving isolation discipline in
.temp scratch scripts.

What it does: for every challenge in the pool it runs ONE solve on the
incumbent engine root and ONE on the candidate engine root, under the
same isolation discipline run_ablation.py adopted in rsi-119
(per-run task copies that never carry engine byproducts, per-(side,
challenge) scratch sqlite, no in-place fallback -- copy failures are
honest crash rows), then emits a per-challenge semantic comparison and a
pooled summary (match rate, mean walls, adversarial clock on both sides
using the ctf-lab formula with --timeout as the miss penalty).

Unlike arena/run_ablation.py (harness VARIANTS over one engine), this
tool compares two ENGINE ROOTS -- the shape every RSI differential /
determinism proof actually needs. The engine bytes under both roots are
never touched; only <state> is written.

Exit code: 0 if every comparison row was produced (crash/timeout rows
are results, not failures); 2 on usage/structural errors.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

# Same boundary as arena/run_ablation.py [evolve-20261117-47]: engine-authored
# artifacts are not challenge material. '.solve*' covers the feed wrapper's
# stdout/stderr captures.
_TASK_COPY_IGNORE = shutil.ignore_patterns(
    "_ctfpilot*", "_extracted", "__pycache__", ".git", ".temp", ".solve*")

SEMANTIC_FIELDS = ("solved", "flag", "attempts", "category")


def _log(msg):
    print(msg, flush=True)


def _append_jsonl(path, row):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _crash_row(side, name, detail):
    return {
        "challenge": name, "side": side, "solved": False, "flag": None,
        "time_seconds": None, "wall_seconds": None, "attempts": None,
        "rc": None, "status": "crash", "category": None, "detail": detail,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _timeout_row(side, name, wall_s):
    return {
        "challenge": name, "side": side, "solved": False, "flag": None,
        "time_seconds": None, "wall_seconds": round(wall_s, 3),
        "attempts": None, "rc": None, "status": "timeout", "category": None,
        "detail": "wall budget exhausted; process tree killed",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _list_challenges(pool):
    out = []
    for name in sorted(os.listdir(pool)):
        d = os.path.join(pool, name)
        if os.path.isdir(d) and not name.startswith("."):
            out.append((name, d))
    if not out:
        raise SystemExit("error: pool %r has no challenge subdirectories" % pool)
    return out


def _run_one(side, root, chall_src, name, state, args, python):
    """One (side, challenge) solve inside a per-run copy + scratch db.

    Returns a result row. Never falls back to solving in place: a copy
    failure or a missing result file is an honest crash/timeout row.
    """
    tasks_root = os.path.join(state, "tasks")
    run_dir = os.path.join(tasks_root, side, name)
    db = os.path.join(state, "db", "%s__%s.sqlite" % (side, name))
    os.makedirs(os.path.dirname(db), exist_ok=True)
    for stale in (db, db + "-wal", db + "-shm"):
        if os.path.exists(stale):
            os.remove(stale)
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
    try:
        shutil.copytree(chall_src, run_dir, ignore=_TASK_COPY_IGNORE)
    except Exception as e:  # honest crash, no in-place fallback
        return _crash_row(side, name, "copy failed: %s: %s" % (type(e).__name__, e))

    env = dict(os.environ)
    env["CTFPILOT_PYTHON"] = python
    env["CTFPILOT_DB"] = db
    cmd = [python, "-X", "utf8", os.path.join(root, "ctfpilot.py"),
           "solve", run_dir, "--mode", args.mode, "--benchmark", "--json",
           "--write-trace"]
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd=root, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True)
    try:
        _out, err = p.communicate(timeout=args.timeout)
        wall = time.time() - t0
        rc = p.returncode
    except subprocess.TimeoutExpired:
        # tree-kill: the engine solve may spawn deep-step subprocesses;
        # a bare process kill would orphan them inside the run copy.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                       capture_output=True)
        return _timeout_row(side, name, args.timeout)

    res_path = os.path.join(run_dir, "_ctfpilot_result.json")
    if rc not in (0, 1) or not os.path.exists(res_path):
        tail = (err or "").strip().splitlines()
        detail = "rc=%s; %s" % (rc, tail[-1][:160] if tail else "no result json")
        return _crash_row(side, name, detail)
    try:
        res = json.load(open(res_path, encoding="utf-8"))
    except Exception as e:
        return _crash_row(side, name, "result json unreadable: %s" % e)
    return {
        "challenge": name, "side": side, "solved": bool(res.get("solved")),
        "flag": res.get("flag"), "time_seconds": res.get("time_seconds"),
        "wall_seconds": round(wall, 3), "attempts": res.get("attempts"),
        "rc": rc, "status": "ok",
        "category": res.get("category"), "detail": None,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _adversarial_clock(rows, timeout_s):
    """ctf-lab formula: mean(time|solved)*rate + timeout*(1-rate)."""
    n = len(rows)
    if not n:
        return None, 0.0
    solved_rows = [r for r in rows if r.get("solved") and r.get("time_seconds") is not None]
    rate = len(solved_rows) / n
    mean_solved = (sum(r["time_seconds"] for r in solved_rows) / len(solved_rows)
                   if solved_rows else 0.0)
    return mean_solved * rate + timeout_s * (1 - rate), rate


def main():
    ap = argparse.ArgumentParser(
        description="Dual-engine-root A/B solve harness (RSI differential/proof driver)")
    ap.add_argument("--inc-root", required=True, help="incumbent engine root (ctfpilot.py inside)")
    ap.add_argument("--cand-root", required=True, help="candidate engine root")
    ap.add_argument("--pool", required=True, help="challenge pool (one subdir per challenge)")
    ap.add_argument("--state", default=None, help="scratch root (default .temp/ab-solve-<ts>)")
    ap.add_argument("--out", default=None, help="results jsonl (default <state>/results.jsonl)")
    ap.add_argument("--timeout", type=float, default=200.0, help="per-run wall seconds")
    ap.add_argument("--mode", default="auto", help="solve mode (default auto)")
    ap.add_argument("--python", default=None,
                    help="python to drive solves (default CTFPILOT_PYTHON or sys.executable)")
    ap.add_argument("--keep-scratch", action="store_true",
                    help="keep the per-run task copies under <state>/tasks")
    args = ap.parse_args()

    for opt, val in (("--inc-root", args.inc_root), ("--cand-root", args.cand_root)):
        if not os.path.isfile(os.path.join(val, "ctfpilot.py")):
            raise SystemExit("error: %s=%r has no ctfpilot.py" % (opt, val))
    if not os.path.isdir(args.pool):
        raise SystemExit("error: --pool %r is not a directory" % args.pool)

    python = args.python or os.environ.get("CTFPILOT_PYTHON") or sys.executable
    state = args.state or os.path.join(".temp", "ab-solve-" +
                                       time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(state, exist_ok=True)
    out_path = args.out or os.path.join(state, "results.jsonl")

    challenges = _list_challenges(args.pool)
    _log("[ab-solve] python=%s mode=%s timeout=%gs state=%s" % (python, args.mode, args.timeout, state))
    _log("[ab-solve] inc=%s" % os.path.abspath(args.inc_root))
    _log("[ab-solve] cand=%s" % os.path.abspath(args.cand_root))

    rows = []
    tasks_root = os.path.join(state, "tasks")
    try:
        for name, src in challenges:
            inc_row = _run_one("inc", args.inc_root, src, name, state, args, python)
            _append_jsonl(out_path, inc_row)
            rows.append(inc_row)
            cand_row = _run_one("cand", args.cand_root, src, name, state, args, python)
            _append_jsonl(out_path, cand_row)
            rows.append(cand_row)

            semantic = all(inc_row.get(f) == cand_row.get(f) for f in SEMANTIC_FIELDS)
            inc_t = inc_row.get("time_seconds")
            cand_t = cand_row.get("time_seconds")
            t_disp = ("%.2fs/%.2fs" % (inc_t, cand_t)
                      if inc_t is not None and cand_t is not None else "n/a")
            _log("[ab-solve] name=%-28s match=%s attempts=%s/%s time=%s status=%s/%s"
                 % (name, semantic, inc_row.get("attempts"), cand_row.get("attempts"),
                    t_disp, inc_row.get("status"), cand_row.get("status")))
    finally:
        if not args.keep_scratch and os.path.isdir(tasks_root):
            shutil.rmtree(tasks_root, ignore_errors=True)

    # pooled summary
    inc_rows = [r for r in rows if r["side"] == "inc"]
    cand_rows = [r for r in rows if r["side"] == "cand"]
    pair_ok = 0
    for a, b in zip(inc_rows, cand_rows):
        if all(a.get(f) == b.get(f) for f in SEMANTIC_FIELDS):
            pair_ok += 1
    n_pairs = len(inc_rows)
    inc_clock, inc_rate = _adversarial_clock(inc_rows, args.timeout)
    cand_clock, cand_rate = _adversarial_clock(cand_rows, args.timeout)
    _log("[ab-solve] SUMMARY semantic_match=%d/%d solved_rate inc=%.3f cand=%.3f"
         % (pair_ok, n_pairs, inc_rate, cand_rate))
    _log("[ab-solve] SUMMARY adversarial_clock inc=%.2fs cand=%.2fs delta=%.1f%%"
         % (inc_clock, cand_clock,
            (1 - cand_clock / inc_clock) * 100 if inc_clock else float("nan")))
    _log("[ab-solve] results=%s" % os.path.abspath(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
