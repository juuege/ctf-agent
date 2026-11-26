"""Metrics: turn trajectory data into measurable indicators and gate every
system change against a baseline (精进评价指标本身).

Dimensions (each independently reported; the gated subset decides PASS/FAIL):
  - capability : smoke-set solve rate -- the engine's hard floor
  - efficiency : median time-to-solve, fast-path share (zero-LLM solves),
                 cost axis (tokens per solve / attempts per solve)
  - coverage   : per-category totals/rates, distinct challenge count
  - health     : detector hit ratios, failure-taxonomy distribution
  - knowledge  : lesson/case counts, signal-carrying ratio
  - retrieval  : top1 / top3 / MRR (reuses the production held-out eval)

Gate policy mirrors eval-retrieval's 5pp rule and adds floors that may never
shrink (smoke rate, case counts). A metric absent from the baseline is
skipped until --save-baseline records one; an unknown current value never
fails the gate (unknown beats falsely-red).

Cost axis (evolve-20261116-05): token cost is the environment-normalized
currency of work volume -- same trajectory, same number, no wall-clock
noise. It is a CEILING (may not rise beyond COST_RISE_MAX) so an evolution
that wins time by hiding work in extra tokens is caught. admit_candidate()
is the dual-axis promotion rule: the adversarial-clock time rule stays
unchanged; cost can only ever reject, never rescue a slower candidate.
"""
import json
import os
import sqlite3
import statistics
import time


