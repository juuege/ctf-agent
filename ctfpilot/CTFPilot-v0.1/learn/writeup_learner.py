"""Writeup learner: turn a writeup (markdown/text) into reusable assets.

Outputs:
 1. skill card JSON  -> skills/learned/<name>.json
    (technique, when_to_use, category, key commands)
 2. checkpoint list  -> stored in SQLite against the challenge name, so a
    future deep run on the same/similar challenge can score partial progress
    (DeepRed-style partial credit, arXiv 2604.19354).
"""
import json
import os
import re

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


def parse_writeup(text):
    techniques = []
    for pat, cat, name in TECH_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            techniques.append((cat, name))

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

    # Category = majority vote of technique categories, else by hints
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
            "commands": commands, "steps": steps, "headings": headings}


def save_skill_card(name, parsed, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    cards = []
    for cat, tech in parsed["techniques"][:6]:
        cards.append({
            "name": name,
            "category": cat,
            "technique": tech,
            "when_to_use": "challenge looks like %s; recall from writeup '%s'" % (cat, name),
            "commands": parsed["commands"][:10],
        })
    for i, card in enumerate(cards):
        path = os.path.join(out_dir, "%s-%02d.json" % (name, i))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, indent=2)
    return [os.path.join(out_dir, "%s-%02d.json" % (name, i))
            for i in range(len(cards))]
