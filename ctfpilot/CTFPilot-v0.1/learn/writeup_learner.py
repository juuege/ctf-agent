"""Writeup learner: turn a writeup (markdown/text) into reusable assets.

Outputs:
  1. skill card JSON  -> skills/learned/<name>[-vN]-NN.json
     (technique, when_to_use, category, key commands, signals, failure paths,
      structured tags 识别信号/陷阱类型/可迁移模式/变体预测; versioned, never
      overwrites old cards)
  2. checkpoint list  -> stored in SQLite against the challenge name, so a
     future deep run on the same/similar challenge can score partial progress
     (DeepRed-style partial credit, arXiv 2604.19354).

The SKILL.md writeup template (references/ctfpilot-rsi.md §2) is parsed
structurally: 题型/题面特征/解题路径/有效技术/失败路径/适用限制/未测试/标签/可固化性
all land in the card and in the lesson row. Before this, only regex keyword
matching happened and every template field was silently dropped.
"""
import json
import os
import re
import time

TECH_PATTERNS = [
    (r"rsa|质数|private key|modulus|公钥|私钥", "crypto", "RSA modulus/factoring analysis"),
    (r"z3|satisfiability|约束求解|solve\(", "crypto", "constraint solving with z3"),
    (r"\biq\b|complex64|bpsk|qpsk|mfsk|ofdm|sps\s*=|cfo|载波偏置|解调|调制识别|viterbi|维特比|卷积码|constellation|星座", "misc", "IQ signal demodulation (BPSK/MFSK, self-describing bootstrap, Viterbi)"),
    (r"sql\s*inject|union\s*select|information_schema", "web", "SQL injection (union-based)"),
    (r"xss|<script", "web", "XSS payload crafting"),
    (r"ssrf|gopher://|dict://", "web", "SSRF scheme abuse"),
    (r"deserial|pickle|unserialize|phar", "web", "insecure deserialization"),
    (r"rop|ret2|stack\s*overflow|gadget", "pwn", "ROP / ret2* chain"),
    (r"format\s*string|%p|printf", "pwn", "format string leak"),
    (r"heap|tcache|fastbin|double\s*free", "pwn", "heap exploitation (glibc bins)"),
    (r"ghidra|\bida\b|decompil| pseudocode ", "reverse", "static decompilation review"),
    (r"upx|unpack|esp\b", "reverse", "unpacking packed binary"),
    (r"steg(hide|solve|solved)?\b|lsb|隐写", "forensics", "steganography extraction"),
    (r"wireshark|pcap|tshark|流量", "forensics", "pcap traffic analysis"),
    (r"volatility|mem(map|dump)|内存取证", "forensics", "memory forensics"),
    (r"foremost|binwalk|文件分离|修复", "misc", "file carving / repair"),
    (r"aes.{0,4}(cbc|128|192|256)|硬编码|hardcoded.?key|嵌入式.?key", "crypto", "AES CBC decryption with extracted hardcoded key (ECB/CBC0/CBC-IV-prefix triage)"),
    (r"squashfs|unsquash|固件|firmware", "reverse", "firmware unpacking (layer peel gzip->squashfs, stdlib superblock walk)"),
    (r"zip(密码|password)|crc(32)?\s*(碰撞|collid)|known.?plaintext", "misc", "archive password recovery / crc collision"),
]

CODE_BLOCK = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)
CMD_LINE = re.compile(r"^\s*(?:\$|>|#)?\s*([a-z][\w.\-]*(?:\s+[^\n]*)?)$",
                      re.MULTILINE)
STEP_LINE = re.compile(r"^\s*(?:step|步骤)\s*[:：]?\s*(.+)$", re.MULTILINE | re.IGNORECASE)
HEADING = re.compile(r"^#{1,4}\s+(.+)$", re.MULTILINE)