# Solve-mode ledger view (same口径 as dashboard/lessons: 'learn' import rows
# are excluded so metrics measure real solving).
LATEST_CTE = """
WITH latest AS (
    SELECT name, MAX(id) AS id FROM challenges GROUP BY name
),
ledger AS (
    SELECT c.id, c.name, c.category, c.solved, c.flag, c.mode, c.summary,
           c.created_at, datetime(c.created_at, 'unixepoch', 'localtime') AS ts_str
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

# Gate tolerances: capability is a hard floor (deterministic smoke set);
# retrieval mirrors the 5pp policy of eval-retrieval.
GATE_TOLERANCES = {
    "smoke_pass_rate": 0.0,     # any smoke regression fails
    "retrieval_top1": 0.05,     # 5pp policy
    "retrieval_top3": 0.05,
}

# Cost ceiling: tokens-per-solve may rise at most this much relative to the
# baseline (10%). Cost is noisy across challenge mixes, so this is not a
# hard floor like smoke -- it only catches blow-ups, not drift.
COST_RISE_MAX = 0.10


def compute_metrics(db_path, knowledge=None, smoke=None):
    """Snapshot every dimension from one trajectory DB (+ optional knowledge /
    smoke context). Pure read: never writes anything."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(LATEST_CTE + """
            SELECT COUNT(*) AS total, SUM(solved) AS solved
            FROM ledger ld JOIN rowkind k ON k.name = ld.name
            WHERE k.kind = 'solve'
        """).fetchone()
        total, solved = row[0] or 0, row[1] or 0

        dur_rows = conn.execute(LATEST_CTE + """
            SELECT ld.created_at, MAX(a.ts) AS last_ts
            FROM ledger ld JOIN rowkind k ON k.name = ld.name
            LEFT JOIN attempts a ON a.challenge_id = ld.id
            WHERE k.kind = 'solve' AND ld.solved = 1
            GROUP BY ld.id, ld.created_at
        """).fetchall()
        durations = []
        for r in dur_rows:
            if r["last_ts"] and r["created_at"]:
                d = r["last_ts"] - r["created_at"]
                if 0 < d < 86400:  # sanity cap: within one day
                    durations.append(d)

        fast_solved = conn.execute(LATEST_CTE + """
            SELECT COUNT(DISTINCT a.challenge_id)
            FROM attempts a JOIN ledger ld ON ld.id = a.challenge_id
            JOIN rowkind k ON k.name = ld.name
            WHERE k.kind = 'solve' AND ld.solved = 1
              AND a.phase = 'fast' AND a.success = 1
        """).fetchone()[0]

        # cost axis (solve-mode ledger, learn imports excluded): token total,
        # per-solve mean, attempt volume, and the zero-cost native share
        crow = conn.execute(LATEST_CTE + """
            SELECT COUNT(DISTINCT ld.id), SUM(COALESCE(a.cost_tokens, 0)), COUNT(*)
            FROM ledger ld JOIN rowkind k ON k.name = ld.name
            LEFT JOIN attempts a ON a.challenge_id = ld.id
            WHERE k.kind = 'solve' AND ld.solved = 1
        """).fetchone()
        cost_solved, cost_tokens, cost_atts = crow[0] or 0, crow[1] or 0, crow[2] or 0
        zero_cost = conn.execute(LATEST_CTE + """
            SELECT COUNT(*) FROM (
                SELECT ld.id, SUM(COALESCE(a.cost_tokens, 0)) AS t
                FROM ledger ld JOIN rowkind k ON k.name = ld.name
                LEFT JOIN attempts a ON a.challenge_id = ld.id
                WHERE k.kind = 'solve' AND ld.solved = 1
                GROUP BY ld.id HAVING t = 0
            )
        """).fetchone()[0]

        bycat = [{"cat": r["category"], "total": r["total"],
                  "solved": r["solved"] or 0}
                 for r in conn.execute(LATEST_CTE + """
            SELECT category, COUNT(*) AS total, SUM(solved) AS solved
            FROM ledger ld JOIN rowkind k ON k.name = ld.name
            WHERE k.kind = 'solve' GROUP BY category ORDER BY total DESC
        """)]

        det = [{"phase": r["phase"], "detector": r["detector"],
                "attempts": r["attempts"], "hits": r["hits"] or 0}
               for r in conn.execute("""
            SELECT phase, detector, COUNT(*) AS attempts, SUM(success) AS hits
            FROM attempts WHERE phase IN ('fast','deep-pre','exec','verify')
            GROUP BY phase, detector ORDER BY phase, hits DESC
        """)]

        errs = [{"class": r["error_class"], "count": r["cnt"],
                 "ok": r["ok"] or 0}
                for r in conn.execute(LATEST_CTE + """
            SELECT a.error_class, COUNT(*) AS cnt, SUM(a.success) AS ok
            FROM attempts a JOIN ledger ld ON ld.id = a.challenge_id
            JOIN rowkind k ON k.name = ld.name
            WHERE k.kind = 'solve' AND COALESCE(a.error_class,'') NOT IN ('','ok')
            GROUP BY a.error_class ORDER BY cnt DESC LIMIT 12
        """)]

        lessons = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        attempts_all = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        distinct = conn.execute("""
            SELECT COUNT(*) FROM (SELECT name FROM challenges
                                  WHERE COALESCE(mode,'') <> 'learn')
        """).fetchone()[0]
    finally:
        conn.close()

    m = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "overall": {"total": total, "solved": solved,
                    "rate": round(solved * 100 / max(total, 1), 1)},
        "efficiency": {
            "median_time_to_solve_s": round(statistics.median(durations), 2)
            if durations else None,
            "durations_n": len(durations),
            "fast_path_solved": fast_solved,
            "fast_path_share": round(fast_solved * 100 / max(solved, 1), 1),
            "cost_tokens_total": int(cost_tokens),
            "cost_per_solve": round(cost_tokens / cost_solved, 1)
            if cost_solved else None,
            "attempts_on_solved": cost_atts,
            "attempts_per_solve": round(cost_atts / cost_solved, 2)
            if cost_solved else None,
            "zero_cost_solved": zero_cost,
            "zero_cost_share": round(zero_cost * 100 / max(solved, 1), 1),
        },
        "coverage": {"distinct": distinct, "categories": len(bycat),
                     "bycat": bycat},
        "health": {"detectors": det, "errors": errs, "attempts": attempts_all,
                   "lessons": lessons},
        "knowledge": dict(knowledge or {}),
        "capability": {},
    }
    if smoke:
        m["capability"] = {"smoke_total": smoke.get("total"),
                           "smoke_pass_rate": smoke.get("pass_rate")}
    return m


def gate_metrics(current, baseline, tolerances=None):
    """Compare current snapshot vs baseline per gated metric.
    Returns {passed, checks:[{metric, base, now, delta?, ok, note?}]}."""
    tol = dict(GATE_TOLERANCES)
    if tolerances:
        tol.update(tolerances)
    checks = []

    def cmp_pp(key, base_v, now_v):
        if base_v is None or now_v is None:
            checks.append({"metric": key, "base": base_v, "now": now_v,
                           "ok": True, "note": "no baseline / unknown value"})
            return
        delta = (now_v - base_v) * 100
        checks.append({"metric": key, "base": base_v, "now": now_v,
                       "delta": round(delta, 2), "ok": delta >= -tol[key],
                       "note": "±%.0fpp tolerance" % (tol[key] * 100)})

    cur_smoke = (current or {}).get("capability", {})
    smoke_b = (baseline or {}).get("capability", {}).get("smoke_pass_rate")
    if smoke_b is not None and cur_smoke.get("pass_rate") is not None:
        cmp_pp("smoke_pass_rate", smoke_b, cur_smoke["pass_rate"])
    for key, bucket, field in (("retrieval_top1", "retrieval", "top1"),
                               ("retrieval_top3", "retrieval", "top3")):
        b_v = (baseline or {}).get(bucket, {}).get(field)
        n_v = (current or {}).get(bucket, {}).get(field)
        if b_v is not None:
            cmp_pp(key, b_v, n_v)

    # Knowledge health: these counts must never shrink (knowledge only grows;
    # regressions here mean the ingestion pipeline silently lost data).
    for metric, b_key, n_key in (("knowledge_cases", "cases", "cases"),
                                 ("cases_with_signals", "with_signals",
                                  "with_signals")):
        b_v = (baseline or {}).get("knowledge", {}).get(b_key)
        n_v = (current or {}).get("knowledge", {}).get(n_key)
        if b_v is not None and n_v is not None:
            checks.append({"metric": metric, "base": b_v, "now": n_v,
                           "delta": n_v - b_v, "ok": n_v >= b_v,
                           "note": "non-shrink floor"})

    # Cost ceiling (evolve-20261116-05): tokens-per-solve may not rise beyond
    # COST_RISE_MAX. A zero-cost incumbent going positive is an infinite rise
    # and fails. Missing values on either side skip (unknown beats falsely-red).
    b_cost = (baseline or {}).get("efficiency", {}).get("cost_per_solve")
    n_cost = (current or {}).get("efficiency", {}).get("cost_per_solve")
    if b_cost is not None and n_cost is not None:
        if b_cost > 0:
            rise = (n_cost - b_cost) / b_cost
            checks.append({"metric": "cost_per_solve", "base": b_cost,
                           "now": n_cost,
                           "delta": round(rise * 100, 2),
                           "ok": rise <= COST_RISE_MAX,
                           "note": "cost ceiling +%.0f%%" % (COST_RISE_MAX * 100)})
        elif n_cost > 0:
            checks.append({"metric": "cost_per_solve", "base": b_cost,
                           "now": n_cost, "delta": None, "ok": False,
                           "note": "zero-cost incumbent must stay zero-cost"})
    passed = all(c["ok"] for c in checks)
    return {"passed": passed, "checks": checks}


def _axis(m, *keys):
    """First non-None value among keys, coerced to float (None if absent)."""
    for k in keys:
        v = (m or {}).get(k)
        if v is not None:
            return float(v)
    return None


def admit_candidate(incumbent, candidate, threshold=0.03, beta0=0.10,
                    beta1=1.0):
    """Dual-axis promotion gate (evolve-20261116-05, RRSI-style allowance).

    The time rule is UNCHANGED from the adversarial-clock gate: the candidate
    must clear `threshold` (dS >= 3%). Cost is an ADDITIONAL constraint that
    can only ever reject, never rescue a slower candidate:

        dS = (t_inc - t_cand) / t_inc     # time gain (higher = better)
        dC = (c_cand - c_inc) / c_inc    # relative cost rise
        admit = dS >= threshold and dC <= beta0 + beta1 * dS

    A clear time win therefore buys a bounded cost allowance: with the
    defaults a 3% win tolerates at most 13% more tokens per solve. Cost
    unknown on either side -> the cost check is skipped and the time rule
    decides alone (unknown beats falsely-red, same policy as gate_metrics).
    A zero-cost incumbent admits only a zero-cost candidate.

    Inputs are flat dicts: {"time": adversarial seconds, "cost": tokens per
    solve}; "adversarial_time" / "cost_per_solve" keys are also accepted.
    Pure function, no I/O. Returns {"admit", "dS", "dC", "checks"}.
    """
    t0 = _axis(incumbent, "adversarial_time", "time")
    t1 = _axis(candidate, "adversarial_time", "time")
    c0 = _axis(incumbent, "cost_per_solve", "cost")
    c1 = _axis(candidate, "cost_per_solve", "cost")
    checks = []
    dS = dC = None
    admit = True
    if t0 is None or t1 is None:
        checks.append({"metric": "time", "ok": False,
                       "note": "adversarial time unknown -- cannot admit"})
        admit = False
    else:
        dS = (t0 - t1) / t0 if t0 > 0 else 0.0
        ok_t = dS >= threshold
        checks.append({"metric": "time", "base": t0, "now": t1,
                       "dS": round(dS, 4), "ok": ok_t,
                       "note": "adversarial-clock rule unchanged "
                               "(dS >= %.0f%%)" % (threshold * 100)})
        if not ok_t:
            admit = False
    if c0 is None or c1 is None:
        checks.append({"metric": "cost", "ok": True,
                       "note": "cost unknown on one side -- skipped"})
    elif c0 <= 0:
        ok_c = c1 <= 0
        checks.append({"metric": "cost", "base": c0, "now": c1,
                       "ok": ok_c,
                       "note": "zero-cost incumbent: any token spend fails"})
        if not ok_c:
            admit = False
    else:
        dC = (c1 - c0) / c0
        allowance = beta0 + beta1 * (dS if dS is not None else 0.0)
        ok_c = dC <= allowance
        checks.append({"metric": "cost", "base": c0, "now": c1,
                       "dC": round(dC, 4), "allowance": round(allowance, 4),
                       "ok": ok_c,
                       "note": "time win buys bounded cost "
                               "(beta0=%.2f, beta1=%.2f)" % (beta0, beta1)})
        if not ok_c:
            admit = False
    return {"admit": admit,
            "dS": None if dS is None else round(dS, 4),
            "dC": None if dC is None else round(dC, 4),
            "checks": checks}


def snapshot_history_append(entry, path):
    """Append one timestamped snapshot to the trend log (never rewrites)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")