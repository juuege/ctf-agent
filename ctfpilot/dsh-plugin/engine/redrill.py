# -*- coding: utf-8 -*-
"""redrill: k-fold 交叉重刷训练协议的引擎侧实现。

设计溯源（每件可查）:
  - k-fold 记忆隔离: ExpeL (arXiv:2308.10144, AAAI-24) S5.1 "four-fold
    validation"、AWM (arXiv:2409.07429) 训练子集学习/持出任务验证
  - 白名单物理隔离 + 私有 flag 布尔裁判 + flag_format 脱壳:
    NYU CTF Bench (arXiv:2406.05590; nyuctf_agents environment.py/tools.py)
  - 池冻结(按时间线划合法性): SWE-rebench (arXiv:2505.20411) 时间戳污染标记
  - 判分在 agent 不可及处重做(证据记账+溯源审计): SWE-Bench Pro V2
    (arXiv:2610.24206) hardened protocol
  - 干净环境重判分泛化为本文件的答案串溯源扫描

状态文件: <memory>/redrill-state.json —— 引擎侧私有，agent 永远拿不到路径。
所有输出面向 agent 的子命令(begin/verify/record/...)绝不输出: 真实题名、
真实 flag、引擎目录、台账路径、writeups 路径。

运维日志（2026-11-26 用户令「交付可运维」）:
  <memory>/redrill-ops.log —— JSONL，每个动作一行 {ts, action, phase,
  pid, detail}。phase=start|ok|error，detail 含脱敏参数与耗时。flag 永不
  落盘（只记 sha256 前 8 位 + 长度 + 格式）。2MB 轮转保留一代 .1。
  入口统一包装在 ctfpilot.py::do_redrill（所有调用方的唯一收口）。
  查看: redrill diag（环境/state/沙箱/日志尾部/最近错误一站式快照）。
"""
import argparse
import hashlib
import json
import os
import random
import re
import shutil
import sys
import time

MEMORY_DIR = None


