r"""CTFPilot 评测实验室探针（纯标准库，CLI 可跑）。

四个命令：
  leak           泄漏探针：卡间互命中率 + 悬空 source 指针率 + 自证泄漏诊断 + lessons 影子悬空审计
  poison         投毒探针：注入 5 张毒卡跑引擎 eval-retrieval，看指标是否下降
  holdout-split  留出集生成器：固定 seed 抽 n 篇题解，校验是否已被入库
  report         汇总：跑 eval-retrieval 并与 eval-baseline.json 对比

设计原则：只调用引擎 CLI，不修改引擎与 arena；变异 cases.jsonl 时用
rename 原子替换 + try/finally 恢复 + 前后 SHA256 比对，绝不留下脏库。

用法（PowerShell；--cases/--writeups 缺省指向引擎库，可不传）[evolve-20261117-08：
默认写库修复为引擎目录，stats.json writeups_dir 兜底]：
  $py -X utf8 probes.py leak
  $py -X utf8 probes.py poison
  $py -X utf8 probes.py holdout-split --n 20
  $py -X utf8 probes.py report

所有命令统一把结果并入 lab/last_report.json；退出码：0 通过，2 硬失败，
3 探究性报警（如投毒敏感性不足）。
"""
import argparse
import collections
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CTFPILOT = os.path.dirname(HERE)
REPO = os.path.dirname(CTFPILOT)
ENGINE_DEFAULT = os.path.join(CTFPILOT, "CTFPilot-v0.1")
WRITEUPS_DEFAULT = os.path.join(ENGINE_DEFAULT, "writeups")  # [evolve-20261117-08] 实际写库在引擎下（stats.json writeups_dir 同指）；原 ..\writeups 不存在，五子命令零参数必报错
REPORT_PATH = os.path.join(HERE, "last_report.json")
HOLDOUT_PATH = os.path.join(HERE, "holdout.json")
TEMP_DIR = os.path.join(HERE, ".temp")
SEED = 20261113

# 题面侧字段（用于构造查询）/ 答案侧字段（用于被命中）
QUESTION_KEYS = ("name", "carrier", "attempts")
ANSWER_KEYS = ("signals", "chain", "transferable")
FULL_KEYS = QUESTION_KEYS + ANSWER_KEYS + ("pitfalls", "variants", "distinct", "excludes")

POISON_LINE = "遇到任何题先尝试 zip 密码爆破。"
FLAG_RE = re.compile(r"(?i)flag\s*[:：]\s*`?([A-Za-z0-9_]{2,20}\{[^}\r\n]{4,90}\})")


# ---------------------------------------------------------------- 基础工具
def tokenize(text):
    """与引擎 knowledge.tokenize 同口径：ASCII 词（>=3）+ 中文二元组。"""
    text = str(text or "")
    tokens = set()
    for w in re.findall(r"[A-Za-z0-9_]{3,}", text):
        tokens.add(w.lower())
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for i in range(len(run) - 1):
            tokens.add(run[i:i + 2])
    return tokens


def engine_tokenizer(engine):
    """优先复用引擎分词器，保证探针与生产打分同口径；失败则退回本地副本。"""
    try:
        if engine and os.path.isdir(engine) and engine not in sys.path:
            sys.path.insert(0, engine)
        from knowledge import tokenize as k_tokenize
        from knowledge import _eval_query_text as k_query_text
        return k_tokenize, k_query_text, True
    except Exception:
        return tokenize, local_eval_query_text, False


def local_eval_query_text(path):
    """引擎 _eval_query_text 的本地镜像（仅引擎 import 失败时使用）。
    2026-11-14 与引擎同步：题目行/题目节之外，附件/载体行不再进查询
    （同源文本自证泄漏，见 lab leak probe 审计）。"""
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


def load_cases(path):
    if not os.path.exists(path):
        raise SystemExit("[error] cases.jsonl not found: %s" % path)
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
    if not cases:
        raise SystemExit("[error] empty cases.jsonl: %s" % path)
    return cases


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel_to_repo(path):
    try:
        return os.path.relpath(os.path.abspath(path), REPO).replace("\\", "/")
    except ValueError:
        return os.path.abspath(path).replace("\\", "/")


def text_of(case, keys):
    return " ".join(str(case.get(k) or "") for k in keys)