# Writeup label formats supported (SOTA ingestion, 2026-11):
#   A. SKILL.md template:   "- 题面特征: ..." / "标签: 识别信号=...；陷阱类型=..."
#   B. "## 标签" colon style:      "- 识别信号: ..." "- 核心技术链: ..."
#   C. "## 标签（七维）" numbered: "1. 题型: ..." "3. 识别信号: ..."
FIELD_ALIASES = [
    ("题型", "题型"),
    ("分类", "题型"),
    ("题目", "题目"),
    (r"载体(?:\s*/\s*格式)?", "载体"),
    (r"识别信号|题面特征|样本特征", "识别信号"),
    (r"核心技术链|技术链|解题路径", "核心技术链"),
    ("有效技术", "有效技术"),
    (r"陷阱(?:类型)?", "陷阱类型"),
    (r"可迁移(?:模式)?", "可迁移模式"),
    (r"变体(?:预测)?", "变体预测"),
    (r"失败原因(?:\s*/\s*边界)?|失败路径", "失败原因"),
    ("适用限制", "适用限制"),
    ("未测试", "未测试"),
    ("可固化性", "可固化性"),
    (r"结果|判定", "结果"),
    ("[Ff][Ll][Aa][Gg]", "flag"),
]
FIELD_RE = re.compile(
    r"^[ \t]*(?:[-*•]\s*)?(?:\*\*)?(?:\d+[.、)]\s*)?(%s)(?:\*\*)?\s*[:：]\s*(.+?)\s*$" %
    "|".join(a for a, _ in FIELD_ALIASES),
    re.MULTILINE)
SECTION_RE = re.compile(
    r"^#{1,4}\s*(一得|失败原因(?:\s*/\s*边界)?|题解学习(?:要点)?|样本特征|尝试路径)[^\n]*$\n(.+?)(?=\n#{1,4}\s|\Z)",
    re.MULTILINE | re.DOTALL)
TAG_KV_RE = re.compile(r"(识别信号|陷阱类型|可迁移模式|变体预测)\s*[=＝]\s*([^；;]+)")


def _clean(v):
    return re.sub(r"[*`]+", "", (v or "")).strip()

CAT_MAP = {"misc": "misc", "crypto": "crypto", "web": "web", "pwn": "pwn",
           "reverse": "reverse", "forensics": "forensics", "osint": "osint",
           "杂项": "misc", "密码": "crypto", "密码学": "crypto", "逆向": "reverse",
           "取证": "forensics", "流量": "forensics", "隐写": "forensics",
           "信号": "misc"}


