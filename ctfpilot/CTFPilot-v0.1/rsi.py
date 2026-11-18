"""RSI: recursive self-improvement over the trajectory store (自改进环).

Signal sources (all offline):
  1. fixation candidates -> detector proposals (经验第二次出现就该是工具)
  2. failure-taxonomy hot spots -> prompt-fix proposals (deep channel blind spots)
  3. zero-rate categories       -> card-thicken proposals (技能卡薄弱)
  4. raw-echo retraction rate   -> flagcheck-tighten proposals (回声污染口径)
  5. skill-card technique gap   -> detector proposals (卡里有、检测器没有);
     scans skills/<cat>.md「常见打法」bullets against the detector registry,
     excluding interactive/live-service families (their knowledge lives in
     the deep-channel playbook, NOT in fast-path detectors)
"""
import json
import os
import re
import time


LEDGER_DIR = "skills/rsi"
CHAIN_REL = os.path.join("skills", "arena", "state", "version-chain.jsonl")
# [evolve-20261117-16] durable adoption snapshots live under the ledger dir
# (skills/rsi/snapshots/<pid>/), NEVER under .temp/ -- a temp area is a
# cleaning policy, not a retention policy (rsi-078/079 snapshots under
# engine .temp/evolve17/ are already gone).
SNAPSHOTS_REL = os.path.join(LEDGER_DIR, "snapshots")
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
PROPOSAL_TYPES = ("detector", "prompt-fix", "card-thicken",
                  "flagcheck-tighten")

# Thresholds: a signal must clear these before it becomes a proposal,
# so noise (one-off failures, thin categories) never enters the ledger.
ERR_FAIL_MIN = 3        # failures accumulated for one error class
CARD_RATE_MAX = 0.15    # category solve rate below this (with N>=4 solves)
CARD_TOTAL_MIN = 4
ECHO_RATE_MIN = 0.02    # raw-echo share of error-classified attempts

# Techniques already implemented by fastpath/detectors.py. A fixation
# proposal for these is redundant noise (e.g. chain-decode had 262
# successes precisely because it is already a detector).
FASTPATH_DETECTORS = {
    "chain-decode", "caesar", "xor-single", "xor-token", "decompress",
    "decompress+chain", "png-lsb",
}

# The seven base skill cards whose 常见打法 bullets are scanned for
# candidate techniques missing from the fast path.
BASE_CARDS = ("crypto", "web", "reverse", "pwn", "misc",
              "forensics", "osint")

# Card bullets that are already covered by a fast-path detector, mapped by
# the technique family keyword found in the bullet text. Kept as engine-
# owned registry data (same pattern as FASTPATH_DETECTORS above), not
# distilled into orchestration code.
CARD_COVERED = {
    "chain-decode": ("编码链", "链解码", "chained"),
    "caesar": ("caesar", "凯撒"),
    "xor-single": ("单字节 xor", "单字节XOR", "single-byte xor"),
    "xor-token": ("token-key", "题面词"),
    "decompress": ("压缩解包", "zlib/gzip raw inflate", "gunzip"),
    "png-lsb": ("lsb", "低有效位", "bit-plane"),
}

# Bullets that are offline-boundary/tooling notes, not technique candidates
# (e.g. "osint 类默认离线不做实际联网" describes a policy, not a detector).
EXCLUDE_BULLET_TOKENS = (
    "默认离线", "输出搜索关键词", "由人执行", "本机工具",
)