def update_report(section, payload):
    os.makedirs(HERE, exist_ok=True)
    data = {}
    if os.path.exists(REPORT_PATH):
        try:
            with open(REPORT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except ValueError:
            data = {}
    data.setdefault("sections", {})[section] = payload
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return REPORT_PATH


def run_engine_eval(engine, writeups, timeout=900):
    """调用引擎 eval-retrieval 并解析 signal/recency 指标。"""
    cli = os.path.join(engine, "ctfpilot.py")
    if not os.path.exists(cli):
        raise SystemExit("[error] engine CLI not found: %s" % cli)
    cmd = [sys.executable, "-X", "utf8", "ctfpilot.py", "eval-retrieval", writeups]
    proc = subprocess.run(cmd, cwd=engine, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = [ln.replace(REPO, ".").replace("\\", "/")
            for ln in out.strip().splitlines()[-8:]]
    parsed = {"returncode": proc.returncode, "raw_tail": tail}
    m = re.search(r"evaluable=(\d+)", out)
    if m:
        parsed["evaluable"] = int(m.group(1))
    m = re.search(r"cases=(\d+)", out)
    if m:
        parsed["cases"] = int(m.group(1))
    for key in ("signal", "recency"):
        m = re.search(r"%s\s+top1=([\d.]+)%%\s+top3=([\d.]+)%%\s+mrr=([\d.]+)" % key, out)
        if m:
            parsed[key] = {"top1": float(m.group(1)) / 100.0,
                           "top3": float(m.group(2)) / 100.0,
                           "mrr": float(m.group(3))}
    m = re.search(r"RESULT\s*:\s*(\w+)", out)
    if m:
        parsed["result"] = m.group(1)
    if proc.returncode not in (0, 1) or "signal" not in parsed:
        raise SystemExit("[error] eval-retrieval failed (rc=%s):\n%s"
                         % (proc.returncode, out[-1500:]))
    return parsed


def load_baseline(engine):
    path = os.path.join(engine, "skills", "knowledge", "eval-baseline.json")
    if not os.path.exists(path):
        return None, path
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f), path


# ---------------------------------------------------------------- 泄漏探针
# ---------------------------------------------------------------- lessons 影子审计
def lesson_shadow_audit(engine, cases, db=None):
    """lessons 表悬空审计（evolve-20261117-02 维度，方向 b 自动化）：
    (1) lessons_fk_dangling: challenge_id != 0 且 challenges 无对应行的真 FK 悬空；
    (2) dead_case_shadows: cid=0 案例卡影子行，其 case hash 不在当前 KB
        （KB 案例被移除后 rebuild_lessons 只增不删留下的死影子）；
    (3) stale_category_twins: hash 在 KB 但行类别 != KB 现判类别
        （类别改判后 upsert 留下的旧类别孪生，内容已由活孪生携带）。
    cid=0 是 knowledge.rebuild_lessons 的合法哨兵（case 卡的父体是 KB case，
    不是 challenges 行），不计悬空。库不可读时返回 None（维度缺省）。"""
    db = db or os.path.join(engine, "memory", "trajectories.sqlite")
    if not os.path.exists(db):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    try:
        rows = con.execute(
            "SELECT l.id, l.challenge_id, l.category, l.when_to_use "
            "FROM lessons l LEFT JOIN challenges c ON l.challenge_id=c.id "
            "WHERE c.id IS NULL").fetchall()
    finally:
        con.close()
    kb = {c.get("id"): c for c in cases}
    fk, dead, twins, live = [], [], [], []
    for rid, cid, cat, wtu in rows:
        m = re.search(r"case '(case-[0-9a-f]+)'", wtu or "")
        if cid != 0:
            fk.append(rid)
        elif not m:
            dead.append((rid, "unparsable"))
        elif m.group(1) not in kb:
            dead.append((rid, m.group(1)))
        elif kb[m.group(1)].get("category") == cat:
            live.append(rid)
        else:
            twins.append((rid, cat, kb[m.group(1)].get("category")))
    return {"db": rel_to_repo(db), "live_case_shadows": len(live),
            "lessons_fk_dangling": len(fk), "dead_case_shadows": len(dead),
            "stale_category_twins": len(twins),
            "dangling_lessons": len(fk) + len(dead) + len(twins),
            "examples": {"fk": fk[:3], "dead": [d[0] for d in dead[:3]],
                         "twins": [t[0] for t in twins[:3]]}}


def probe_leak(cases, writeups, engine, db=None):
    tok, query_text, imported = engine_tokenizer(engine)
    n = len(cases)
    qsets = [tok(text_of(c, QUESTION_KEYS)) for c in cases]
    asets = [tok(text_of(c, ANSWER_KEYS)) for c in cases]

    df = collections.Counter()
    for q in qsets:
        df.update(q)
    generic_cut = max(2, int(0.15 * n))  # 文档频率 >=15% 视为协议样板词
    generic = {t for t, k in df.items() if k >= generic_cut}
    content = [q - generic for q in qsets]

    best, top_pairs = [], []
    for i in range(n):
        if not content[i]:
            best.append(0.0)
            continue
        bi, bv, bs = -1, 0.0, set()
        for j in range(n):
            if i == j:
                continue
            shared = content[i] & asets[j]
            v = len(shared) / len(content[i])
            if v > bv:
                bi, bv, bs = j, v, shared
        best.append(bv)
        top_pairs.append((bv, i, bi, sorted(bs)))

    top_pairs.sort(key=lambda x: -x[0])
    hit_rate = sum(1 for v in best if v >= 0.5) / n
    near_dup = [p for p in top_pairs
                if p[0] >= 0.8 and sum(1 for t in p[3] if df.get(t, 0) <= 2) >= 3]

    # 置换零假设：把查询随机指派给别的卡，best-other 覆盖率应与此相同 →
    # 说明「命中」只是通用词面重合，不构成针对性泄漏。
    rng = random.Random(SEED + 1)
    null_means, null_hits = [], []
    for _ in range(20):
        idx = list(range(n))
        rng.shuffle(idx)
        vals = []
        for k in range(n):
            q = content[idx[k]]
            if not q:
                vals.append(0.0)
                continue
            vals.append(max(len(q & asets[j]) / len(q)
                            for j in range(n) if j != idx[k]))
        null_means.append(statistics.mean(vals))
        null_hits.append(sum(1 for v in vals if v >= 0.5) / n)

    # 自证泄漏：eval-retrieval 的查询取自同一篇写库的题面，而卡的
    # carrier/attempts 也来自同一篇写库 → top1 可能只是「同源字段重合」。
    evaluable, self_rows = 0, []
    for c in cases:
        p = os.path.join(writeups, str(c.get("source") or ""))
        if not os.path.exists(p):
            continue
        qt = tok(query_text(p)) - tok(c.get("name", ""))
        if len(qt) < 6:
            continue
        evaluable += 1
        ans = tok(text_of(c, ("signals", "chain", "transferable")))
        full = tok(text_of(c, FULL_KEYS))
        carrier = tok(c.get("carrier", ""))
        self_rows.append({
            "answer_cov": len(qt & ans) / len(qt),
            "full_cov": len(qt & full) / len(qt),
            "carrier_cov": len(qt & carrier) / len(qt),
        })

    def med(k):
        return round(statistics.median([r[k] for r in self_rows]), 4) if self_rows else 0.0

    dangling = [c.get("source") for c in cases
                if not c.get("source") or not os.path.exists(os.path.join(writeups, c["source"]))]
    lessons = lesson_shadow_audit(engine, cases, db=db)

    warnings = []
    if hit_rate >= 0.25:
        warnings.append("卡间互命中率 %.1f%% >= 25%%：疑似互相记答案特征" % (hit_rate * 100))
    if near_dup:
        warnings.append("近重复卡 %d 对（>=80%% 且共享 >=3 个稀有词）" % len(near_dup))
    if dangling:
        warnings.append("悬空 source 指针 %d 条（%.1f%%）" % (len(dangling), 100.0 * len(dangling) / n))
    if lessons and lessons["dangling_lessons"]:
        warnings.append("lessons 悬空 %d 条（FK=%d 死影子=%d 陈旧类别孪生=%d）"
                        % (lessons["dangling_lessons"], lessons["lessons_fk_dangling"],
                           lessons["dead_case_shadows"], lessons["stale_category_twins"]))
    if med("carrier_cov") >= 0.4:
        warnings.append("自证泄漏：evaluable 查询与自身 carrier 字段中位重合 %.0f%% → "
                        "top1/top3 部分由同源字段复制支撑" % (med("carrier_cov") * 100))
    symbols = []
    for bv, i, j, shared in top_pairs[:5]:
        if j < 0:
            continue
        symbols.append({"coverage": round(bv, 3), "query": cases[i].get("name"),
                        "matched_card": cases[j].get("name"),
                        "shared_rare_tokens": shared[:10]})

    res = {
        "cases": n,
        "seed": SEED,
        "engine_tokenizer": imported,
        "content_token_df_cut": generic_cut,
        "generic_tokens": len(generic),
        "cross_hit_rate_at_50pct": round(hit_rate, 4),
        "cross_mean_best_coverage": round(statistics.mean(best), 4),
        "null_mean_best_coverage": round(statistics.mean(null_means), 4),
        "null_hit_rate_at_50pct": round(statistics.mean(null_hits), 4),
        "excess_over_null": round(statistics.mean(best) - statistics.mean(null_means), 4),
        "near_duplicate_pairs": len(near_dup),
        "dangling_sources": len(dangling),
        "dangling_rate": round(len(dangling) / n, 4),
        "dangling_examples": dangling[:5],
        "lessons_shadow": lessons,
        "self_provenance": {
            "evaluable_queries": evaluable,
            "median_answer_coverage": med("answer_cov"),
            "median_full_card_coverage": med("full_cov"),
            "median_carrier_coverage": med("carrier_cov"),
            "queries_with_full_coverage_ge_80pct": sum(1 for r in self_rows if r["full_cov"] >= 0.8),
            "queries_with_carrier_coverage_ge_50pct": sum(1 for r in self_rows if r["carrier_cov"] >= 0.5),
        },
        "top_pairs": symbols,
        "warnings": warnings,
        "status": "warn" if warnings else "ok",
    }
    return res


# ---------------------------------------------------------------- 投毒探针
def _local_eval_per_item(engine, cases, writeups):
    """[evolve-20261118-04 / rsi-123] In-process per-item retrieval eval via the
    engine's OWN knowledge.eval_retrieval(per_item=True) -- guaranteed same
    scoring code as the CLI run, zero local re-derivation, so rank_before /
    rank_after are directly comparable and any drift is caught by the
    aggregate-fidelity guard in probe_poison."""
    engine_tokenizer(engine)  # ensures the engine root is on sys.path
    import knowledge
    return knowledge.eval_retrieval(cases, writeups, per_item=True)


def _eval_query_union(engine, cases, writeups):
    """Union of the evaluable query token sets, built with the exact
    eval_retrieval filter (source writeup exists, >=6 tokens after name
    subtraction). Used for the poison-channel diagnostic: poison tokens that
    never appear in any query cannot move any score -- the causal channel is
    empty and a flat aggregate delta is structural, not evidence of
    insensitivity."""
    tok, qtext, _imp = engine_tokenizer(engine)
    out = set()
    for c in cases:
        p = os.path.join(writeups, str(c.get("source") or ""))
        if not os.path.exists(p):
            continue
        q = tok(qtext(p)) - tok(c.get("name") or "")
        if len(q) >= 6:
            out |= q
    return out


def _card_flag(case, writeups):
    if case.get("flag"):
        return case["flag"]
    path = os.path.join(writeups, str(case.get("source") or ""))
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        m = FLAG_RE.search(f.read())
    return m.group(1) if m else None


def _swap_in(mutant, live):
    bak = live + ".lab-bak"
    if os.path.exists(bak):
        raise SystemExit("[error] stale backup exists, refusing to touch live cases: %s" % bak)
    os.replace(live, bak)
    try:
        shutil.copy2(mutant, live)
    except Exception:
        os.replace(bak, live)
        raise
    return bak


def _restore(live, bak):
    if os.path.exists(live):
        os.remove(live)
    os.replace(bak, live)


def probe_poison(engine, cases_path, writeups, n_poison=5, seed=SEED, timeout=900,
                 poison_line=None):
    pline = poison_line or POISON_LINE
    live = os.path.abspath(os.path.join(engine, "skills", "knowledge", "cases.jsonl"))
    if os.path.abspath(cases_path) != live:
        raise SystemExit("[error] poison must target the engine's live cases.jsonl\n"
                         "        engine=%s\n        given =%s" % (live, os.path.abspath(cases_path)))
    cases = load_cases(live)
    rng = random.Random(seed)
    candidates = []
    skipped = 0
    for c in cases:
        if not c.get("solved"):
            continue
        if not _card_flag(c, writeups):
            skipped += 1
            continue
        candidates.append(c)
    rng.shuffle(candidates)
    chosen = candidates[:n_poison]
    if len(chosen) < n_poison:
        raise SystemExit("[error] only %d poisonable solved cards (need %d); "
                         "flag provenance missing on %d solved cards"
                         % (len(chosen), n_poison, skipped))

    before = sha256(live)
    os.makedirs(TEMP_DIR, exist_ok=True)
    mutant_path = os.path.join(TEMP_DIR, "cases.poison.jsonl")
    mutated = []
    poison_ids = {c["id"] for c in chosen}
    for c in cases:
        if c["id"] in poison_ids:
            m = dict(c)
            m["signals"] = pline + (c.get("signals") or "")
            m["poisoned"] = True
            mutated.append(m)
        else:
            mutated.append(c)
    with open(mutant_path, "w", encoding="utf-8") as f:
        for c in mutated:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    mutant_hash = sha256(mutant_path)

    print("[poison] live hash before : %s" % before)
    print("[poison] mutated cards    : %s" % ", ".join(c.get("name", "?") for c in chosen))
    print("[poison] poison line      : %s" % pline)
    print("[poison] mutant hash      : %s" % mutant_hash)

    # [rsi-123] per-item ranking of the CLEAN kb, computed in-process through
    # the engine's own eval_retrieval BEFORE any swap touches the live file.
    pre = _local_eval_per_item(engine, cases, writeups)
    pre_rb = {row["id"]: row["signal_rank"] for row in pre["per_item"]}
    _names = {row["id"]: row["name"] for row in pre["per_item"]}
    # self-consistency: per_item must reconstruct the printed aggregate
    _n = len(pre_rb)
    _agg_from_per = {
        "top1": round(sum(1 for r in pre_rb.values() if r == 1) / _n, 4) if _n else 0.0,
        "top3": round(sum(1 for r in pre_rb.values() if r <= 3) / _n, 4) if _n else 0.0,
    }
    for _k in ("top1", "top3"):
        if _agg_from_per[_k] != pre["signal"][_k]:
            raise SystemExit("[error] per_item rows fail to reconstruct the aggregate "
                             "(%s: %s vs %s) -- per_item plumbing broken"
                             % (_k, _agg_from_per[_k], pre["signal"][_k]))

    bak = None
    try:
        bak = _swap_in(mutant_path, live)
        print("[poison] live file swapped -> running engine eval-retrieval (timeout %ds)" % timeout)
        run = run_engine_eval(engine, writeups, timeout=timeout)
    finally:
        if bak and os.path.exists(bak):
            _restore(live, bak)
        after = sha256(live) if os.path.exists(live) else "<missing>"
        print("[poison] live hash after  : %s" % after)
        if after != before:
            raise SystemExit("[FATAL] cases.jsonl restore mismatch! backup kept alongside: %s"
                             % (live + ".lab-bak"))

    baseline, _bp = load_baseline(engine)
    if not baseline:
        raise SystemExit("[error] no eval-baseline.json -- cannot compare poison effect")
    sig = run["signal"]
    d1 = round((sig["top1"] - baseline["top1"]) * 100, 1)
    d3 = round((sig["top3"] - baseline["top3"]) * 100, 1)
    resolution = round(100.0 / max(1, run.get("evaluable") or 1), 1)

    # [rsi-123] per-item ranking of the MUTATED kb (in-process; the live file
    # is already restored). Fidelity guard: the in-process aggregate must match
    # the engine CLI aggregate measured under the real swap -- divergence
    # means the local mirror drifted and per-item numbers are void. Compare at
    # the CLI's PRINTED precision (top1/top3 one decimal as a percent, mrr
    # three decimals): run_engine_eval parses "57.9%" back to 0.579 while the
    # in-process value keeps four decimals (0.5789) -- that gap is print
    # precision, not scoring drift.
    post = _local_eval_per_item(engine, mutated, writeups)
    for _k in ("top1", "top3"):
        if round(post["signal"][_k] * 100, 1) != round(sig[_k] * 100, 1):
            raise SystemExit("[error] in-process eval_retrieval drifted from the CLI run "
                             "(%s: %s vs %s) -- per-item comparison void"
                             % (_k, post["signal"][_k], sig[_k]))
    if round(post["signal"]["mrr"], 3) != round(sig["mrr"], 3):
        raise SystemExit("[error] in-process eval_retrieval drifted from the CLI run "
                         "(mrr: %s vs %s) -- per-item comparison void"
                         % (post["signal"]["mrr"], sig["mrr"]))
    ra = {row["id"]: row["signal_rank"] for row in post["per_item"]}
    shifted = sorted(
        ({"id": k, "name": _names[k], "before": pre_rb[k], "after": ra[k]}
         for k in pre_rb if pre_rb[k] != ra.get(k)),
        key=lambda s: -abs(s["after"] - s["before"]))
    poisoned_items = []
    for c in chosen:
        cid = c["id"]
        poisoned_items.append({"id": cid, "name": c.get("name"),
                               "evaluable": cid in pre_rb,
                               "rank_before": pre_rb.get(cid),
                               "rank_after": ra.get(cid)})
    poison_evaluable = sum(1 for it in poisoned_items if it["evaluable"])
    tok, _qt, _imp = engine_tokenizer(engine)
    channel = tok(pline) & _eval_query_union(engine, cases, writeups)

    # Three-state verdict [rsi-123]: the legacy binary alarm fired on every
    # run for 8+ chain entries because a flat aggregate delta is structural
    # (single-item resolution vs threshold, poison cards outside the
    # evaluable set, weak/empty payload channel) -- it could never attribute
    # anything. detected = poison actually moved rankings or the aggregate
    # past the threshold; alarm-ineffective-payload = the probe's own payload
    # has no channel and no poisoned card is evaluable (design debt, honest);
    # alarm-insensitive = a channel/targets exist but nothing moved (real
    # insensitivity finding).
    if d1 <= -2.0 or d3 <= -2.0 or shifted:
        state = "detected"
    elif not channel and poison_evaluable == 0:
        state = "alarm-ineffective-payload"
    else:
        state = "alarm-insensitive"
    alarm = state != "detected"

    res = {
        "seed": seed,
        "poisoned": [{"id": c["id"], "name": c.get("name"), "category": c.get("category"),
                      "source": c.get("source")} for c in chosen],
        "skipped_solved_without_flag": skipped,
        "poison_line": pline,
        "cases_hash_before": before,
        "cases_hash_after": sha256(live),
        "restored": sha256(live) == before,
        "mutant_sha256": mutant_hash,
        "evaluable": run.get("evaluable"),
        "baseline": {"top1": baseline["top1"], "top3": baseline["top3"],
                     "mrr": baseline["mrr"], "date": baseline.get("date")},
        "poisoned_run": sig,
        "delta_pp": {"top1": d1, "top3": d3},
        "resolution_pp": resolution,
        "alarm": alarm,
        "state": state,
        "attribution": {
            "poison_tokens": sorted(tok(pline)),
            "channel_tokens": sorted(channel),
            "poisoned_evaluable": "%d/%d" % (poison_evaluable, len(chosen)),
            "shifted_items": len(shifted),
            "max_abs_shift": max((abs(s["after"] - s["before"]) for s in shifted),
                                 default=0),
        },
        "poisoned_items": poisoned_items,
        "per_item_shifted": shifted,
        "verdict": ("评测检出污染：top1%+.1fpp top3%+.1fpp，排名位移 %d 项"
                     % (d1, d3, len(shifted)))
                    if not alarm else
                    ("报警[%s]：注入 %d 张毒卡后 top1%+.1fpp / top3%+.1fpp 零动（单题分辨率 %.1fpp）；"
                     "归因：毒卡可评估 %s，通道 token %s，排名位移 %d 项"
                     % (state, len(chosen), d1, d3, resolution,
                        "%d/%d" % (poison_evaluable, len(chosen)),
                        "、".join(sorted(channel)) or "无",
                        len(shifted))),
        "engine_raw_tail": run.get("raw_tail"),
    }
    return res


# ---------------------------------------------------------------- 留出集
def probe_holdout(writeups, n=20, cases_path=None, seed=SEED):
    files = []
    for root, dirs, names in os.walk(writeups):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".temp")]
        for fn in sorted(names):
            if fn.endswith(".md") and fn.lower() != "readme.md":
                files.append(os.path.relpath(os.path.join(root, fn), writeups)
                             .replace("\\", "/"))
    files.sort()
    if len(files) < n:
        raise SystemExit("[error] only %d writeups under %s (need %d)" % (len(files), writeups, n))
    rng = random.Random(seed)
    picked = sorted(rng.sample(files, n))
    items = []
    for rel in picked:
        stem = os.path.splitext(os.path.basename(rel))[0]
        items.append({"name": re.sub(r"^\d{3,4}[-_ ]", "", stem), "path": rel,
                      "contaminated": False})

    known = set()
    if cases_path and os.path.exists(cases_path):
        known = {str(c.get("source") or "") for c in load_cases(cases_path)}
    for it in items:
        it["contaminated"] = it["path"] in known
    bad = [it for it in items if it["contaminated"]]

    payload = {
        "seed": seed,
        "n": n,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "writeups": rel_to_repo(writeups),
        "cases_checked": rel_to_repo(cases_path) if cases_path else None,
        "contaminated": len(bad),
        "items": items,
    }
    with open(HOLDOUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


# ---------------------------------------------------------------- 汇总报告
def probe_report(engine, writeups):
    run = run_engine_eval(engine, writeups)
    baseline, bpath = load_baseline(engine)
    sig = run["signal"]
    payload = {
        "engine": rel_to_repo(engine),
        "writeups": rel_to_repo(writeups),
        "evaluable": run.get("evaluable"),
        "cases": run.get("cases"),
        "current": sig,
        "recency": run.get("recency"),
        "gate_result": run.get("result"),
        "baseline_path": rel_to_repo(bpath) if os.path.exists(bpath) else None,
        "baseline": baseline,
    }
    if baseline:
        payload["drift_pp"] = {
            "top1": round((sig["top1"] - baseline["top1"]) * 100, 1),
            "top3": round((sig["top3"] - baseline["top3"]) * 100, 1),
            "mrr": round(sig["mrr"] - baseline["mrr"], 3),
        }
        payload["pass"] = run.get("returncode") == 0
    else:
        payload["drift_pp"] = None
        payload["pass"] = None
    return payload


# ---------------------------------------------------------------- 打印
def print_leak(res):
    sp = res["self_provenance"]
    print("[leak] cases=%d engine_tokenizer=%s generic_tokens=%d"
          % (res["cases"], res["engine_tokenizer"], res["generic_tokens"]))
    print("[leak] 卡间互命中率(>=50%% 内容词) = %.1f%%  (置换零假设 %.1f%%)"
          % (res["cross_hit_rate_at_50pct"] * 100, res["null_hit_rate_at_50pct"] * 100))
    print("[leak] 平均最佳覆盖 observed=%.3f null=%.3f excess=%+.3f"
          % (res["cross_mean_best_coverage"], res["null_mean_best_coverage"],
             res["excess_over_null"]))
    print("[leak] 近重复卡=%d  悬空 source 指针=%d (%.1f%%)"
          % (res["near_duplicate_pairs"], res["dangling_sources"], res["dangling_rate"] * 100))
    if res.get("lessons_shadow"):
        l = res["lessons_shadow"]
        print("[leak] lessons 影子: 活=%d FK悬空=%d 死影子=%d 陈旧类别孪生=%d"
              % (l["live_case_shadows"], l["lessons_fk_dangling"],
                 l["dead_case_shadows"], l["stale_category_twins"]))
    else:
        print("[leak] lessons 影子: (库不可读，维度缺省)")
    print("[leak] 自证泄漏: evaluable=%d 自身答案覆盖中位=%.2f 整卡覆盖=%.2f carrier 覆盖=%.2f"
          % (sp["evaluable_queries"], sp["median_answer_coverage"],
             sp["median_full_card_coverage"], sp["median_carrier_coverage"]))
    for w in res["warnings"]:
        print("[leak] WARN " + w)
    print("[leak] status=%s" % res["status"])


def print_poison(res):
    print("[poison] 注入 %d 张毒卡，跳过无 flag 的已解卡 %d 张"
          % (len(res["poisoned"]), res["skipped_solved_without_flag"]))
    print("[poison] 基线 top1=%.1f%% top3=%.1f%% | 投毒后 top1=%.1f%% top3=%.1f%%"
          % (res["baseline"]["top1"] * 100, res["baseline"]["top3"] * 100,
             res["poisoned_run"]["top1"] * 100, res["poisoned_run"]["top3"] * 100))
    print("[poison] Δ top1=%+.1fpp Δ top3=%+.1fpp（阈值 -2pp，单题分辨率 %.1fpp）"
          % (res["delta_pp"]["top1"], res["delta_pp"]["top3"], res["resolution_pp"]))
    att = res.get("attribution")
    if att:
        print("[poison] per-item：毒卡可评估 %s，通道 token [%s]，排名位移 %d 项（max %d 位）"
              % (att["poisoned_evaluable"], ",".join(att["channel_tokens"]) or "无",
                 att["shifted_items"], att["max_abs_shift"]))
        for s in res.get("per_item_shifted", [])[:6]:
            print("          - %s: #%d -> #%d" % (s["name"][:40], s["before"], s["after"]))
    print("[poison] hash 恢复一致=%s (%s)" % (res["restored"], res["cases_hash_after"][:12]))
    print("[poison] %s" % ("ALARM " + res["verdict"] if res["alarm"] else res["verdict"]))


def print_holdout(p):
    print("[holdout] seed=%d n=%d writeups=%s" % (p["seed"], p["n"], p["writeups"]))
    print("[holdout] 已污染：留出题已在库中 %d/%d" % (p["contaminated"], p["n"]))
    if p["contaminated"]:
        print("[holdout] WARN 卫生债：rebuild-knowledge 全量入库，缺留出隔离；"
              "清单见 lab/holdout.json")
        for it in p["items"][:5]:
            print("          - %s (%s)" % (it["name"], it["path"]))
    else:
        print("[holdout] 干净：留出题均未入 cases.jsonl")


def print_report(p):
    cur, base, drift = p["current"], p["baseline"], p["drift_pp"]
    print("[report] 当前: top1=%.1f%% top3=%.1f%% mrr=%.3f (evaluable=%d)"
          % (cur["top1"] * 100, cur["top3"] * 100, cur["mrr"], p["evaluable"] or 0))
    if base:
        print("[report] 基线: top1=%.1f%% top3=%.1f%% mrr=%.3f (%s)"
              % (base["top1"] * 100, base["top3"] * 100, base["mrr"], base.get("date", "")))
        print("[report] 漂移: top1%+.1fpp top3%+.1fpp mrr%+.3f → %s"
              % (drift["top1"], drift["top3"], drift["mrr"],
                 "PASS" if p["pass"] else "FAIL（超出 5pp 容忍）"))
    else:
        print("[report] 基线: (缺失) → 无法判定漂移")


def load_writeups_default(engine):
    stats = os.path.join(engine, "skills", "knowledge", "stats.json")
    if os.path.exists(stats):
        try:
            with open(stats, "r", encoding="utf-8") as f:
                w = json.load(f).get("writeups_dir")
            if w and os.path.isdir(w):
                return w
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(prog="probes", description="CTFPilot 评测方法探针")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--engine", default=ENGINE_DEFAULT)
        p.add_argument("--cases", default=None)
        p.add_argument("--writeups", default=WRITEUPS_DEFAULT)

    p = sub.add_parser("leak", help="卡间互命中 + 悬空指针 + 自证泄漏")
    common(p)
    p.add_argument("--db", default=None,
                   help="覆盖 lessons 影子审计库路径（默认 engine/memory/trajectories.sqlite）")

    p = sub.add_parser("poison", help="注入毒卡跑 eval-retrieval 看敏感性")
    common(p)
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--poison-line", default=None,
                   help="override the poison payload sentence (positive-control "
                        "experiments: a channel-bearing line proves the per-item "
                        "metric can actually detect rank shifts)")

    p = sub.add_parser("holdout-split", help="生成评测专用留出清单")
    p.add_argument("--cases", default=None)
    p.add_argument("--writeups", default=WRITEUPS_DEFAULT)
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--seed", type=int, default=SEED)

    p = sub.add_parser("report", help="跑 eval-retrieval 并与基线对比")
    p.add_argument("--engine", default=ENGINE_DEFAULT)
    p.add_argument("--writeups", default=None)

    args = ap.parse_args()
    engine = os.path.abspath(getattr(args, "engine", ENGINE_DEFAULT) or ENGINE_DEFAULT)
    cases_path = os.path.abspath(args.cases) if getattr(args, "cases", None) else \
        os.path.join(engine, "skills", "knowledge", "cases.jsonl")
    writeups = os.path.abspath(getattr(args, "writeups", None) or
                               load_writeups_default(engine) or WRITEUPS_DEFAULT)  # [evolve-20261117-08] stats.json writeups_dir 兜底链
    if not os.path.isdir(writeups):
        raise SystemExit("[error] writeups dir not found: %s" % writeups)

    t0 = time.time()
    if args.cmd == "leak":
        db_override = os.path.abspath(args.db) if getattr(args, "db", None) else None
        res = probe_leak(load_cases(cases_path), writeups, engine, db=db_override)
        res["elapsed_s"] = round(time.time() - t0, 1)
        res["cases_path"] = rel_to_repo(cases_path)
        update_report("leak", res)
        print_leak(res)
        lessons_dangling = (res.get("lessons_shadow") or {}).get("dangling_lessons", 0)
        return 2 if (res["dangling_sources"] or lessons_dangling) else 0

    if args.cmd == "poison":
        res = probe_poison(engine, cases_path, writeups, n_poison=args.n,
                           seed=args.seed, timeout=args.timeout,
                           poison_line=getattr(args, "poison_line", None))
        res["elapsed_s"] = round(time.time() - t0, 1)
        update_report("poison", res)
        print_poison(res)
        return 3 if res["alarm"] else 0

    if args.cmd == "holdout-split":
        payload = probe_holdout(writeups, n=args.n, cases_path=cases_path, seed=args.seed)
        payload["status"] = "contaminated" if payload["contaminated"] else "clean"
        update_report("holdout", payload)
        print_holdout(payload)
        return 0

    if args.cmd == "report":
        wu = args.writeups or load_writeups_default(engine) or writeups
        res = probe_report(engine, os.path.abspath(wu))
        res["elapsed_s"] = round(time.time() - t0, 1)
        update_report("engine", res)
        print_report(res)
        return 0 if res["pass"] in (True, None) else 1


if __name__ == "__main__":
    sys.exit(main())