def _safe_filename(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "writeup"


def parse_writeup(text):
    techniques = []
    for pat, cat, name in TECH_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            techniques.append((cat, name))

    # --- structured fields: four label formats (A/B/C/D above) ---
    # D (循环任务报告) packs several fields per line separated by the
    # full-width bar ｜ and bolds labels: "- **分类**：Reverse ｜ **结果**：NOT SOLVED"
    norm = text.replace("｜", "\n")
    fields = {}
    for m in FIELD_RE.finditer(norm):
        raw = m.group(1)
        for pat, cname in FIELD_ALIASES:
            if re.fullmatch(pat, raw):
                fields[cname] = _clean(m.group(2))
                break
    # --- section blocks without labels (一得 / 失败原因 / 样本特征 / 尝试路径) ---
    for m in SECTION_RE.finditer(text):
        head = m.group(1)
        key = ("失败原因" if head.startswith("失败") else
               "识别信号" if head.startswith("样本") else
               "尝试路径" if head.startswith("尝试") else
               "一得" if head == "一得" else "题解要点")
        if key not in fields:
            lines = []
            for l in m.group(2).splitlines():
                l = l.strip()
                if not l or l.startswith("#"):
                    continue
                if l.startswith("|"):
                    cells = [c.strip() for c in l.strip("|").split("|")]
                    cells = [c for c in cells
                             if c and not set(c) <= {"-", ":", " "}]
                    if cells:
                        lines.append("：".join(cells))
                else:
                    lines.append(_clean(re.sub(r"^[\s\-*•\d.、)]+", "", l)))
            fields[key] = "；".join(lines)[:400]
    # --- equals-style tags (format A) + colon/numbered fields fill-in ---
    tags = {}
    for kv in TAG_KV_RE.finditer(text):
        tags[kv.group(1)] = _clean(kv.group(2))
    for k in ("识别信号", "陷阱类型", "可迁移模式", "变体预测"):
        if not tags.get(k) and fields.get(k):
            tags[k] = fields[k]

    commands = []
    for block in CODE_BLOCK.findall(text):
        for line in block.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("import") \
                    and "print" not in line[:6]:
                commands.append(line)
    commands = commands[:20]

    steps = [m.group(1).strip() for m in STEP_LINE.finditer(text)]
    headings = [m.group(1).strip() for m in HEADING.finditer(text)]
    if not steps:
        steps = [h for h in headings if len(h) < 120][:10]

    # Category: explicit 题型 field wins over keyword voting (the vote is
    # easily poisoned by decoy words in the writeup, cf. iq-signal-playbook).
    title = fields.get("题目", "")
    name = re.split(r"[（(]", title)[0].strip() if title else ""
    category = None
    category_inferred = False
    if fields.get("题型"):
        first = re.split(r"[/／,，\s]+", fields["题型"])[0].strip().lower()
        category = CAT_MAP.get(first)
        if category is None:
            for k, v in CAT_MAP.items():
                if first.startswith(k):
                    category = v
                    break
    if category is None and title:
        pm = re.search(r"[（(]([A-Za-z]+)[）)]", title)
        if pm:
            category = CAT_MAP.get(pm.group(1).lower())
    if category is None:
        # fallback: keyword vote / text guess -- flag it so the audit and
        # recall consumers know this category was inferred, not stated.
        category_inferred = True
        if techniques:
            cats = {}
            for cat, _n in techniques:
                cats[cat] = cats.get(cat, 0) + 1
            category = max(cats, key=cats.get)
        else:
            low = text.lower()
            category = ("crypto" if any(k in low for k in ("rsa", "aes", "密码"))
                        else "web" if any(k in low for k in ("sql", "xss", "ssrf"))
                        else "pwn" if any(k in low for k in ("pwn", "rop", "栈"))
                        else "reverse" if any(k in low for k in ("ida", "ghidra", "逆向"))
                        else "forensics" if any(k in low for k in ("流量", "pcap", "隐写"))
                        else "misc")

    # outcome: 结果/判定 + flag fields
    res = fields.get("结果", "")
    flagv = _clean(fields.get("flag", ""))
    solved = None
    lowres = res.lower()
    if "solved" in lowres and "not" not in lowres and "未" not in res:
        solved = True
    elif ("未解出" in res) or ("not solved" in lowres):
        solved = False
    if flagv and flagv[:1] not in ("无", "未", "-", "—"):
        solved = True

    return {"category": category, "techniques": techniques,
            "commands": commands, "steps": steps, "headings": headings,
            "fields": fields, "tags": tags,
            "signals": fields.get("识别信号", ""),
            "effective": fields.get("有效技术", ""),
            "failure_paths": fields.get("失败原因", ""),
            "limits": fields.get("适用限制", ""),
            "untested": fields.get("未测试", ""),
            "fixable": fields.get("可固化性", ""),
            "chain": fields.get("核心技术链", ""),
            "pitfalls": fields.get("陷阱类型", ""),
            "transferable": fields.get("可迁移模式", ""),
            "variants": fields.get("变体预测", ""),
            "carrier": fields.get("载体", ""),
            "gain": fields.get("一得", ""),
            "notes": fields.get("题解要点", ""),
            "attempts": fields.get("尝试路径", ""),
            "title": title, "name": name, "solved": solved, "flag_value": flagv,
            "category_inferred": category_inferred}


def _next_version(out_dir, safe):
    """Version bump: never overwrite an existing card (旧记录不删), stale
    cards are superseded by created_at at load time."""
    v = 1
    for fn in os.listdir(out_dir):
        m = re.match(re.escape(safe) + r"(?:-v(\d+))?-_?\d{0,3}-?\d{2}\.json$", fn)
        if m:
            if m.group(1):
                v = max(v, int(m.group(1)) + 1)
            else:
                v = max(v, 2)
    return v


def save_skill_card(name, parsed, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    safe = _safe_filename(name)
    version = _next_version(out_dir, safe)
    now = time.time()
    cards = []
    for cat, tech in parsed["techniques"][:6]:
        cards.append({
            "name": name,
            "category": cat,
            "technique": tech,
            "when_to_use": (parsed.get("signals")
                            or "challenge looks like %s; recall from writeup '%s'" % (cat, name)),
            "commands": parsed["commands"][:10],
            "signals": parsed.get("signals", ""),
            "effective": parsed.get("effective", ""),
            "failure_paths": parsed.get("failure_paths", ""),
            "limits": parsed.get("limits", ""),
            "untested": parsed.get("untested", ""),
            "tags": parsed.get("tags", {}),
            "fixable": parsed.get("fixable", ""),
            "version": version,
            "created_at": now,
        })
    paths = []
    for i, card in enumerate(cards):
        if version == 1:
            fn = "%s-%02d.json" % (safe, i)
        else:
            fn = "%s-v%d-%02d.json" % (safe, version, i)
        path = os.path.join(out_dir, fn)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, indent=2)
        paths.append(path)
    return paths