# Live-service / networked / runtime-dependent technique families. Their
# knowledge ALREADY lives in the deep channel as model-executed playbook --
# they are NOT fast-path detector candidates (no server = no SSTI, no ELF
# runtime = no ROP, offline = no osint). A gap scan must not propose them as
# detectors: that would confuse memory with capability.
INTERACTIVE_KEYWORDS = (
    "sql", "union select", "information_schema", "宽字节",
    "ssti", "jinja2", "erb",
    "反序列化", "__wakeup", "phar",
    "ssrf", "gopher", "dict://",
    "文件上传", "双后缀", ".htaccess",
    "xsleak", "缓存投毒", "jwt", "alg:none",
    "前端题", "隐藏字段",
    "ret2", "栈溢出", "gadget", "pop rdi",
    "格式化字符串", "%p", "canary",
    "堆", "fastbin", "tcache", "uaf", "double free", "one_gadget",
    "padding oracle",
    "地理定位", "时间定位", "人物定位", "地图", "街景", "航班",
    "天气预报", "社交平台", "车牌",
)


def _is_interactive(label):
    """Does this technique clause depend on a live service / network /
    binary runtime? (keyword registry, engine-owned data like CARD_COVERED)"""
    low = label.lower()
    return any(k in low for k in INTERACTIVE_KEYWORDS)


def _covered_by_detector(label):
    """Does this card bullet's text name a technique that already has a
    fast-path detector?"""
    low = label.lower()
    for _det, keys in CARD_COVERED.items():
        for k in keys:
            if k.lower() in low:
                return True
    return False


def _proposal_key(p):
    return (p.get("type"), p.get("target"))


def _redundant_detector(p):
    """A detector proposal is redundant when the target is already a
    fast-path detector or is a case memory card, not a technique."""
    if p.get("type") != "detector":
        return False
    target = str(p.get("target") or "")
    technique = target.split("/", 1)[1] if "/" in target else target
    return technique in FASTPATH_DETECTORS or technique.startswith("case:")


def _pick_balanced(props, k):
    """Keep proposal diversity: at most 2 detectors, at most 1 of each other
    type, then fill remaining slots from the leftover pool. A global top-k
    sorted with detectors first would always hide card-thicken/prompt-fix."""
    quotas = {"detector": 2, "prompt-fix": 1, "card-thicken": 1,
              "flagcheck-tighten": 1}
    chosen, used = [], set()
    for p in props:
        t = p["type"]
        if quotas.get(t, 1) <= 0:
            continue
        chosen.append(p)
        used.add(id(p))
        quotas[t] = quotas.get(t, 1) - 1
        if len(chosen) >= k:
            return chosen
    for p in props:
        if id(p) in used:
            continue
        chosen.append(p)
        if len(chosen) >= k:
            break
    return chosen


def _project_root():
    # these modules sit AT the engine root (like ctfpilot.py): one dirname up.
    return os.path.dirname(os.path.abspath(__file__))


def ledger_paths(root=None):
    root = root or _project_root()
    d = os.path.join(root, LEDGER_DIR)
    return {"dir": d, "ledger": os.path.join(d, "ledger.json"),
            "proposals": os.path.join(d, "proposals.jsonl")}


def load_ledger(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"proposals": []}


def save_ledger(data, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_proposal_stream(path):
    """Every ever-proposed record, newest-appended."""
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("id"):
                    out.append(o)
    except OSError:
        pass
    return out


def chain_path(root=None):
    """Version-chain jsonl path, ledger-relative under the same root."""
    root = root or _project_root()
    return os.path.join(root, CHAIN_REL)


def load_chain(path):
    """Chain rows with valid JSON and a chain_entry number, in file order."""
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if isinstance(o, dict) and o.get("chain_entry"):
                    rows.append(o)
    except OSError:
        pass
    return rows


def chain_has_pid(path, pid):
    """Does the chain already carry a line for this proposal id? Chain pid
    fields carry a parenthetical suffix ("rsi-074 (2026-11-17 04:03 ...)"),
    so match on the leading id token only."""
    tok = str(pid).strip()
    for row in load_chain(path):
        if str(row.get("pid") or "").split(" ", 1)[0] == tok:
            return True
    return False


def _snap(path):
    """Byte-faithful rollback snapshot: (existed, bytes)."""
    try:
        with open(path, "rb") as f:
            return True, f.read()
    except OSError:
        return False, b""


def _restore(path, snap):
    """Restore a file to its snapshot. A snap taken from a file that did
    not exist removes the file again (guarded write created it)."""
    existed, blob = snap
    if existed:
        with open(path, "wb") as f:
            f.write(blob)
    else:
        try:
            os.remove(path)
        except OSError:
            pass


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(131072), b""):
            h.update(chunk)
    return h.hexdigest()


