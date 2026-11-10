"""Challenge triage / router.

Stage 1 (deterministic, free): file inventory + magic bytes + extension
fingerprints -> category + confidence + evidence.
Stage 2 (optional): LLM confirms or overrides using a compact text preview.
Category drives which skill card the deep path loads.
"""
import os
import re

MAGIC = [
    (b"\x1f\x8b", "gzip"),
    (b"PK\x03\x04", "zip"),
    (b"\x89PNG", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF8", "gif"),
    (b"%PDF", "pdf"),
    (b"7z\xbc\xaf", "7z"),
    (b"Rar!", "rar"),
    (b"\xd4\xc3\xb2\xa1", "pcap"),
    (b"\x0a\x0d\x0d\x0a", "pcapng"),
    (b"-----BEGIN", "pem-key"),
    (b"\x7fELF", "elf"),
    (b"MZ", "pe-exe"),
]

EXT_MAP = {
    ".pcap": "forensics", ".pcapng": "forensics",
    ".png": "forensics", ".jpg": "forensics", ".jpeg": "forensics",
    ".gif": "forensics", ".bmp": "forensics", ".wav": "forensics",
    ".zip": "misc", ".rar": "misc", ".7z": "misc", ".gz": "misc",
    ".py": "reverse", ".c": "reverse", ".cpp": "reverse",
    ".exe": "reverse", ".elf": "pwn", ".so": "reverse", ".bin": "reverse",
    ".apk": "reverse",
    ".sql": "web", ".php": "web", ".html": "web", ".js": "web",
    ".pem": "crypto", ".key": "crypto",
    ".txt": "misc", ".md": "misc", ".log": "forensics",
    ".enc": "crypto", ".crypt": "crypto",
}

TEXT_HINTS = [
    (r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY", "crypto"),
    (r"[0-9a-fA-F]{128,}", "crypto"),
    (r"n\s*=\s*[0-9e\s]+", "crypto"),
    (r"(SELECT|UNION|information_schema|' or '1'='1)", "web"),
    (r"(http://|https://)[^\s]+", "web"),
    (r"(sqlite|mysql|postgres)", "web"),
]


def _read_head(path, n=8):
    with open(path, "rb") as f:
        return f.read(n)


def _text_preview(path, n=4000):
    try:
        with open(path, "rb") as f:
            raw = f.read(n)
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def sniff_magic(path):
    head = _read_head(path)
    for magic, name in MAGIC:
        if head.startswith(magic):
            return name
    return None


def inventory(chall_dir, max_files=50):
    """Return list of file records with basic typing info."""
    files = []
    for root, _dirs, names in os.walk(chall_dir):
        for name in names:
            if name.startswith("_ctfpilot") or name == "flag.txt":
                continue
            p = os.path.join(root, name)
            rel = os.path.relpath(p, chall_dir)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = -1
            files.append({
                "path": rel,
                "abs": p,
                "size": size,
                "magic": sniff_magic(p),
                "ext": os.path.splitext(name)[1].lower(),
            })
            if len(files) >= max_files:
                return files
    return files


def route(chall_dir, llm=None):
    """Return dict(category, confidence, evidence, files, summary)."""
    files = inventory(chall_dir)
    evidence = []
    votes = {}

    def vote(cat, weight, why):
        votes[cat] = votes.get(cat, 0) + weight
        evidence.append("[%s] %s" % (cat, why))

    for f in files:
        if f["magic"]:
            cat = {"png": "forensics", "jpg": "forensics", "gif": "forensics",
                   "pcap": "forensics", "pcapng": "forensics",
                   "zip": "misc", "gzip": "misc", "7z": "misc", "rar": "misc",
                   "pdf": "forensics",
                   "pem-key": "crypto", "elf": "pwn", "pe-exe": "reverse",
                   }.get(f["magic"], "misc")
            vote(cat, 2, "magic=%s in %s" % (f["magic"], f["path"]))
        if f["ext"] in EXT_MAP:
            vote(EXT_MAP[f["ext"]], 1, "ext=%s" % f["ext"])

    # Text content hints (only for smallish files)
    for f in files:
        if 0 <= f["size"] <= 200_000 and f["magic"] in (None,):
            txt = _text_preview(f["abs"])
            for pat, cat in TEXT_HINTS:
                if re.search(pat, txt, re.IGNORECASE):
                    vote(cat, 2, "content hint in %s" % f["path"])
                    break

    if votes:
        cat = max(votes, key=votes.get)
        conf = min(0.95, votes[cat] / max(1, sum(votes.values()) + 1.0))
    else:
        cat, conf = "misc", 0.2

    summary = build_summary(files, chall_dir)
    return {"category": cat, "confidence": round(conf, 2),
            "evidence": evidence, "files": files, "summary": summary}


def build_summary(files, chall_dir, per_file_chars=600):
    lines = []
    for f in files:
        line = "- %s (%d bytes" % (f["path"], f["size"])
        if f["magic"]:
            line += ", magic=%s" % f["magic"]
        line += ")"
        if f["size"] <= 200_000 and f["magic"] in (None, "pe-exe"):
            txt = _text_preview(f["abs"], per_file_chars)
            if txt.strip():
                line += " :: " + txt.replace("\n", " \\n ")[:per_file_chars]
        lines.append(line)
    return "\n".join(lines)


def llm_confirm(routing, llm):
    """Optional LLM confirmation of the category; only used when available."""
    if llm is None:
        return routing
    sys_p = ("You are a CTF challenge classifier. Reply with ONE word category "
             "from: crypto, web, pwn, reverse, forensics, misc, osint.")
    user = ("Rule-based guess: %s (confidence %.2f)\nEvidence:\n%s\n\n"
            "Which category is this challenge?" %
            (routing["category"], routing["confidence"],
             "\n".join(routing["evidence"][:10])))
    res = llm.chat(sys_p, user, max_tokens=16, temperature=0.0)
    if res.ok and res.text:
        guess = res.text.strip().split()[0].strip(".,").lower()
        if guess in ("crypto", "web", "pwn", "reverse", "forensics", "misc", "osint"):
            if guess != routing["category"]:
                routing["llm_override"] = guess
                routing["category"] = guess
                routing["confidence"] = 0.6
    return routing
