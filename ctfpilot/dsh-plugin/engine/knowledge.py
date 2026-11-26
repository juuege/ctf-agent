"""SOTA-style knowledge layer: case cards from writeups + relevance-scored recall.

Design (grounded in the 2026-11 SOTA review):
- Case cards (case-based units, cf. CASCADE / DomAgent): one per writeup,
  carrying 识别信号 / 核心技术链 / 陷阱 / 可迁移模式 / 失败原因 / 一得.
- Evidence pointers (WikiTrace): every case links back to its source writeup
  so the model can descend for details when a card is not enough.
- Relevance scoring (ArchOR / SKALOS): recall ranks by signal-token overlap
  with the current challenge signature, not recency alone.
- Idempotent lesson upsert: re-running rebuild-knowledge refreshes, never
  duplicates (stable case id as source fingerprint), and self-prunes stale
  case-card shadow rows (dead shadows + stale-category twins merged into
  their live twin) [evolve-20261117-07].
"""
import hashlib
import json
import os
import re
import time

from learn.writeup_learner import parse_writeup

CASE_TEXT_KEYS = ("name", "signals", "chain", "pitfalls",
                  "transferable", "variants", "carrier", "attempts",
                  "distinct", "excludes")
REFINED_FIELDS = ("signals", "distinct", "excludes", "chain", "pitfalls",
                  "transferable", "variants", "failure", "gain", "limits",
                  "carrier", "attempts", "category", "category_inferred")


def tokenize(text):
    """ASCII words (>=3) + CJK bigrams -- cheap lexical signature."""
    text = str(text or "")
    tokens = set()
    for w in re.findall(r"[A-Za-z0-9_]{3,}", text):
        tokens.add(w.lower())
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for i in range(len(run) - 1):
            tokens.add(run[i:i + 2])
    return tokens


def _case_text(c):
    return " ".join(str(c.get(k) or "") for k in CASE_TEXT_KEYS)


def case_tokens(c):
    return tokenize(_case_text(c))


def build_cases(writeups_dir):
    """Parse every writeup under writeups_dir into a case card."""
    cases = []
    for root, dirs, names in os.walk(writeups_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
        for fn in sorted(names):
            if not fn.endswith(".md") or fn.lower() == "readme.md":
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, writeups_dir).replace("\\", "/")
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                created = int(os.path.getmtime(p))
            except OSError:
                continue
            parsed = parse_writeup(text)
            name = parsed.get("name") or re.sub(r"^\d{3,4}-", "",
                                                 os.path.splitext(fn)[0])
            cases.append({
                "id": "case-" + hashlib.md5(rel.encode("utf-8")).hexdigest()[:8],
                "name": name,
                "category": parsed.get("category") or "misc",
                "solved": bool(parsed.get("solved")),
                "signals": parsed.get("signals", "") or parsed.get("carrier", ""),
                "chain": parsed.get("chain", "") or (parsed.get("notes", "") or "")[:160],
                "pitfalls": parsed.get("pitfalls", ""),
                "transferable": parsed.get("transferable", ""),
                "variants": parsed.get("variants", ""),
                "failure": parsed.get("failure_paths", ""),
                "gain": parsed.get("gain", ""),
                "limits": parsed.get("limits", ""),
                "carrier": parsed.get("carrier", ""),
                "attempts": parsed.get("attempts", ""),
                "category_inferred": bool(parsed.get("category_inferred")),
                "source": rel,
                "created": created,
            })
    cases.sort(key=lambda c: c["source"])
    return cases