def candidate_snapshot(root, pid, role, files):
    """[evolve-20261117-16] Snapshot the adoption's real files into the
    durable ledger area and compute their sha256 -- replacing the
    hand-computed rollback hash that produced the rsi-083 first-pass
    errata (an uncomputed placeholder sha256 was committed into a chain
    row's rollback text and the whole adoption had to be rolled back and
    re-run). `role` is "candidate" or "incumbent". Returns a list of
    {"path", "sha256", "snapshot"} (snapshot = path relative to `root`);
    raises ValueError on a missing file, a basename collision, or a
    same-pid snapshot whose bytes drifted (pid reuse protection)."""
    if role not in ("candidate", "incumbent"):
        raise ValueError("role must be candidate|incumbent")
    base = root or _project_root()
    out = []
    seen = {}
    for path in files or []:
        ap = os.path.abspath(path)
        if not os.path.isfile(ap):
            raise ValueError("snapshot file not found: %s" % ap)
        digest = _sha256_file(ap)
        with open(ap, "rb") as f:
            blob = f.read()
        name = os.path.basename(ap)
        if name in seen and seen[name] != digest:
            raise ValueError("two files share basename %r with different "
                             "bytes; give them distinct names" % name)
        seen[name] = digest
        snap_dir = os.path.join(base, SNAPSHOTS_REL, str(pid))
        os.makedirs(snap_dir, exist_ok=True)
        snap_path = os.path.join(snap_dir, "%s-%s" % (role, name))
        if os.path.exists(snap_path):
            with open(snap_path, "rb") as f:
                prev = f.read()
            if prev == blob:
                pass                # idempotent re-adopt: same bytes, skip
            else:
                raise ValueError(
                    "snapshot %s exists with different bytes (pid %s "
                    "reuse with drifted content is forbidden)"
                    % (snap_path, pid))
        else:
            with open(snap_path, "wb") as f:
                f.write(blob)
        out.append({"path": os.path.relpath(ap, base), "sha256": digest,
                    "snapshot": os.path.relpath(snap_path, base)})
    return out


def rollback_hash_guard(rollback_text, real_hashes):
    """[evolve-20261117-16] Every sha256 the human rollback text CLAIMS must
    be a hash of a file the CLI actually snapshotted. This is the structural
    fix for the rsi-083 failure mode (placeholder hash committed unseen):
    a claim that matches nothing real raises before any ledger/chain write.
    Claim-less rollback texts pass (early chain rows pre-date this guard);
    an empty real-hash set means the caller did not opt in -- do not guard."""
    real = {h.lower() for h in (real_hashes or [])}
    if not real:
        return
    claimed = {m.lower() for m in _SHA256_RE.findall(str(rollback_text or ""))}
    missed = sorted(claimed - real)
    if missed:
        raise ValueError(
            "rollback claims %d sha256 value(s) that match NO snapshotted "
            "file: %s -- compute them from disk or fix the text; refusing "
            "to commit (rsi-083 placeholder-hash mode)" %
            (len(missed), ", ".join(missed[:3]) + ("..." if len(missed) > 3
                                                   else "")))


