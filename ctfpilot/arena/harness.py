"""Arena harness：变体抽象、单题求解执行、题目指纹与隔离台账。

纯标准库；被 run_ablation.py import，也可被 solve-loop / lab 直接复用：

    from harness import list_variants, run_variant, challenge_digest

一个 HarnessVariant = 一组求解配置（命令行参数 + 环境变量 + 前置命令）。
run_variant 只做一次 subprocess 调用并解析结果，不读写任何全局状态。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time


TRUTHY = {"1", "true", "yes", "y", "on", "solved", "ok", "pass"}
ARTIFACT_DIRS = {"_extracted", "__pycache__", ".git"}
BENCH_RE = re.compile(r"\[benchmark\]\s*(.*)$")
KV_RE = re.compile(r"([A-Za-z_][\w.-]*)\s*=\s*(\"[^\"]*\"|'[^']*'|\S+)")
TRIAGE_CAT_RE = re.compile(r"\[triage\][^\n]*?category=([\w.-]+)")
SOLVED_RE = re.compile(r"\[result\]\s+SOLVED in ([0-9.]+)s")
UNSOLVED_RE = re.compile(r"\[result\]\s+NOT SOLVED in ([0-9.]+)s(?:\s*\((\d+) attempts?\))?")
FLAG_RE = re.compile(r"\[result\]\s+FLAG:\s*(\S+)")


class HarnessVariant:
    """一组可复现的求解配置。

    env_overrides 的值支持占位符：{tempdir}（本次运行独占的临时目录，
    跑完即删）、{chall_dir}、{engine_root}。
    env_probe 声明"引擎必须显式确认才认为生效"的环境变量；未确认即标
    variant_unsupported，避免把"开关不存在"误读成"该能力无贡献"。
    """

    def __init__(self, name, env_overrides=None, solve_args=None,
                 pre_cmds=None, desc="", env_probe=None):
        self.name = name
        self.env_overrides = dict(env_overrides or {})
        self.solve_args = tuple(solve_args or ())
        self.pre_cmds = tuple(tuple(c) for c in (pre_cmds or ()))
        self.desc = desc
        self.env_probe = tuple(env_probe or ())

    def with_env(self, extra):
        """返回副本；变体自身声明的 env 优先于调用方注入的默认值。"""
        merged = dict(extra or {})
        merged.update(self.env_overrides)
        return HarnessVariant(self.name, merged, self.solve_args,
                              self.pre_cmds, self.desc, self.env_probe)

    def __repr__(self):
        return "HarnessVariant(%s)" % self.name


def _build_variants():
    bench = ("--mode", "auto", "--benchmark", "--json")
    return [
        HarnessVariant(
            "baseline-fast", {}, ("--mode", "fast"), (),
            "确定性快通道下界：只跑离线检测器，不进入深通道（免费得分）",
        ),
        HarnessVariant(
            "baseline-auto", {}, bench, (),
            "完整双通道基线：快通道 + 深通道，要求 benchmark/json 结构化输出",
        ),
        HarnessVariant(
            "no-memory", {"CTFPILOT_DB": "{tempdir}/no-memory.sqlite"}, bench, (),
            "记忆消融：空轨迹库启动，度量知识/教训召回对求解的净贡献",
        ),
        HarnessVariant(
            "no-jit", {"CTFPILOT_NO_JIT": "1"}, bench, (),
            "JIT 重检索消融：关闭深通道每 4 步/连败 2 次的自动重检索",
            env_probe=("CTFPILOT_NO_JIT",),
        ),
    ]


def list_variants():
    return _build_variants()


def get_variant(name):
    for v in _build_variants():
        if v.name == name:
            return v
    return None


def resolve_variants(spec):
    """'all' / 'v1,v2' -> [HarnessVariant]；未知名字抛 ValueError。"""
    s = (spec or "all").strip()
    if s == "all":
        return _build_variants()
    wanted = [x.strip() for x in s.split(",") if x.strip()]
    if "all" in wanted:
        return _build_variants()
    out, unknown = [], []
    for n in wanted:
        v = get_variant(n)
        if v is None:
            unknown.append(n)
        else:
            out.append(v)
    if unknown:
        raise ValueError("unknown variant(s): %s; available: %s" % (
            ", ".join(unknown), ", ".join(v.name for v in _build_variants())))
    if not out:
        raise ValueError("no variant selected")
    return out


def challenge_digest(chall_dir):
    """题目内容指纹（sha256）：忽略引擎产物，用于"评测过即隔离"。

    只覆盖原始题目文件，因此解题过程写入的 _ctfpilot_* 与 _extracted/
    不会改变指纹。
    """
    entries = []
    base = os.path.abspath(chall_dir)
    for root, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in ARTIFACT_DIRS)
        for n in sorted(names):
            if n.startswith("_ctfpilot"):
                continue
            p = os.path.join(root, n)
            rel = os.path.relpath(p, base).replace("\\", "/")
            entries.append((rel, p))
    h = hashlib.sha256()
    for rel, p in sorted(entries):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        try:
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()


class Quarantine:
    """评测隔离台账：一旦某题被评测，其指纹写入此处，永不再进训练输入。"""

    def __init__(self, path):
        self.path = os.path.abspath(path)

    def entries(self):
        if not os.path.exists(self.path):
            return []
        rows = []
        with open(self.path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return rows

    def digests(self):
        return {r.get("sha256"): r for r in self.entries() if r.get("sha256")}

    def has(self, digest):
        return digest in self.digests()

    def add(self, name, digest, pool):
        if self.has(digest):
            return False
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        rec = {"challenge": name, "sha256": digest,
               "pool": os.path.abspath(pool),
               "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True


def _expand(value, tempdir, chall_dir, engine_root):
    return (value.replace("{tempdir}", tempdir)
                 .replace("{chall_dir}", chall_dir)
                 .replace("{engine_root}", engine_root))


def _merge_reason(current, new):
    if not current:
        return new
    parts = current.split("+")
    return current if new in parts else current + "+" + new


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in TRUTHY


def _as_float(value):
    if value is None:
        return None
    s = str(value).strip().rstrip("s").rstrip("S")
    try:
        return float(s)
    except ValueError:
        return None


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_list(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x for x in re.split(r"[,;|\s]+", str(value)) if x]


def _pick(raw, *keys):
    for k in keys:
        if k in raw and raw[k] not in (None, ""):
            return raw[k]
    return None


FLAG_SENTINELS = {"-", "--", "none", "null", "nil", "n/a", "na", "(none)", ""}


def _normalize_info(raw):
    low = {str(k).lower(): v for k, v in raw.items()}
    flag = _pick(low, "flag", "found_flag", "answer")
    flag = str(flag).strip().strip('"').strip("'") if flag else None
    if flag is not None and flag.lower() in FLAG_SENTINELS:
        flag = None
    solved_raw = _pick(low, "solved", "success", "ok", "passed")
    # 显式 solved 字段优先；缺失时才用 flag 是否存在推断
    solved = _as_bool(solved_raw) if solved_raw is not None else bool(flag)
    t = _as_float(_pick(low, "time", "time_seconds", "elapsed", "elapsed_s",
                        "seconds", "duration"))
    attempts = _as_int(_pick(low, "attempts", "n_attempts", "tries"))
    return {"solved": bool(solved), "flag": flag, "time": t,
            "attempts": attempts,
            "unsupported_env": _as_list(_pick(low, "unsupported_env",
                                              "unsupported_envs")) or [],
            "env_ack": _as_list(_pick(low, "env_ack", "acked_env", "envs")),
            "variant_unsupported": _as_bool(_pick(low, "variant_unsupported",
                                                  "unsupported")),
            "name": _pick(low, "name", "challenge", "task")}


def _parse_benchmark(stdout):
    info = None
    for line in stdout.splitlines():
        m = BENCH_RE.search(line)
        if not m:
            continue
        body = m.group(1).strip()
        if not body:
            continue
        if body.startswith("{"):
            try:
                data = json.loads(body)
            except ValueError:
                continue
            if isinstance(data, dict):
                info = data
            continue
        info = {k: v for k, v in KV_RE.findall(body)}
    return _normalize_info(info) if info else None


def _parse_legacy(stdout):
    """当前引擎（尚未支持 --benchmark）的自然输出。"""
    m = UNSOLVED_RE.search(stdout)
    if m:
        return {"solved": False, "flag": None, "time": _as_float(m.group(1)),
                "attempts": _as_int(m.group(2)), "unsupported_env": [],
                "env_ack": None, "variant_unsupported": False, "name": None}
    m = SOLVED_RE.search(stdout)
    if m:
        f = FLAG_RE.search(stdout)
        return {"solved": True, "flag": f.group(1) if f else None,
                "time": _as_float(m.group(1)), "attempts": None,
                "unsupported_env": [], "env_ack": None,
                "variant_unsupported": False, "name": None}
    return None


def _result_json_paths(chall_dir, engine_root):
    return [os.path.join(chall_dir, "_ctfpilot_result.json"),
            os.path.join(engine_root, "_ctfpilot_result.json"),
            os.path.join(os.getcwd(), "_ctfpilot_result.json")]


def _snapshot_mtimes(paths):
    out = {}
    for p in paths:
        try:
            out[p] = os.path.getmtime(p)
        except OSError:
            out[p] = None
    return out


def _read_result_json(paths, before):
    """读取引擎的 _ctfpilot_result.json。

    只接受本次运行新写出的文件：跑前已存在且 mtime 未前进的一律忽略，
    否则上一题/上一次运行的残留会被误记成本题结果。
    """
    for p in paths:
        try:
            if not os.path.isfile(p):
                continue
            prev = before.get(p)
            if prev is not None and os.path.getmtime(p) <= prev + 0.001:
                continue
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return _normalize_info(data), p
        except (OSError, ValueError):
            continue
    return None, None


def _exec(cmd, cwd, env, timeout_s):
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return proc.returncode, out or "", err or "", False, time.time() - t0
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        out, err = proc.communicate()
        return -1, out or "", (err or "") + "\n[arena] timeout after %ss" % timeout_s, True, time.time() - t0


def _rejected_args(stderr, solve_args):
    """从 argparse 报错里取出引擎不认识的参数，返回待剥离列表。"""
    if "unrecognized arguments" not in stderr and "unknown option" not in stderr.lower():
        return []
    tail = stderr.split("unrecognized arguments:", 1)[-1]
    tokens = [t.strip().rstrip(",") for t in tail.split() if t.strip().startswith("-")]
    known = set(solve_args)
    return [t for t in tokens if t in known]


def run_variant(variant, chall_dir, engine_root, python, timeout_s=900):
    """跑一次 solve，返回结果 dict；任何异常都转成失败记录，不抛出。"""
    chall_dir = os.path.abspath(chall_dir)
    engine_root = os.path.abspath(engine_root)
    python = python or sys.executable
    row = {
        "challenge": os.path.basename(chall_dir.rstrip("\\/")),
        "variant": variant.name,
        "solved": False,
        "flag": None,
        "time_seconds": None,
        "wall_seconds": None,
        "attempts": None,
        "variant_unsupported": False,
        "unsupported_reason": None,
        "rc": None,
        "status": "error",
        "time_source": None,
        "degraded": False,
        "degraded_args_removed": [],
        "category": None,
        "detail": "",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    info = None
    tempdir = tempfile.mkdtemp(prefix="ctfpilot-arena-")
    try:
        env = os.environ.copy()
        for k, v in variant.env_overrides.items():
            env[str(k)] = _expand(str(v), tempdir, chall_dir, engine_root)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        pre_errors = []
        for cmd in variant.pre_cmds:
            rc0, _o, e0, to0, _w0 = _exec([str(x) for x in cmd],
                                          engine_root, env, min(60, timeout_s))
            if to0 or rc0 != 0:
                pre_errors.append("pre_cmd %s rc=%s %s" % (
                    " ".join(cmd), rc0, (e0 or "").strip()[:200]))

        engine_py = os.path.join(engine_root, "ctfpilot.py")
        cmd = [python, "-X", "utf8", engine_py, "solve", chall_dir] \
            + list(variant.solve_args)
        json_paths = _result_json_paths(chall_dir, engine_root)
        json_before = _snapshot_mtimes(json_paths)
        rc, out, err, timed_out, wall = _exec(cmd, engine_root, env, timeout_s)
        row.update(rc=rc, wall_seconds=round(wall, 3))
        mcat = TRIAGE_CAT_RE.search(out)
        if mcat:
            row["category"] = mcat.group(1)

        if timed_out:
            row.update(status="timeout", time_seconds=float(timeout_s),
                       time_source="timeout",
                       detail="killed after %ss" % timeout_s)
        else:
            rejected = _rejected_args(err, variant.solve_args)
            if rc == 2 and rejected:
                row["variant_unsupported"] = True
                row["unsupported_reason"] = "cli"
                row["degraded_args_removed"] = list(rejected)
                stripped = [a for a in variant.solve_args if a not in rejected]
                cmd2 = [python, "-X", "utf8", engine_py, "solve", chall_dir] + stripped
                rc2, out2, err2, to2, wall2 = _exec(cmd2, engine_root, env, timeout_s)
                row.update(degraded=True, wall_seconds=round(wall + wall2, 3),
                           rc=rc2,
                           detail="engine rejected %s; degraded run without them"
                                  % " ".join(rejected))
                if to2:
                    row.update(status="timeout", time_seconds=float(timeout_s),
                               time_source="timeout", timed_out=True)
                    timed_out = True
                else:
                    rc, out, err = rc2, out2, err2
                    mcat = TRIAGE_CAT_RE.search(out)
                    if mcat:
                        row["category"] = mcat.group(1)

            if not timed_out:
                info = _parse_benchmark(out)
                source = "benchmark"
                if info is None:
                    info, jpath = _read_result_json(json_paths, json_before)
                    source = "result_json" if info else None
                    if info:
                        row["detail"] = (row["detail"] + "; " if row["detail"] else "") \
                            + "result json: %s" % os.path.basename(jpath or "")
                if info is None:
                    info = _parse_legacy(out)
                    source = "stdout" if info else None

            if info is not None:
                row["solved"] = bool(info["solved"])
                row["flag"] = info["flag"]
                row["attempts"] = info["attempts"]
                row["time_source"] = source
                row["time_seconds"] = (info["time"] if info["time"] is not None
                                       else round(wall, 3))
                if info["time"] is None:
                    row["time_source"] = "wall"
                row["status"] = "solved" if row["solved"] else "unsolved"
                if info.get("variant_unsupported"):
                    row["variant_unsupported"] = True
                    row["unsupported_reason"] = row["unsupported_reason"] or "engine"
            elif not timed_out:
                row["status"] = "unparsed" if rc in (0, 1) else "error"

        if pre_errors:
            row["detail"] = (row["detail"] + "; " if row["detail"] else "") \
                + "; ".join(pre_errors)

        # env 开关生效性：引擎必须在 benchmark 行/结果 JSON 里 ack。
        # 与 cli 原因并存：即使这次是降级跑，也不能默认 env 开关生效。
        if variant.env_probe:
            rejected_env = (info or {}).get("unsupported_env") or []
            ack = (info or {}).get("env_ack")
            missing = [k for k in variant.env_probe if k in rejected_env]
            if missing:
                row["variant_unsupported"] = True
                row["unsupported_reason"] = _merge_reason(
                    row["unsupported_reason"], "env-rejected")
                row["detail"] = (row["detail"] + "; " if row["detail"] else "") \
                    + "engine reports unsupported env: %s" % ", ".join(missing)
            elif ack is None:
                row["variant_unsupported"] = True
                row["unsupported_reason"] = _merge_reason(
                    row["unsupported_reason"], "env-not-acknowledged")
                row["detail"] = (row["detail"] + "; " if row["detail"] else "") \
                    + "engine did not ack env %s" % ",".join(variant.env_probe)
            else:
                ack_set = set(ack)
                missing = [k for k in variant.env_probe if k not in ack_set]
                if missing:
                    row["variant_unsupported"] = True
                    row["unsupported_reason"] = _merge_reason(
                        row["unsupported_reason"], "env-not-acknowledged")
                    row["detail"] = (row["detail"] + "; " if row["detail"] else "") \
                        + "engine ack lacks %s" % ",".join(missing)

        if row["status"] in ("error", "unparsed"):
            tail = err.strip().splitlines()[-1][:300] if err.strip() else "no parseable result"
            row["detail"] = (row["detail"] + "; " if row["detail"] else "") + tail
    except Exception as exc:  # 运行器自身绝不因单题失败而崩
        row["status"] = "crash"
        row["detail"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)

    if row["status"] in ("error", "crash"):
        row["solved"] = False
    return row


def summarize(rows, timeout_s):
    """按变体汇总；对抗时钟见 README 公式。"""
    groups = {}
    for r in rows:
        groups.setdefault(r.get("variant"), []).append(r)
    out = []
    for name in sorted(groups):
        rs = groups[name]
        n = len(rs)
        solved = [r for r in rs if r.get("solved")]
        rate = len(solved) / n if n else 0.0
        mean_solved = (sum(float(r.get("time_seconds") or 0.0) for r in solved)
                       / len(solved)) if solved else 0.0
        adversarial = mean_solved * rate + float(timeout_s) * (1.0 - rate)
        out.append({
            "variant": name,
            "n": n,
            "solved": len(solved),
            "solved_rate": rate,
            "mean_solved_time": mean_solved,
            "adversarial_time": adversarial,
            "timeouts": sum(1 for r in rs if r.get("status") == "timeout"),
            "errors": sum(1 for r in rs if r.get("status") in ("error", "crash", "unparsed")),
            "unsupported": sum(1 for r in rs if r.get("variant_unsupported")),
            "degraded": sum(1 for r in rs if r.get("degraded")),
        })
    return out