def save_cases(cases, out_dir, extra_stats=None):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "cases.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    stats = {
        "cases": len(cases),
        "solved": sum(1 for c in cases if c.get("solved")),
        "with_signals": sum(1 for c in cases if c.get("signals")),
        "with_pitfalls": sum(1 for c in cases if c.get("pitfalls")),
        "with_transferable": sum(1 for c in cases if c.get("transferable")),
        "with_failure": sum(1 for c in cases if c.get("failure")),
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    stats.update(extra_stats or {})
    spath = os.path.join(out_dir, "stats.json")
    with open(spath, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return [path, spath]


def load_cases(engine_skill_dir):
    path = os.path.join(engine_skill_dir, "knowledge", "cases.jsonl")
    if not os.path.exists(path):
        return None
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except ValueError:
                continue
    return cases


def merge_refined(cases, existing_jsonl_path):
    """Preserve curated card fields across rebuilds (Zep-style: generation and
    curation are separate stages). rebuild-knowledge regenerates cases from
    the writeups; refined fields (rewritten signals / distinct / excludes,
    Skill++-style) live in an overlay keyed by case id so re-running the
    parser never clobbers curation."""
    if not existing_jsonl_path or not os.path.exists(existing_jsonl_path):
        return cases
    old = {}
    with open(existing_jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("id"):
                old[o["id"]] = o
    for c in cases:
        o = old.get(c["id"])
        if o:
            for k in REFINED_FIELDS:
                v = o.get(k, None)
                if v is None:
                    continue
                # booleans (e.g. category_inferred=False after human
                # adjudication) are values -- copy whenever present; strings
                # only when non-empty so empty overlays never clobber parses.
                # evolve-20261116-13: 'category' is curated ONLY when the
                # overlay row carries an adjudication (category_inferred
                # False or absent). An inferred vote from an older parse is
                # not curation and must follow the current parser, or parser
                # fixes can never reach cases.jsonl (live precedent: the dfjk
                # writeup's off-target crypto vote stayed locked across a
                # rebuild that had already fixed the vote).
                if k == "category" and o.get("category_inferred") is True:
                    continue
                if isinstance(v, bool) or v:
                    c[k] = v
    return cases


def _eval_query_text(path):
    """Query = the writeup's own 题面 description only. Deliberately excludes
    the answer-side fields (signals/chain) AND the 附件/载体 lines: the
    carrier line is the same-source text of the target card's own carrier
    field, so keeping it made the eval rank a card against its own words
    (lab leak probe 2026-11-13: 15/28 evaluable queries overlapped their own
    card's carrier field >=80%). Production queries are built from the live
    challenge signature, never from this writeup line, so dropping it makes
    the eval honest without touching production behavior."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        t = f.read()
    parts = []
    m = re.search(r"^[ \t]*(?:[-*•]\s*)?(?:\d+[.、)]\s*)?题目\s*[:：]\s*(.+)$", t, re.M)
    if m:
        parts.append(m.group(1))
    m = re.search(r"^#{1,4}\s*题目\s*$\n(.+?)(?=\n#{1,4}\s|\Z)", t, re.M | re.S)
    if m:
        parts.append(" ".join(m.group(1).split()[:120]))
    return " ".join(parts)


def eval_retrieval(cases, writeups_dir, per_item=False):
    """Held-out retrieval regression (固化版留出评测):
    for each case build a query from its own writeup's 题面 description (minus
    the case name tokens), then rank within the target's recorded category pool
    using the production scorer (token overlap over CASE_TEXT_KEYS, recency
    tiebreak -- category bonus is constant within a pool). Returns metrics
    for the production ranker ('signal') and a recency-only reference.

    [evolve-20261118-04 / rsi-123] per_item=True additionally returns
    out["per_item"]: one row per evaluable query carrying the target card's
    id/name/category and its signal/recency rank, so callers (the lab poison
    probe) can compare rankings across a mutated KB instead of only the
    aggregate top1/top3 -- the aggregate alone has a single-item resolution of
    100/evaluable pp (5.3pp at evaluable=19) and cannot attribute a flat
    delta to "insensitive metric" vs "dead payload channel" vs "poisoned
    cards outside the evaluable set". Additive opt-in: per_item=False keeps
    the returned dict byte-compatible for every existing caller."""
    qs = []
    for c in cases:
        p = os.path.join(writeups_dir, c["source"])
        if not os.path.exists(p):
            continue
        q = tokenize(_eval_query_text(p)) - tokenize(c["name"])
        if len(q) >= 6:
            qs.append((c, q))
    ranks = {"signal": [], "recency": []}
    per = []
    for target, q in qs:
        pool = [c for c in cases if c.get("category") == target.get("category")]
        if len(pool) < 2:
            pool = cases
        scored = sorted(((len(q & case_tokens(c)), -float(c.get("created") or 0), c["id"])
                         for c in pool), key=lambda x: (-x[0], x[1]))
        r = next(i for i, (_s, _t, cid) in enumerate(scored, 1)
                 if cid == target["id"])
        ranks["signal"].append(r)
        by_time = sorted(pool, key=lambda c: -float(c.get("created") or 0))
        rr = next(i for i, c in enumerate(by_time, 1)
                  if c["id"] == target["id"])
        ranks["recency"].append(rr)
        if per_item:
            per.append({"id": target["id"], "name": target.get("name"),
                        "category": target.get("category"),
                        "signal_rank": r, "recency_rank": rr})
    out = {"evaluable": len(qs), "total": len(cases)}
    for k, rr in ranks.items():
        n = len(rr)
        out[k] = {
            "top1": round(sum(1 for r in rr if r == 1) / n, 4) if n else 0.0,
            "top3": round(sum(1 for r in rr if r <= 3) / n, 4) if n else 0.0,
            "mrr": round(sum(1.0 / r for r in rr) / n, 4) if n else 0.0,
        }
    if per_item:
        out["per_item"] = per
    return out


def rank_cases(cases, category, query_tokens, top_k=3):
    """Score = signal overlap + category bonus; ties broken by recency."""
    scored = []
    for c in cases:
        overlap = len(query_tokens & case_tokens(c))
        cat_bonus = 3.0 if c.get("category") == category else 0.0
        scored.append((overlap + cat_bonus, -float(c.get("created") or 0), c))
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = [c for _s, _t, c in scored[:top_k]]
    return top


def _prune_case_shadows(store, cases):
    """Prune pass appended to rebuild_lessons [evolve-20261117-07].

    Root cause fixed: rebuild_lessons was upsert-only, so category
    re-adjudication left stale-category twin rows (rsi-070: 54 twins)
    and KB case removal left dead shadow rows (qwqsncx: 1) forever;
    rsi-071 proved the mechanism and cleaned the stock by hand. This
    pass makes rebuild-knowledge self-cleaning so the leak probe's
    lessons dimensions stay 0 without bookkeeping surgery rounds.

    Same three-way classification as lab/probes.py lesson_shadow_audit
    (same predicates, same cid=0 case-card sentinel semantics):
    - dead shadow (case id unparsable or no longer in the KB case set)
      -> DELETE;
    - stale category twin (id in KB, row category != KB current
      verdict) with a live twin present -> merge hit_count/success_count
      into the live twin, then DELETE (rsi-071 surgery predicates);
    - live shadow -> keep. Conservative: a twin whose live twin is
      missing is KEPT (it is the sole content carrier -- never lose
      data to a prune).

    Idempotent: a second run deletes/merges nothing. Single
    transaction, commit last (rsi-071 finding (e): verify from a
    separate process after commit). Returns (dead_removed, twins_merged).
    """
    kb = {c.get("id"): c for c in cases}
    rows = store.conn.execute(
        "SELECT id, category, when_to_use, hit_count, success_count "
        "FROM lessons WHERE challenge_id=0").fetchall()
    live_by_hash, dead, twins = {}, [], []
    for rid, cat, wtu, hit, succ in rows:
        m = re.search(r"case '(case-[0-9a-f]+)'", wtu or "")
        if not m or m.group(1) not in kb:
            dead.append(rid)
        elif kb[m.group(1)].get("category") == cat:
            live_by_hash.setdefault(m.group(1), rid)
        else:
            twins.append((rid, m.group(1), hit or 0, succ or 0))
    merged, gone = 0, list(dead)
    for rid, h, hit, succ in twins:
        live_id = live_by_hash.get(h)
        if live_id is None:
            continue  # conservative keep: sole content carrier
        store.conn.execute(
            "UPDATE lessons SET hit_count=COALESCE(hit_count,0)+?, "
            "success_count=COALESCE(success_count,0)+? WHERE id=?",
            (hit, succ, live_id))
        merged += 1
        gone.append(rid)
    if gone:
        store.conn.executemany("DELETE FROM lessons WHERE id=?",
                               [(rid,) for rid in gone])
    store.conn.commit()
    return len(dead), merged


def rebuild_lessons(store, cases):
    """Upsert one lesson row per case (stable id -> idempotent re-runs),
    then prune stale case-card shadows [evolve-20261117-07]: rebuild is
    now refresh-only -- category re-adjudication and case removal are
    both followed by self-cleanup instead of leaving twin/dead rows.
    Returns (upserted, dead_removed, twins_merged)."""
    n = 0
    for c in cases:
        wtu = "case '%s': %s" % (c["id"], (c.get("signals") or c.get("name") or "")[:160])
        evidence = " | ".join(filter(None, [
            ("链: " + c["chain"])[:200] if c.get("chain") else "",
            ("坑: " + c["pitfalls"])[:150] if c.get("pitfalls") else "",
            ("迁移: " + c["transferable"])[:150] if c.get("transferable") else "",
            ("失败: " + c["failure"])[:150] if c.get("failure") else "",
            ("尝试: " + c.get("attempts", ""))[:150] if c.get("attempts") else "",
            ("一得: " + c["gain"])[:150] if c.get("gain") else "",
        ]))
        tags = {k: (c.get(k) or "")
                for k in ("signals", "pitfalls", "transferable", "variants") if c.get(k)}
        tags_json = json.dumps({"识别信号": c.get("signals", ""),
                                "陷阱类型": c.get("pitfalls", ""),
                                "可迁移模式": c.get("transferable", ""),
                                "变体预测": c.get("variants", "")},
                               ensure_ascii=False) if any(tags.values()) else ""
        store.add_lesson(0, c["category"], ("case:" + c["name"])[:80],
                         wtu, evidence, tags=tags_json)
        n += 1
    dead, twins = _prune_case_shadows(store, cases)
    return n, dead, twins
