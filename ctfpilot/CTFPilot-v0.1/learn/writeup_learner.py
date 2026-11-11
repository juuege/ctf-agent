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

# SKILL.md writeup template fields (references/ctfpilot-rsi.md §2)
FIELD_RE = re.compile(
    r"^\s*[-*]?\s*(题型|题面特征|解题路径|有效技术|失败路径|适用限制|未测试|可固化性)\s*[:：]\s*(.+?)\s*$",
    re.MULTILINE)
TAG_LINE_RE = re.compile(r"^\s*[-*]?\s*标签\s*[:：]\s*(.+)$", re.MULTILINE)
TAG_KV_RE = re.compile(r"(识别信号|陷阱类型|可迁移模式|变体预测)\s*[=＝]\s*([^；;]+)")

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

    # --- structured template fields (SKILL.md 落库模板) ---
    fields = {}
    for m in FIELD_RE.finditer(text):
        fields[m.group(1)] = m.group(2).strip()
    tags = {}
    tm = TAG_LINE_RE.search(text)
    if tm:
        for kv in TAG_KV_RE.finditer(tm.group(1)):
            tags[kv.group(1)] = kv.group(2).strip()

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
    category = None
    if fields.get("题型"):
        first = re.split(r"[/／,，\s]+", fields["题型"])[0].strip().lower()
        category = CAT_MAP.get(first)
    if category is None:
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
    return {"category": category, "techniques": techniques,
            "commands": commands, "steps": steps, "headings": headings,
            "fields": fields, "tags": tags,
            "signals": fields.get("题面特征", ""),
            "effective": fields.get("有效技术", ""),
            "failure_paths": fields.get("失败路径", ""),
            "limits": fields.get("适用限制", ""),
            "untested": fields.get("未测试", ""),
            "fixable": fields.get("可固化性", "")}


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