def _ws_root():
    """工作空间根（ctfpilot.py 的上级）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _memory_dir():
    global MEMORY_DIR
    if MEMORY_DIR is None:
        db_env = os.environ.get("CTFPILOT_DB")
        base = os.path.dirname(db_env) if db_env else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "memory")
        MEMORY_DIR = base
    return MEMORY_DIR


def _state_path():
    return os.path.join(_memory_dir(), "redrill-state.json")


def _sandbox_root():
    return os.path.join(_memory_dir(), "redrill-sandbox")


def log(msg):
    print(msg, flush=True)


# ----------------------------------------------------------------------------
# ops log: 运维操作日志（2026-11-26 用户令：交付可运维，操作留痕可溯源）
# ----------------------------------------------------------------------------

_OPS_LOG_MAX_BYTES = 2 * 1024 * 1024  # 2MB 轮转，保留一代 .1


def _ops_log_path():
    return os.path.join(_memory_dir(), "redrill-ops.log")


def _sha8(s):
    return hashlib.sha256((s or "").encode("utf-8", "replace")).hexdigest()[:8]


def ops_snapshot_args(args):
    """把 argparse 参数压成可记日志的摘要。脱敏：flag 只记 hash8/长度/
    格式（真值永不落盘）；sessions 只记 basename；空值不占行。"""
    d = {}
    for k, v in sorted(vars(args).items()):
        if k in ("flag", "note"):
            continue
        if v is None or v == "" or v is False or v == []:
            continue
        if k == "sessions":
            d["sessions"] = [os.path.basename(str(x)) for x in v]
            continue
        d[k] = v
    flag = getattr(args, "flag", "") or ""
    if flag:
        d["flag_sha8"] = _sha8(flag)
        d["flag_len"] = len(flag)
        d["flag_fmt"] = flag_format_of(flag)
    return d


def ops_log(action, phase, detail=None):
    """追加一条运维日志（JSONL）。phase: start|ok|error。
    设计约束：写日志失败绝不打断主流程（日志自身不能成为故障源）；
    超 2MB 轮转为 .1 保留一代（防无限增长）。"""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "action": action, "phase": phase, "pid": os.getpid(),
    }
    if detail:
        entry["detail"] = detail
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    p = _ops_log_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            if os.path.getsize(p) > _OPS_LOG_MAX_BYTES:
                os.replace(p, p + ".1")
        except OSError:
            pass
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
    except OSError as e:
        print("[redrill][warn] ops log write failed: %s" % e,
              file=sys.stderr, flush=True)


def _ops_tail(n=20, phases=None):
    """读 ops 日志尾部（最多回读 64KB，适配 status 30s 轮询）。
    phases 可过滤（如只看 error）。解析失败的行直接跳过。"""
    p = _ops_log_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            back = min(size, 64 * 1024)
            f.seek(size - back)
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue  # 尾部截断的半个 JSON 行
        if phases and e.get("phase") not in phases:
            continue
        out.append(e)
    return out[-n:]


# ----------------------------------------------------------------------------
# state helpers
# ----------------------------------------------------------------------------

def load_state():
    p = _state_path()
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        log("[redrill][error] state file unreadable: %s" % e)
        return None


def save_state(st):
    p = _state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def active_state():
    """返回有效 campaign 状态（未 finalize）或 None。"""
    st = load_state()
    if st and st.get("status") not in ("done", "abandoned"):
        return st
    return None


def normalize_flag(s):
    """归一化: 去空白; 保留 X{Y} 结构 (前后缀.lower(), 内容原样);
    无包装时整体 strip+lower (md5/hex 型)。内容保持大小写是因为部分赛
    区 flag 区分大小写、而包装前缀不区分。"""
    s = (s or "").strip()
    m = re.match(r"^([A-Za-z0-9_]+)\{(.*)\}$", s, re.S)
    if m:
        return "%s{%s}" % (m.group(1).lower(), m.group(2))
    return s.lower()


def flag_format_of(flag):
    m = re.match(r"^([A-Za-z0-9_]+)\{.*\}$", (flag or "").strip(), re.S)
    if m:
        return "%s{...}" % m.group(1)
    return "(unwrapped, %d chars)" % len(flag or "")


# ----------------------------------------------------------------------------
# plan: 同flag分组 -> 原件预检 -> 分折 -> 盲副本
# ----------------------------------------------------------------------------

_SKIP_COPY = ("_ctfpilot_model_result.json", "_ctfpilot_result.json",
              "redrill_meta.json")


def _candidate_dirs(name, dir_path):
    """台账 dir_path 可能是绝对路径/相对路径/裸题名，逐层兜底。"""
    ws = _ws_root()
    cands = []
    if dir_path:
        if os.path.isabs(dir_path):
            cands.append(dir_path)
        else:
            cands.append(os.path.join(ws, dir_path))
            cands.append(os.path.join(ws, "ctf-feed", dir_path))
            cands.append(os.path.join(ws, "CTFPilot-v0.1", "challs_incoming", dir_path))
    cands.append(os.path.join(ws, "ctf-feed", name))
    cands.append(os.path.join(ws, "CTFPilot-v0.1", "challs_incoming", name))
    seen, out = set(), []
    for c in cands:
        c2 = os.path.normpath(c)
        if c2 not in seen and os.path.isdir(c2):
            seen.add(c2)
            out.append(c2)
    return out


def _sanitize_blind_copy(src, dst, flag):
    """盲副本保护: 复制题目文件到 slot 目录, 但
    (a) 跳过引擎产物 (_ctfpilot_*);
    (b) 二进制/归档中命中真 flag 的文件直接剔除 (zip 里flag 无法打码);
    (c) 文本中命中真 flag 的行打码为 [REDACTED]。
    目录名只用 slot id, 不留题名。"""
    os.makedirs(dst, exist_ok=True)
    flag_norm = normalize_flag(flag)
    redacted_files, dropped_files = [], []
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
        rel = os.path.relpath(root, src)
        for fn in files:
            if fn in _SKIP_COPY:
                continue
            s = os.path.join(root, fn)
            d = os.path.join(dst, rel if rel != "." else "", fn)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            with open(s, "rb") as f:
                data = f.read()
            if flag and (flag.encode() in data):
                btxt = None
                try:
                    btxt = data.decode("utf-8")
                except UnicodeDecodeError:
                    pass
                if btxt is not None and flag_norm and flag_norm in btxt:
                    # 文本: 打码
                    btxt = btxt.replace(flag, "[REDACTED]")
                    if flag_norm and flag_norm != flag and not re.match(
                            r"^[a-z0-9]+\{.*\}$", flag, re.S):
                        btxt = re.sub(re.escape(flag_norm), "[REDACTED]",
                                      btxt, flags=re.I)
                    with open(d, "w", encoding="utf-8", newline="") as f:
                        f.write(btxt)
                    redacted_files.append(os.path.relpath(d, dst))
                    continue
                # 二进制命中(或不可解码文本): 保守整文件剔除
                dropped_files.append(os.path.relpath(d, dst))
                continue
            shutil.copy2(s, d)
    return redacted_files, dropped_files


def do_plan(store, args):
    """构造 campaign。从台账 solved 题池取, 同flag/近名分组, 原件预检,
    固定种子分折, 复制盲副本。"""
    if active_state():
        log("[redrill][error] campaign already active (finalize/abandon first)")
        return 2
    rows = store.conn.execute(
        "SELECT name, category, dir_path, flag FROM challenges "
        "WHERE solved=1 AND name NOT LIKE 'writeup:%%' AND flag IS NOT NULL "
        "AND flag != ''").fetchall()
    # 题名去重取最新 + 同 flag 分组
    by_name, flag_groups = {}, {}
    for name, cat, dirp, flag in rows:
        if name not in by_name:
            by_name[name] = (cat, dirp, flag)
    for name, (cat, dirp, flag) in by_name.items():
        flag_groups.setdefault(normalize_flag(flag), []).append(name)
    # 原件预检
    missing, groups, meta = [], [], {}
    for fl, names in flag_groups.items():
        found = [(n, _candidate_dirs(n, by_name[n][1])[0])
                 for n in names if _candidate_dirs(n, by_name[n][1])]
        mising_t = [n for n in names if not _candidate_dirs(n, by_name[n][1])]
        missing.extend(mising_t)
        if found:
            groups.append(found)
            for n, d in found:
                meta[n] = {"category": by_name[n][0],
                           "nflag": fl,
                           "dir": d, "flag": by_name[n][2]}
    rng = random.Random(args.seed)
    rng.shuffle(groups)
    folds, cur = [], []
    for g in groups:
        if len(cur) >= args.fold_size and len(folds) < args.folds - 1:
            folds.append(cur)
            cur = []
        cur.append(g)
    if cur:
        folds.append(cur)
    if not folds or not any(folds):
        log("[redrill][error] no usable challenge originals "
            "(pool=%d, missing=%d)" % (len(by_name), len(missing)))
        return 2
    if getattr(args, "dry_run", False):
        # 干演：不落状态、不建沙箱、不复制盲副本 —— 预审分折/分组/
        # missing 用，绝不进入 active（不解锁 ctfpilot 工具封锁）。
        idx = 0
        log("[redrill] dry-run: folds=%d groups=%d pool=%d missing=%d "
            "(nothing written)" % (len(folds), len(groups), len(by_name),
                                   len(set(missing))))
        for f in folds:
            parts = []
            for g in f:
                idx += 1
                parts.append("S%02d:%s[%s]" % (idx, g[0][0][:34],
                                                 flag_format_of(meta[g[0][0]]["flag"])))
            log("  fold %d/%d: %s" % (folds.index(f) + 1, len(folds),
                                      " ".join(parts)))
        if missing:
            log("  missing (excluded): %s" % ", ".join(sorted(set(missing))[:12]))
        return 0
    max_lesson_id = store.conn.execute("SELECT MAX(id) FROM lessons").fetchone()[0] or 0
    cid = "c%s" % time.strftime("%Y%m%d-%H%M%S")
    sandbox = os.path.join(args.sandbox or _sandbox_root(), cid)
    st = {
        "version": 1,
        "campaign_id": cid,
        "created": time.time(),
        "seed": args.seed,
        "status": "active",
        "fold_count": len(folds),
        "active_fold": 0,
        "pool_freeze_lesson_id": max_lesson_id,
        "registry_root": args.registry or "",
        "missing": sorted(set(missing)),
        "folds": [],
        "history": [],
    }
    idx = 0
    for fi, glist in enumerate(folds, 1):
        fold = {"index": fi, "status": "pending", "groups": []}
        for g in glist:
            slot = "S%02d" % (idx + 1)
            idx += 1
            names = [n for n, _ in g]
            prime = names[0]
            blind_dir = os.path.join(sandbox, slot)
            red_f, drop_f = _sanitize_blind_copy(
                meta[prime]["dir"], blind_dir, meta[prime]["flag"])
            fold["groups"].append({
                "slot": slot,
                "names": names,
                "category": meta[prime]["category"],
                "prime": prime,
                "flag": meta[prime]["flag"],
                "format": flag_format_of(meta[prime]["flag"]),
                "dir_source": meta[prime]["dir"],
                "blind_dir": blind_dir,
                "redacted": red_f,
                "dropped": drop_f,
                "state": "pending",
                "via": None,
                "dispatch_ts": None,
                "first_match_ts": None,
                "fast_solved": False,
                "hint_level": 0,
                "note": "",
            })
        st["folds"].append(fold)
    save_state(st)
    # agent 视角的清单（无题名/无 flag/无源路径）
    total = sum(len(f["groups"]) for f in st["folds"])
    log("[redrill] campaign %s planned: folds=%d slots=%d missing=%d" %
        (cid, len(folds), total, len(missing)))
    for f in st["folds"]:
        log("  fold %d/%d: %s" %
            (f["index"], len(st["folds"]),
             " ".join(g["slot"] + ":" + g["format"] for g in f["groups"])))
    return 0


# ----------------------------------------------------------------------------
# agent-facing actions
# ----------------------------------------------------------------------------

def _find_group(st, slot):
    for f in st["folds"]:
        for g in f["groups"]:
            if g["slot"] == (slot or "").strip():
                return f, g
    return None, None


def _check_flags(st):
    fl = st.setdefault("flags", {})
    msgs = []
    if fl.get("abort"):
        msgs.append("USER-ABORT: stop immediately, no further actions.")
    if fl.get("pause"):
        msgs.append("USER-PAUSE: finish current slot, then wait.")
    return msgs


def do_begin(store, args):
    st = active_state()
    if not st:
        log("[redrill][error] no active campaign")
        return 2
    f, g = _find_group(st, args.slot)
    if not f:
        log("[redrill][error] unknown slot")
        return 2
    if f["index"] != st["active_fold"] + 1:
        log("[redrill][error] slot belongs to fold %d (active=%d)" %
            (f["index"], st["active_fold"] + 1))
        return 2
    if g["state"] in ("verified", "failed", "missing", "recorded"):
        log("[redrill] slot %s already terminal: %s" % (g["slot"], g["state"]))
        return 0
    if not g["dispatch_ts"]:
        g["dispatch_ts"] = time.time()
    g["state"] = "running"
    save_state(st)
    log("[redrill] slot=%s state=running" % g["slot"])
    log("  blind_dir=<slot-local relative path only; see sandbox listing>")
    log("  category=%s  flag_format=%s" % (g["category"], g["format"]))
    log("  budget=8 tool calls / 2 attack routes per slot (protocol)")
    for m in _check_flags(st):
        log("  [control] %s" % m)
    return 0


def do_verify(store, args):
    st = active_state()
    if not st:
        log("[redrill][error] no active campaign")
        return 2
    f, g = _find_group(st, args.slot)
    if not f:
        log("[redrill][error] unknown slot")
        return 2
    cand = normalize_flag(args.flag)
    ok = cand == normalize_flag(g["flag"])
    if ok:
        g["state"] = "verified"
        if not g["first_match_ts"]:
            g["first_match_ts"] = time.time()
        save_state(st)
        log("[redrill] slot=%s result=MATCH (engine-verified, evidence "
            "recorded; now run: record)" % g["slot"])
    else:
        log("[redrill] slot=%s result=MISMATCH (format hint: %s)" %
            (g["slot"], g["format"]))
    return 0 if ok else 1


def do_fast_try(store, args):
    """引擎快通道在盲副本上重演 (production fast path)。命中判定:
    do_solve 以 name=slot 建行, fast 命中会写 flag 字段 —— 引擎查自己的
    台账即知，不依赖 agent 报告。命中时自动 engine-verify。"""
    import ctfpilot as _cp
    st = active_state()
    if not st:
        log("[redrill][error] no active campaign")
        return 2
    f, g = _find_group(st, args.slot)
    if not f:
        log("[redrill][error] unknown slot")
        return 2
    ns = _cp.argparse.Namespace(
        dir=g["blind_dir"], mode="fast", brain="mock", name=g["slot"],
        flag_regex=None, write_trace=False, benchmark=True, json=False,
        allow_replay=True)
    try:
        _cp.do_solve(ns, _cp.load_config())
    except SystemExit as e:
        if e.code not in (0, 1):
            raise
    row = store.conn.execute(
        "SELECT flag FROM challenges WHERE name=? ORDER BY id DESC LIMIT 1",
        (g["slot"],)).fetchone()
    hit = bool(row and row[0])
    if hit:
        g["fast_solved"] = True
        g["state"] = "verified"
        if not g["first_match_ts"]:
            g["first_match_ts"] = time.time()
        save_state(st)
        log("[redrill] slot=%s fast path=BINGO (engine-verified via its own "
            "detectors; evidence recorded). record --via fast" % g["slot"])
        return 0
    log("[redrill] slot=%s fast path=MISS (detectors found nothing; "
        "continue manually; verify your candidate flag, then record "
            "--via manual)" % g["slot"])
    return 1


def do_hint(store, args):
    """分级提示（引擎侧可控泄漏, 每次 hint 记入 slot 历史供审计）。"""
    st = active_state()
    if not st:
        log("[redrill][error] no active campaign")
        return 2
    f, g = _find_group(st, args.slot)
    if not g:
        log("[redrill][error] unknown slot")
        return 2
    lvl = min(3, max(1, args.level or (g["hint_level"] + 1)))
    g["hint_level"] = lvl
    save_state(st)
    if lvl == 1:
        log("[redrill] hint L1: category=%s —— 先做文件类型/工具链体检, "
            "按题型路由选工具" % g["category"])
    elif lvl == 2:
        row = store.conn.execute(
            "SELECT technique FROM lessons l JOIN challenges c ON "
            "c.id=l.challenge_id WHERE c.name=? ORDER BY l.id DESC LIMIT 1",
            ("writeup:" + g["names"][0],)).fetchone()
        if row:
            log("[redrill] hint L2: 历史解法的技术关键词是 '%s' "
                "（仅技术名, 无步骤/无 payload）" % row[0][:60])
        else:
            log("[redrill] hint L2: 无历史技术卡, 考虑从题目元数据与文件头 "
                "签名入手")
    else:
        log("[redrill] hint L3: 目标格式 %s, 内容长度 %s 字符; 逐段验证你在"
            "中间产物中看到的候选串" % (g["format"], len(g["flag"])))
    return 0


def do_record(store, args):
    """守门记分: fast/manual 必须有引擎侧 verify 证据, 否则拒绝。"""
    import arena as A
    st = active_state()
    if not st:
        log("[redrill][error] no active campaign")
        return 2
    f, g = _find_group(st, args.slot)
    if not f:
        log("[redrill][error] unknown slot")
        return 2
    via = args.via
    if via in ("fast", "manual"):
        if g["state"] != "verified":
            log("[redrill][REJECTED] slot=%s cannot record %s without an "
                "engine verify MATCH (current state=%s). Run verify--flag "
                "first; fabricated solves are not creditable." %
                (g["slot"], via, g["state"]))
            return 2
        if via == "fast" and not g["fast_solved"]:
            log("[redrill][REJECTED] --via fast requires the engine's own "
                "fast-path BINGO on this slot (none recorded).")
            return 2
    elif via not in ("fail", "missing"):
        log("[redrill][error] --via must be fast|manual|fail|missing")
        return 2
    g["state"] = "recorded"
    g["via"] = via
    g["note"] = args.note or ""
    susp = _suspicious(g)
    root = st.get("registry_root") or None
    for nm in g["names"]:
        A.register(nm, g["category"], "redrill/k-fold", tier="normal",
                   root=root)
        A.record_result(nm, g["category"], solved=(via in ("fast", "manual")),
                        tier="normal",
                        fast_solved=(via == "fast"),
                        time_s=(g["first_match_ts"] - g["dispatch_ts"])
                        if (g["first_match_ts"] and g["dispatch_ts"]) else None,
                        root=root)
    if via == "missing":
        g["state"] = "missing"
    save_state(st)
    log("[redrill] slot=%s recorded via=%s (%s)%s" %
        (g["slot"], via, g["names"][0] if root is None else "arena[k-fold]",
         " [SUSPICIOUS]" if susp else ""))
    return 0


def _suspicious(g):
    """机械判据: 分发到 match 异常短(<=40s)却非 fast; 或从未 begin 却
    verified (时间戳缺失)。供 audit 与 report 引用。"""
    if not g.get("first_match_ts"):
        return True
    if not g.get("dispatch_ts"):
        return True
    dt = g["first_match_ts"] - g["dispatch_ts"]
    return (g.get("via") != "fast") and dt <= 40


def do_fold_done(store, args):
    st = active_state()
    if not st:
        log("[redrill][error] no active campaign")
        return 2
    f = st["folds"][st["active_fold"]]
    pending = [g for g in f["groups"] if g["state"] not in
               ("recorded", "missing", "failed")]
    summary = {
        "fold": f["index"],
        "ts": time.time(),
        "n": len(f["groups"]),
        "via": {g["slot"]: (g["via"], g["state"], bool(_suspicious(g)))
                for g in f["groups"]},
        "unrecorded": [g["slot"] for g in pending],
    }
    st["history"].append(summary)
    f["status"] = "done"
    save_state(st)
    log("[redrill] fold %d/%d closed: %s" %
        (f["index"], st["fold_count"],
         " ".join("%s=%s" % (s, v[0] or v[1]) for s, v in summary["via"].items())))
    if st["active_fold"] + 1 >= st["fold_count"]:
        log("[redrill] all folds done -> finalize")
        return do_finalize(store, args)
    st["active_fold"] += 1
    st["folds"][st["active_fold"]]["status"] = "active"
    st["flags"] = {}
    save_state(st)
    log("[redrill] next fold %d/%d armed (knowledge-pool unfreeze: new "
        "lessons from this fold are now legal study material)" %
        (st["active_fold"] + 1, st["fold_count"]))
    return 0


def do_control(store, args):
    """面板干预: --pause/--continue/--abort/--skip-slot S。"""
    st = active_state()
    if not st:
        log("[redrill][error] no active campaign")
        return 2
    fl = st.setdefault("flags", {})
    if args.abort:
        fl["abort"] = True
        log("[redrill] control: ABORT armed (agent stops at next engine call)")
    elif args.pause:
        fl["pause"] = True
        log("[redrill] control: PAUSE armed (finish current slot then wait)")
    elif args.skip_slot:
        f, g = _find_group(st, args.skip_slot)
        if not g:
            log("[redrill][error] unknown slot")
            return 2
        g["state"] = "skipped"
        g["via"] = "fail"
        g["note"] = "user-skipped"
        save_state(st)
        log("[redrill] control: slot %s marked skipped" % g["slot"])
    elif getattr(args, "continue_", False) or getattr(args, "continue", False):
        fl["pause"] = False
        fl["abort"] = False
        log("[redrill] control: continue")
    save_state(st)
    return 0


def do_status(store, args):
    st = load_state()
    if not st:
        log("[redrill] no campaign state")
        return 0
    out = {
        "campaign": st["campaign_id"], "status": st["status"],
        "fold": st["active_fold"] + 1 if st.get("active_fold") is not None else 0,
        "folds": st["fold_count"], "pool_freeze_id": st["pool_freeze_lesson_id"],
        "missing": st.get("missing", []),
        "slots": [], "history": st.get("history", []),
        "flags": st.get("flags", {}),
        # 运维（2026-11-26）：面板进度区可直接展示最近错误，无需进日志目录。
        "ops_log": _ops_log_path(),
        "last_errors": _ops_tail(5, phases=("error",)),
    }
    for f in st["folds"]:
        for g in f["groups"]:
            out["slots"].append({
                "fold": f["index"], "slot": g["slot"], "category": g["category"],
                "state": g["state"], "via": g["via"],
                "suspicious": _suspicious(g) if g["state"] in ("recorded",) else False,
                "elapsed_s": round(time.time() - g["dispatch_ts"], 1)
                if g.get("dispatch_ts") and g["state"] == "running" else None,
                # blind_dir 仅进 --as-json（Host 侧 staging 用；agent 动词
                # 面不含 status，无泄漏面）
                "blind_dir": g["blind_dir"] if getattr(args, "as_json", False) else None,
            })
    if getattr(args, "as_json", False):
        log(json.dumps(out, ensure_ascii=False))
    else:
        log("[redrill] %s status=%s fold=%s/%s" %
            (st["campaign_id"], st["status"], out["fold"], st["fold_count"]))
        for s in out["slots"]:
            sym = {"recorded": "✔", "verified": "⊙", "running": "✏",
                   "pending": "·", "missing": "⊘", "skipped": "⏭",
                   "failed": "✖"}.get(s["state"], "?")
            log("  %s F%d %s %-10s %s" % (sym, s["fold"], s["slot"],
                                          s["state"], s["via"] or ""))
    return 0


def do_diag(store, args):
    """运维诊断快照（2026-11-26 用户令「交付可运维」）：引擎环境 / state /
    沙箱 / ops 日志尾部 / 最近错误一站式输出。面板「诊断」按钮与
    /ctf-redrill-diag 命令的服务端实现。--as-json 输出单行 JSON。"""
    st = load_state()
    sandbox = _sandbox_root()
    sand_dirs = []
    try:
        if os.path.isdir(sandbox):
            sand_dirs = sorted(d for d in os.listdir(sandbox)
                               if os.path.isdir(os.path.join(sandbox, d)))
    except OSError as e:
        sand_dirs = ["(list failed: %s)" % e]
    ops_path = _ops_log_path()
    try:
        ops_size = os.path.getsize(ops_path)
    except OSError:
        ops_size = 0
    active_fold = (st or {}).get("active_fold")
    info = {
        "ok": True,
        "engine_dir": _ws_root(),
        "memory_dir": _memory_dir(),
        "python": sys.version.split()[0],
        "state": {
            "path": _state_path(), "exists": os.path.exists(_state_path()),
            "status": (st or {}).get("status"),
            "campaign": (st or {}).get("campaign_id"),
            "fold": (active_fold + 1) if active_fold is not None else 0,
            "folds": (st or {}).get("fold_count"),
        },
        "sandbox": {"root": sandbox, "dirs": sand_dirs},
        "ops_log": {"path": ops_path, "bytes": ops_size, "tail": _ops_tail(20)},
        "recent_errors": _ops_tail(10, phases=("error",)),
    }
    if st and st.get("status") == "active":
        mx, excl, fpats = recall_filter(st)
        info["recall_gate"] = {
            "pool_freeze_lesson_id": mx,
            "excluded_writeup_cards": len(excl),
            "flag_echo_patterns": len(fpats),
        }
        info["slots"] = [
            {"slot": g["slot"], "fold": f["index"], "state": g["state"],
             "blind_dir_exists": os.path.isdir(g.get("blind_dir") or "")}
            for f in st["folds"] for g in f["groups"]]
    if getattr(args, "as_json", False):
        log(json.dumps(info, ensure_ascii=False))
        return 0
    log("[redrill][diag] engine=%s memory=%s python=%s" %
        (info["engine_dir"], info["memory_dir"], info["python"]))
    log("  state: exists=%s status=%s campaign=%s fold=%s/%s" %
        (info["state"]["exists"], info["state"]["status"],
         info["state"]["campaign"], info["state"]["fold"],
         info["state"]["folds"]))
    log("  sandbox: %s dirs=%d" % (sandbox, len(sand_dirs)))
    log("  ops_log: %s (%.1f KB, tail=%d)" %
        (ops_path, ops_size / 1024.0, len(info["ops_log"]["tail"])))
    errs = info["recent_errors"]
    log("  recent_errors: %d" % len(errs))
    for e in errs[-5:]:
        log("    [%s] %s %s" % (e.get("ts"), e.get("action"),
                                json.dumps(e.get("detail") or {},
                                           ensure_ascii=False)[:160]))
    return 0


def do_finalize(store, args):
    st = active_state() or load_state()
    if not st:
        log("[redrill][error] no state")
        return 2
    st["status"] = "done"
    # 折摘要补齐（fold-done 未走过时的兜底）
    if st.get("active_fold") is not None and st["history"]:
        pass
    save_state(st)
    html = build_report(st)
    log("[redrill] campaign %s finalized. report: %s" %
        (st["campaign_id"], html))
    return 0


def do_abandon(store, args):
    st = load_state()
    if not st:
        log("[redrill] no state")
        return 0
    st["status"] = "abandoned"
    st["abandoned_ts"] = time.time()
    save_state(st)
    log("[redrill] campaign %s abandoned (sandbox kept for forensics)" %
        st["campaign_id"])
    return 0


# ----------------------------------------------------------------------------
# recall 闭卷过滤 (k-fold closed-book view)
# ----------------------------------------------------------------------------

def recall_filter(st):
    """返回 (max_lesson_id, excluded_writeup_names, compiled_flag_pats)
    —— do_recall 调用侧应用。"""
    if not st or st.get("status") not in ("active",):
        return None, [], []
    idx = st.get("active_fold") or 0
    if idx >= len(st["folds"]):
        return None, [], []
    names, pats = set(), []
    for g in st["folds"][idx]["groups"]:
        names.update("writeup:" + n for n in g["names"])
        pats.append(re.compile(re.escape(g["flag"]), re.I))
    return st["pool_freeze_lesson_id"], sorted(names), pats


def lesson_visible_closed_book(row, max_id, excluded_names, flag_pats=()):
    """单行判可见性: id<=freeze, ch!=0, 不属于考批 writeup 卡, 且文本
    不含考批真 flag（兔底）。row: (id, cid, cname, wt, snip, tags)"""
    lid, cid, cname, wt, snip, tags = row
    if cid == 0:
        return False, "orphan"
    if max_id is not None and lid > max_id:
        return False, "frozen-out"
    if cname and cname in excluded_names:
        return False, "exam-group"
    txt = "%s %s %s" % (wt or "", snip or "", tags or "")
    for p in flag_pats:
        if p.search(txt):
            return False, "flag-echo"
    return True, ""


# ----------------------------------------------------------------------------
# audit: 会话事件流溯源
# ----------------------------------------------------------------------------

def do_audit(store, args):
    """扫描会话 jsonl(明文) 检测四类通道:
    R1 答案早现 (真 flag 出现于任何非 assistant 声明且早于该题 match)
    R2 路径违规 (writeups|trajectories.sqlite|redrill-state|CTFPilot 引擎目录)
    R3 搜索违规 (query 含 writeup/题解/真名)
    R4 未走 runner 的引擎直调 (argv 里出现 ctfpilot.py 完整路径)"""
    st = load_state()
    if not st:
        log("[redrill][error] no state to audit against")
        return 2
    paths = args.sessions or []
    findings = []
    flag_re = {}
    for f in st["folds"]:
        for g in f["groups"]:
            flag_re[g["slot"]] = (re.compile(re.escape(g["flag"])),
                                  re.compile(re.escape(normalize_flag(g["flag"])),
                                             re.I))
    name_re = {g["slot"]: g["names"] for f in st["folds"] for g in f["groups"]}
    banned = re.compile(r"writeups?[-a-z]*|trajectories\.sqlite|redrill-state",
                        re.I)
    engine_path = re.compile(re.escape(os.path.basename(
        os.path.dirname(os.path.abspath(__file__)))) + r".*ctfpilot\.py", re.I)
    search_pat = re.compile(r"writeup|题解|wp\b", re.I)
    ev = []
    for p in paths:
        if not os.path.exists(p):
            findings.append({"rule": "R0", "sev": "warn",
                             "note": "session file missing: %s" % p})
            continue
        try:
            with open(p, "rb") as fh:
                head = fh.read(4)
                fh.seek(0)
                if head[:4] == b"\x28\xb5\x2f\xfd":
                    # DSH 原生会话是 zstd 流: 先解压到临时明文再扫描
                    import zstandard as _zstd
                    import tempfile
                    _plain = tempfile.NamedTemporaryFile(
                        "w", suffix=".jsonl", delete=False, encoding="utf-8")
                    with _zstd.ZstdDecompressor().stream_reader(fh) as zr:
                        _plain.write(zr.read().decode("utf-8", "replace"))
                    _plain.close()
                    p = _plain.name
        except Exception as e:
            findings.append({"rule": "R0", "sev": "warn",
                             "note": "session open failed (%s): %s" %
                                     (type(e).__name__, p)})
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    ev.append(json.loads(ln))
                except ValueError:
                    pass
        ev.sort(key=lambda e: e.get("seq") or 0)
    flags_seen = {}
    for e in ev:
        seq = e.get("seq")
        t = e.get("type")
        d = e.get("data") or {}
        if t == "tool/call":
            argstr = d.get("arguments", "") or ""
            if banned.search(argstr):
                findings.append({"seq": seq, "rule": "R2", "sev": "high",
                                 "note": "banned path in tool call: ..." +
                                         banned.search(argstr).group(0)})
            if engine_path.search(argstr) and "redrill" not in argstr[:120]:
                findings.append({"seq": seq, "rule": "R4", "sev": "medium",
                                 "note": "direct engine invocation outside runner"})
            if search_pat.search(argstr):
                findings.append({"seq": seq, "rule": "R3", "sev": "high",
                                 "note": "solution-search query detected"})
            for slot, (rx, rxn) in flag_re.items():
                if rx.search(argstr) or rxn.search(argstr):
                    flags_seen.setdefault(slot, []).append((seq, "tool-call-arg"))
        elif t in ("tool/result", "user/message"):
            txt = json.dumps(d, ensure_ascii=False)
            for slot, (rx, rxn) in flag_re.items():
                if rx.search(txt) or rxn.search(txt):
                    flags_seen.setdefault(slot, []).append(
                        (seq, "tool-result" if t == "tool/result" else "user-msg"))
            if t == "user/message" and d.get("source", {}).get("kind") in ("runtime-context", "time-context"):
                pass
        elif t == "assistant/message":
            txt = "".join(c.get("text", "")
                          for c in (d.get("message", {}).get("content") or [])
                          if isinstance(c, dict))
            for slot, names in name_re.items():
                for nm in names:
                    if nm in txt:
                        findings.append({"seq": seq, "rule": "R3", "sev": "high",
                                         "note": "real challenge name spoken: %s" % nm[:60]})
    # R1: 首个非 assistant 出现 == tool-call-arg / tool-result / user-msg
    st_map = {g["slot"]: (g["state"], g["via"], g.get("first_match_ts"))
              for f in (st.get("folds") or []) for g in f["groups"]}
    for slot, spots in flags_seen.items():
        first = spots[0]
        if first[1] != "assistant-msg" and st_map.get(slot, ("",))[0] == "recorded":
            findings.append({"slot": slot, "seq": first[0], "rule": "R1",
                             "sev": "critical",
                             "note": "ground-truth flag surfaced via %s before "
                                     "solve credit" % first[1]})
    if args.as_json:
        log(json.dumps({"campaign": st["campaign_id"], "findings": findings},
                       ensure_ascii=False))
    else:
        log("[redrill][audit] campaign=%s findings=%d" %
            (st["campaign_id"], len(findings)))
        for x in findings:
            log("  [%s/%s] seq=%s %s" %
                (x.get("rule"), x.get("sev"), x.get("seq"), x.get("note", "")))
    st.setdefault("audit", []).append({
        "ts": time.time(), "sessions": paths, "n_findings": len(findings)})
    save_state(st)
    return 1 if any(f.get("sev") == "critical" for f in findings) else 0


# ----------------------------------------------------------------------------
# report
# ----------------------------------------------------------------------------

def _pct(a, b):
    return "%.0f%%" % (100.0 * a / b) if b else "—"


def build_report(st):
    """单文件 HTML 报告 (零依赖, 用户直读)。"""
    rows = []
    kpi = {"n": 0, "pass": 0, "fast": 0, "manual": 0, "fail": 0,
           "missing": 0, "suspicious": 0}
    for f in st["folds"]:
        for g in f["groups"]:
            kpi["n"] += 1
            v = g.get("via")
            if v in ("fast", "manual"):
                kpi["pass"] += 1
                kpi[v] += 1
            elif v == "fail":
                kpi["fail"] += 1
            else:
                kpi["missing"] += 1
            if _suspicious(g) and g["state"] == "recorded":
                kpi["suspicious"] += 1
            rows.append((f["index"], g["slot"], g["category"], g["state"],
                         v or "—", "⚠" if (_suspicious(g) and g["state"] == "recorded") else ""))
    audit_n = sum(a.get("n_findings", 0) for a in st.get("audit", []))
    hist = st.get("history", [])
    html = """<!doctype html><html lang="zh"><meta charset="utf-8">