def _append_chain_entry(cpath, hit, note, chain_fields):
    """Build and append ONE version-chain line for an adoption. Caller owns
    rollback; this raises on write failure leaving the file untouched."""
    chain_rows = load_chain(cpath)
    entry_num = max(
        (int(r.get("chain_entry") or 0) for r in chain_rows), default=0) + 1
    last = chain_rows[-1] if chain_rows else None
    prev = ("%s %s (%s)" % (last.get("winner"), last.get("date"),
                            last.get("pid"))) if last else "genesis"
    extra = dict(chain_fields or {})
    entry = {
        "winner": extra.pop("winner", None) or str(hit.get("target") or "")[:120],
        "pid": hit.get("id"),
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "prev": prev,
        "change": extra.pop("change", None) or ([note] if note else []),
    }
    for k in ("pid", "date", "prev"):    # computed identity is not overridable
        extra.pop(k, None)
    entry.update(extra)                   # rollback / evidence / next_candidates
    entry["chain_entry"] = entry_num
    os.makedirs(os.path.dirname(os.path.abspath(cpath)), exist_ok=True)
    with open(cpath, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry_num


def skill_card_gaps(root=None):
    """Scan the seven base skill cards' 常见打法 bullets for techniques that
    have NO fast-path detector yet. The cards are authored expert knowledge,
    so their bullet list is the highest-trust candidate registry a proposal
    can draw from. Returns [{"category", "bullet", "path"}]; stdlib only."""
    base = _project_root() if root is None else root
    out = []
    try:
        cards = os.listdir(os.path.join(base, "skills"))
    except OSError:
        return out
    for cat in BASE_CARDS:
        cpath = os.path.join(base, "skills", "%s.md" % cat)
        if not os.path.exists(cpath):
            continue
        try:
            with open(cpath, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        # bullets inside the 常见打法 section only (a ## heading ends it;
        # headers may carry parentheticals like "常见打法（离线）"). Wrapped
        # bullets continue on indent-free next lines -- merge them back.
        m = re.search(r"^##\s*常见打法[^#\n]*$(.*?)(?=^##|\Z)",
                      text, re.MULTILINE | re.DOTALL)
        if not m:
            continue

        def _push(b):
            """Normalize one bullet (bold markers stripped) and append it if
            it survives every gate: covered-detector, boundary-token, and
            interactive-service families all kill the WHOLE bullet."""
            label = b.strip().strip("*").strip().replace("**", "")
            if len(label) < 8 or _covered_by_detector(label):
                return
            if any(tok in label for tok in EXCLUDE_BULLET_TOKENS):
                return
            if _is_interactive(label):
                return
            out.append({"category": cat, "bullet": label[:80],
                        "path": os.path.relpath(cpath, base)})

        buf = None
        for raw_line in m.group(1).splitlines():
            s = raw_line.strip()
            if not s:
                continue
            if s.startswith("-"):
                if buf is not None:
                    _push(buf)
                buf = s.lstrip("-•* ").strip(":：").strip()
            elif buf is not None:
                buf += " " + s
        if buf is not None:
            _push(buf)
    return out


def signal_gather(store, root=None):
    """Pull the raw signal set from the live trajectory connection plus
    the skill-card technique gap scan (`root` scopes both reads)."""
    sig = {"fixations": [], "errors": {}, "bycat": [], "echoes": 0,
           "card_gaps": []}
    try:
        sig["card_gaps"] = skill_card_gaps(root)
    except Exception:
        pass
    try:
        sig["fixations"] = store.fixation_candidates()
    except Exception:
        pass
    try:
        sig["errors"] = store.error_breakdown()
    except Exception:
        pass
    conn = getattr(store, "conn", None)
    if conn is not None:
        try:
            rows = conn.execute("""
                SELECT category, COUNT(*) AS total, SUM(solved) AS solved
                FROM challenges WHERE COALESCE(mode,'') <> 'learn'
                GROUP BY category ORDER BY total DESC
            """).fetchall()
            sig["bycat"] = [{"cat": r[0], "total": r[1],
                             "solved": r[2] or 0} for r in rows]
            sig["echoes"] = conn.execute(
                "SELECT COUNT(*) FROM attempts "
                "WHERE error_class = 'semantic:raw-echo'").fetchone()[0]
        except Exception:
            pass
    return sig


def propose_signals(sig, k=5, seq=0, existing=None):
    """Rank and shape proposals from gathered signals. Pure function:
    same signals + same seq -> same proposal set (idempotent re-runs).
    `seq` seeds the stable proposal ids; `existing` is the set of
    (type, target) keys already in the proposal stream, so repeated cycles
    never append the same proposal again."""
    props = []

    # 1) detectors from fixations: top by hits+successes
    for fx in list(sig.get("fixations") or []):
        score = fx["hits"] + fx["successes"]
        if score < 3:
            continue
        props.append({
            "type": "detector",
            "target": "%s/%s" % (fx["category"], fx["technique"]),
            "evidence": {"hits": fx["hits"], "successes": fx["successes"],
                         "rows": fx["rows"]},
            "why": ("fixation trigger reached (hits+successes=%d): compile "
                    "into a fast-path detector" % score),
            "gate": "eval-suite (smoke floor + retrieval 5pp + cost ceiling)",
        })

    # 1b) detectors from skill-card technique gaps: the cards' 常见打法
    # bullets that name no existing fast-path detector. Card-authored
    # candidates get an explicit card pointer; nothing is implemented here.
    for gap in sig.get("card_gaps") or []:
        props.append({
            "type": "detector",
            "target": "%s/%s" % (gap["category"], gap["bullet"][:60]),
            "evidence": {"card_path": gap["path"], "bullet": gap["bullet"]},
            "why": ("skill card lists this technique with no fast-path "
                    "detector yet: candidate for engine-owned "
                    "implementation (human implements + eval-suite admits)"),
            "gate": "eval-suite (smoke floor + retrieval 5pp + cost ceiling)",
        })

    # 2) prompt-fix from failure hot spots: classes that dominate failures
    errs = sorted((sig.get("errors") or {}).items(),
                  key=lambda kv: -(kv[1]["count"] - kv[1]["success"]))
    for cls, stats in errs:
        # "ok" is the default class for successful exec attempts; its
        # count-success delta is not a failure mode (audit 2026-11-16).
        if cls == "ok" or cls.startswith("exec:ok"):
            continue
        failed = stats["count"] - stats["success"]
        if failed >= ERR_FAIL_MIN:
            props.append({
                "type": "prompt-fix",
                "target": cls,
                "evidence": {"count": stats["count"], "failed": failed},
                "why": ("failure class '%s' accumulated %d failures -- deep "
                        "channel likely blind to it" % (cls, failed)),
                "gate": "eval-suite + 3-challenge spot check",
            })

    # 3) card-thicken: weakest qualifying category (lowest rate first, not
    # the first one in total-order -- crypto 3.8% must beat forensics 7.9%)
    weak = None
    ranked = sorted(sig.get("bycat") or [],
                    key=lambda c: c["solved"] / max(c["total"], 1))
    for c in ranked:
        rate = c["solved"] / max(c["total"], 1)
        if rate <= CARD_RATE_MAX and c["total"] >= CARD_TOTAL_MIN:
            weak = c
            break
    if weak is not None:
        rate = weak["solved"] / max(weak["total"], 1)
        props.append({
            "type": "card-thicken",
            "target": weak["cat"],
            "evidence": {"total": weak["total"], "solved": weak["solved"],
                         "rate": round(rate, 2)},
            "why": ("category '%s' solve rate %.0f%% with %d challenges -- "
                    "skill card likely missing techniques" %
                    (weak["cat"], rate * 100, weak["total"])),
            "gate": "eval-retrieval + 3 new writeups",
        })

    # 4) flagcheck-tighten: raw-echo ratio above threshold
    att = sum((v["count"] for v in (sig.get("errors") or {}).values()))
    if att and sig.get("echoes", 0) > 0:
        echo_rate = sig["echoes"] / att
        if echo_rate >= ECHO_RATE_MIN:
            props.append({
                "type": "flagcheck-tighten",
                "target": "flagcheck.require_derived",
                "evidence": {"echo_rows": sig["echoes"],
                             "error_rows": att,
                             "rate": round(echo_rate, 4)},
                "why": ("%.1f%% of error-classified attempts are raw-echo "
                        "(%d rows) -- provenance surface needs review" %
                        (echo_rate * 100, sig["echoes"])),
                "gate": "eval-suite + known-answer challenge set",
            })

    existing = existing or set()
    seen, deduped = set(), []
    for p in props:
        key = _proposal_key(p)
        if key in existing or key in seen or _redundant_detector(p):
            continue
        seen.add(key)
        deduped.append(p)
    picked = _pick_balanced(deduped, k)
    for i, p in enumerate(picked):
        p["id"] = "rsi-%03d" % (seq + i + 1)
    return picked


def cycle(cfg=None, store=None, k=5, log=print, root=None):
    """One RSI cycle: gather -> propose -> append to proposals.jsonl.
    Never touches detectors/cards/prompts. Returns the proposal list.
    `root` redirects the ledger under a scratch dir (selftest isolation)."""
    paths = ledger_paths(root)
    if store is None:
        from memory.trajstore import TrajStore
        db = os.environ.get("CTFPILOT_DB") or os.path.join(
            _project_root(), str((cfg or {}).get("memory", {}).get(
                "db_path", "memory/trajectories.sqlite")))
        store = TrajStore(db)
    stream = load_proposal_stream(paths["proposals"])
    seq = len(stream)  # stable ids; re-cycles append, never mutate old rows
    existing = {_proposal_key(p) for p in stream}
    sig = signal_gather(store, root=root)
    props = propose_signals(sig, k=k, seq=seq, existing=existing)
    os.makedirs(os.path.dirname(os.path.abspath(paths["proposals"])),
                exist_ok=True)
    with open(paths["proposals"], "a", encoding="utf-8") as f:
        for p in props:
            rec = dict(p)
            rec["status"] = "proposed"
            rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log("[rsi] cycle done: %d new proposals (stream: %s)" %
        (len(props), paths["proposals"]))
    for p in props:
        log("  [%s] %-16s %s" % (p["type"], p["target"], p["why"]))
    return props


def transition(pid, action, note="", root=None, chain_fields=None,
               candidate_files=None, incumbent_files=None):
    """Ledger state machine: approve / reject / adopt. Writes ONE row in
    ledger.json with history; proposals.jsonl stays append-only forever.

    [evolve-20261117-10] adoption is a guarded dual-file write: the
    version-chain line and the ledger row commit together (all-or-nothing,
    byte-faithful rollback snapshots, verified by re-parsing both files
    after commit). rsi-067..073 lost seven chain entries to an unguarded
    hand append; this is the only adoption write path now. Re-adopting a
    pid that already carries a chain line appends ledger history only,
    never a duplicate chain entry.
    [evolve-20261117-15] the ledger row commits WITH its chain_entry
    annotation (previously set only after the save, so it never persisted
    -- the rsi-081 audit chased a phantom 'ledger bypass' partly because
    of this gap), and the post-commit verify re-parses the DISK row to
    require it. Single book: the canonical version-chain.jsonl is the one
    adoption chain; no mirror copy is written or maintained by code.
    [evolve-20261117-16] adoption can carry the round's REAL files: they
    are snapshotted durably under skills/rsi/snapshots/<pid>/ (never
    .temp/ -- rsi-078/079 snapshots there are already gone) and every
    sha256 the rollback text CLAIMS must match one of them (the rsi-083
    first pass committed an uncomputed placeholder hash and the adoption
    had to be rolled back and re-run). Opt-in: passing no files keeps
    the incumbent semantics byte-for-byte."""
    if action not in ("approved", "rejected", "adopted"):
        raise ValueError("action must be approved|rejected|adopted")
    paths = ledger_paths(root)
    cpath = chain_path(root)
    data = load_ledger(paths["ledger"])
    hit = next((p for p in data["proposals"] if p.get("id") == pid), None)
    if hit is None:
        hit = next((p for p in load_proposal_stream(paths["proposals"])
                    if p.get("id") == pid), None)
    if hit is None:
        return None
    hit["status"] = action
    hit.setdefault("history", [])
    hit["history"].append({"step": action, "note": note,
                           "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
    if hit not in data["proposals"]:
        data["proposals"].append(hit)
    if action != "adopted" or chain_has_pid(cpath, pid):
        # approve/reject stay ledger-only; a re-adopt whose chain line is
        # already on disk must not duplicate it (history only).
        save_ledger(data, paths["ledger"])
        return hit
    # -- [evolve-20261117-16] pre-commit face: durable snapshots first,
    # hash guard second; BOTH run before any ledger/chain byte is
    # touched, so a guard refusal leaves the books untouched (snapshot
    # files may persist from a refused attempt -- idempotent, harmless).
    base = root or _project_root()
    snap_spec = {}
    if candidate_files:
        snap_spec["candidate_files"] = candidate_snapshot(
            base, pid, "candidate", candidate_files)
    if incumbent_files:
        snap_spec["incumbent_files"] = candidate_snapshot(
            base, pid, "incumbent", incumbent_files)
    if snap_spec:
        real_hashes = [f["sha256"] for fs in snap_spec.values()
                       for f in fs]
        rollback_hash_guard((chain_fields or {}).get("rollback"), real_hashes)
        chain_fields = dict(chain_fields or {})
        chain_fields.update(snap_spec)   # computed identity wins over hand-
        # typed fields: the chain row must describe what is actually on disk
    # -- adoption: chain first, ledger second, both verified or both restored --
    chain_rows_n = len(load_chain(cpath))
    snap_c = _snap(cpath)
    snap_l = _snap(paths["ledger"])
    try:
        entry_num = _append_chain_entry(cpath, hit, note, chain_fields)
        # [evolve-20261117-15] the annotation commits WITH the ledger row:
        # set it before the save so the disk row carries its chain_entry
        # (incumbent set it only after save_ledger, so it never persisted)
        hit["chain_entry"] = entry_num
        save_ledger(data, paths["ledger"])
    except Exception:
        _restore(cpath, snap_c)
        _restore(paths["ledger"], snap_l)
        raise
    grown = load_chain(cpath)
    ok = (len(grown) == chain_rows_n + 1
          and grown[-1].get("pid") == pid
          and int(grown[-1].get("chain_entry") or 0) == entry_num)
    led = load_ledger(paths["ledger"])
    ok = ok and any(p.get("id") == pid and p.get("status") == "adopted"
                    and int(p.get("chain_entry") or 0) == entry_num
                    for p in led.get("proposals", []))
    if not ok:
        _restore(cpath, snap_c)
        _restore(paths["ledger"], snap_l)
        raise RuntimeError("adoption dual-write verify failed for %s; "
                           "both files restored to pre-write state" % pid)
    return hit


def list_proposals(status=None, limit=50, root=None):
    paths = ledger_paths(root)
    rows = load_proposal_stream(paths["proposals"])
    led = load_ledger(paths["ledger"])
    # ledger rows override stream status (human decisions win over stale 'proposed')
    led_by_id = {p["id"]: p for p in led.get("proposals", [])}
    for i, p in enumerate(rows):
        lp = led_by_id.get(p["id"])
        if lp:
            rows[i] = {**p, **{k: lp[k] for k in ("status", "history")
                               if k in lp}}
    if status:
        rows = [p for p in rows if p.get("status") == status]
    rows.sort(key=lambda p: str(p.get("ts") or ""), reverse=True)
    return rows[:limit]