<title>交叉重刷训练报告 {cid}</title>
<style>
 body{{font-family:"Segoe UI",system-ui,sans-serif;background:#0f1117;color:#e6e8ef;margin:0;padding:32px}}
 .wrap{{max-width:960px;margin:0 auto}}
 h1{{font-size:22px}} h2{{font-size:16px;margin-top:28px;color:#9ecbff}}
 .kpis{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}}
 .kpi{{background:#1a1e2a;border:1px solid #2a3042;border-radius:12px;padding:14px 18px;min-width:120px}}
 .kpi b{{display:block;font-size:26px;color:#7fd1ff}}
 .kpi span{{font-size:12px;color:#98a2b3}}
 table{{border-collapse:collapse;width:100%;font-size:13px}}
 th,td{{border-bottom:1px solid #232839;padding:7px 10px;text-align:left}}
 th{{color:#98a2b3;font-weight:600}}
 .ok{{color:#6ee7a0}} .bad{{color:#ff8a8a}} .warn{{color:#ffcf6e}}
 .note{{background:#161a26;border-radius:10px;padding:12px 16px;color:#aab4c8;font-size:13px;line-height:1.7}}
</style><div class="wrap">
<h1>🎯 交叉重刷训练报告 · {cid}</h1>
<p class="sub" style="color:#98a2b3">{folds} 折 · {n} 题 · 生成于 {ts}</p>
<div class="kpis">
 <div class="kpi"><b>{pass_}</b><span>通过（总 {n}）</span></div>
 <div class="kpi"><b>{fast}</b><span>快通道</span></div>
 <div class="kpi"><b>{manual}</b><span>手工解</span></div>
 <div class="kpi"><b>{fail}</b><span>失败</span></div>
 <div class="kpi"><b>{passpct}</b><span>通过率</span></div>
 <div class="kpi"><b>{susp}</b><span>待人工复核 ⚠</span></div>
 <div class="kpi"><b>{audit_n}</b><span>审计发现</span></div>
</div>
<h2>📋 逐题明细</h2>
<table><tr><th>折</th><th>slot</th><th>分类</th><th>状态</th><th>via</th><th>备注</th></tr>
{rows_html}
</table>
<h2>🛡️ 完整性说明</h2>
<div class="note">
· 记分守门：所有 <code>fast/manual</code> 通过均要求引擎侧 verify MATCH 证据，无证据申报已被物理拒绝。<br>
· 闭卷条件：本次考核期间 recall 已按 k-fold 隔离（考批组题解不可见、无归属孤儿卡不可见、批后新卡不可见）。<br>
· 孤儿卡拦截：challenge_id=0 的卡片在该 campaign 内一律不可检索。<br>
· 审计：会话事件流溯源扫描已完成，发现 {audit_n} 条线索（critical=答案早现，high=路径/搜索违规）。<br>
· 历史折：{hist}
</div>
</div></html>"""
    rows_html = "".join(
        "<tr><td>F%d</td><td>%s</td><td>%s</td><td class=\"%s\">%s</td>"
        "<td>%s</td><td>%s</td></tr>" % (
            r[0], r[1], r[2],
            "ok" if r[4] in ("fast", "manual") else ("bad" if r[4] in ("fail",) else ""),
            r[3], r[4], r[5]) for r in rows)
    html = html.format(
        cid=st["campaign_id"], folds=st["fold_count"], n=kpi["n"],
        ts=time.strftime("%Y-%m-%d %H:%M:%S"),
        pass_=kpi["pass"], fast=kpi["fast"], manual=kpi["manual"],
        fail=kpi["fail"], passpct=_pct(kpi["pass"], kpi["n"]),
        susp=kpi["suspicious"], audit_n=audit_n,
        rows_html=rows_html,
        hist=" → ".join("F%d %d/%d" % (h["fold"],
                                       sum(1 for v in h["via"].values() if v[0] in ("fast", "manual")),
                                       h["n"]) for h in hist) or "(首个 campaign)")
    out = os.path.join(_memory_dir(), "redrill-report-%s.html" % st["campaign_id"])
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out


# ----------------------------------------------------------------------------
# selftest (全部隔离: 临时 db / registry / sandbox)
# ----------------------------------------------------------------------------

def do_selftest(store_unused, args):
    import tempfile
    import sqlite3
    import memory.trajstore as trajstore
    root = tempfile.mkdtemp(prefix="redrill-selftest-")
    db = os.path.join(root, "t.sqlite")
    os.environ["CTFPILOT_DB"] = db
    # 2026-11-26（可运维改造连带修复）：do_redrill 包装层的 ops_log 会先解析
    # 并缓存 MEMORY_DIR；selftest 必须重置缓存，否则 state/sandbox/ops 会
    # 落到生产 memory 目录（覆盖生产 redrill-state.json）。
    global MEMORY_DIR
    MEMORY_DIR = None
    # 测试题目录也必须落在临时 root 下，绝不动生产 ctf-feed
    feed_dir = os.path.join(root, "ctf-feed")
    os.makedirs(feed_dir, exist_ok=True)
    store = trajstore.TrajStore(db)
    # 造题: A(有原件,文本含flag), B(有原件), C(无原件->missing); A2 与 A 同flag同组
    from memory.trajstore import TrajStore
    ok = [0]
    def check(label, cond):
        ok[0] += 1
        log("  [%s] %s" % ("PASS" if cond else "FAIL", label))
        if not cond:
            raise SystemExit("selftest failed at: " + label)
    for nm, cat, flag, with_dir in (("alpha-chal", "crypto", "flag{AAA_f1rst}", True),
                                    ("alpha-twin", "crypto", "flag{AAA_f1rst}", True),
                                    ("beta-chal", "web", "hitcon{BBB_sEcond}", True),
                                    ("gamma-chal", "misc", "flag{CCC}", False)):
        d = os.path.join(feed_dir, nm) if with_dir else None
        if d:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "readme.txt"), "w", encoding="utf-8") as f:
                f.write("challenge %s\nflag is %s somewhere\n" % (nm, flag if nm == "alpha-chal" else "hidden"))
        cid = store.new_challenge(nm, cat, d, "manual", "s")
        store.conn.execute("UPDATE challenges SET solved=1, flag=? WHERE id=?", (flag, cid))
    store.conn.commit()
    # 造 lessons: 考题 writeup 卡(id 应被排除) + 他题卡(可见) + 孤儿卡(排除) + 新冻结卡(排除)
    cid_a = store.conn.execute("SELECT id FROM challenges WHERE name='alpha-chal'").fetchone()[0]
    cid_b = store.conn.execute("SELECT id FROM challenges WHERE name='beta-chal'").fetchone()[0]
    wid = store.new_challenge("writeup:alpha-chal", "crypto", "x", "learn", "s")
    store.add_lesson(wid, "crypto", "alpha-solution", "use it", "flag{AAA_f1rst} leaked")
    obid = store.add_lesson(cid_b, "web", "beta-tech", "other", "safe content")
    orphan = store.conn.execute(
        "INSERT INTO lessons(challenge_id,ts,category,technique,when_to_use,"
        "snippet) VALUES(0,?,'misc','orph','orph','orph-snippet')",
        (time.time(),)).lastrowid
    store.conn.commit()
    # plan (isolate sandbox+registry)
    ns = argparse.Namespace(seed=7, fold_size=1, folds=3, sandbox=os.path.join(root, "sb"),
                            registry=root)
    check("plan ok", do_plan(store, ns) == 0)
    st = active_state()
    check("missing filtered", "gamma-chal" in st.get("missing", []))
    slot_g = st["folds"][0]["groups"][0]
    names = set(slot_g["names"])
    check("same-flag group together", names == {"alpha-chal", "alpha-twin"} or names == {"beta-chal"})
    leaked = []
    for dp, _, fns in os.walk(slot_g["blind_dir"]):
        for fn in fns:
            try:
                c = open(os.path.join(dp, fn), encoding="utf-8").read()
                if "flag{AAA_f1rst}" in c:
                    leaked.append(fn)
            except (UnicodeDecodeError, OSError):
                pass
    check("blind copy flag-free", not leaked)
    # begin + verify mismatch/match
    ns = argparse.Namespace(slot=slot_g["slot"], flag="")
    check("begin ok", do_begin(store, ns) == 0)
    ns = argparse.Namespace(slot=slot_g["slot"], flag="flag{wrong}")
    check("verify mismatch rc=1", do_verify(store, ns) == 1)
    real = slot_g["flag"]
    ns = argparse.Namespace(slot=slot_g["slot"], flag="FLAG" + real[4:])
    check("verify match (wrapper case-insensitive)",
          do_verify(store, ns) == 0)
    if real[-1].upper() != real[-1]:
        ns = argparse.Namespace(slot=slot_g["slot"],
                                flag=real[:-1] + real[-1].upper())
        check("verify content case-sensitive (wrong case rejected)",
              do_verify(store, ns) == 1)
    # record 守门: fast 无 fast_solved 证据 -> 拒
    ns = argparse.Namespace(slot=slot_g["slot"], via="fast", note="")
    check("record fast rejected (no engine fast evidence)", do_record(store, ns) == 2)
    ns = argparse.Namespace(slot=slot_g["slot"], via="manual", note="")
    check("record manual ok (verify evidence present)", do_record(store, ns) == 0)
    # 无 verify 直接 record -> 拒 (下一 fold 的题)
    g2 = None
    for f in st["folds"][1:]:
        for g in f["groups"]:
            g2 = g
            break
        if g2:
            break
    if g2:
        # begin without verify then record manual -> rejected
        ns2b = argparse.Namespace(slot=g2["slot"], flag="")
        do_begin(store, ns2b)
        ns2r = argparse.Namespace(slot=g2["slot"], via="manual", note="")
        check("record manual rejected (no verify)", do_record(store, ns2r) == 2)
        check("missing recorded ok", do_record(store, argparse.Namespace(
            slot=g2["slot"], via="missing", note="")) == 0)
    # closed-book recall 过滤
    nsv = recall_filter(st)
    mx, excl, fpats = nsv
    rows2 = store.conn.execute(
        "SELECT l.id, l.challenge_id, c.name, l.when_to_use, l.snippet, l.tags "
        "FROM lessons l LEFT JOIN challenges c ON c.id=l.challenge_id "
        "WHERE l.category='crypto' AND (l.snippet<>'' OR l.tags<>'')").fetchall()
    vis = [r for r in rows2 if lesson_visible_closed_book(r, mx, excl, fpats)[0]]
    check("exam writeup card hidden", all(r[2] != "writeup:alpha-chal" for r in vis))
    check("orphan hidden", all(r[1] != 0 for r in vis))
    # fold-done 推进
    nsf = argparse.Namespace(report_html="", sessions=[])
    check("fold-done advances", do_fold_done(store, nsf) in (0,))
    st2 = active_state() or load_state()
    check("fold history recorded", len(st2.get("history", [])) >= 1)
    # audit 自检: 构造含 flag 注入的假会话
    fake = os.path.join(root, "fake.jsonl")
    with open(fake, "w", encoding="utf-8") as f:
        f.write(json.dumps({"seq": 1, "type": "user/message", "data": {
            "content": [{"text": "go " + real}], "source": {"kind": "ctfpilot"}}}) + "\n")
        f.write(json.dumps({"seq": 2, "type": "tool/call", "data": {
            "name": "pwsh", "arguments": "{\"command\":\"Get-ChildItem C:/x/writeups\"}"}}) + "\n")
    rc = do_audit(store, argparse.Namespace(sessions=[fake], as_json=False))
    st3 = load_state()
    check("audit detected findings", st3.get("audit") and st3["audit"][-1]["n_findings"] >= 1)
    check("audit rc flags critical", rc == 1)
    # 运维日志自检（2026-11-26）：ops_log 落盘 + diag JSON 完整
    ops_log("selftest", "ok", {"probe": "selftest-ops"})
    check("ops log persisted",
          any(e.get("action") == "selftest" for e in _ops_tail(5)))
    check("ops flag redacted (no raw flag in log)",
          all("AAA_f1rst" not in json.dumps(e, ensure_ascii=False)
              for e in _ops_tail(50)))
    import contextlib
    import io
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        _drc = do_diag(store, argparse.Namespace(as_json=True))
    _dj = None
    for _ln in _buf.getvalue().splitlines():
        if _ln.strip().startswith("{"):
            try:
                _dj = json.loads(_ln)
            except ValueError:
                pass
    check("diag rc ok", _drc == 0)
    check("diag json complete", bool(_dj and _dj.get("ok") is True and
                                     "state" in _dj and "sandbox" in _dj and
                                     "ops_log" in _dj))
    # cleanup env
    os.environ.pop("CTFPILOT_DB", None)
    MEMORY_DIR = None
    log("[redrill][selftest] ALL %d CHECKS PASSED" % ok[0])
    return 0


