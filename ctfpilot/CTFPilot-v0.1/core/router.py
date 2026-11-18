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

# [evolve-20261117-11] Wrapper redirection byproducts: the feed/arena skill
# templates redirect solve stdout/stderr into these fixed names INSIDE the
# challenge dir. They are runtime artifacts, never challenge input. Scanning
# them burns attempts (live: 2026-LanqiaoCup-Finals ~25/71 attempts on
# .solve.log, 2024-dfjk-Pwn a whole caesar/xor batch + ext=.log vote dragged
# a pwn-family dud to forensics) and their ext=.log entry pollutes the
# category evidence. Every observation face must exclude them; the name set
# is exact on purpose -- a challenge shipping a file literally named
# .solve.log would still be honored by a rename, and over-broad prefixes
# risk eating real attachments.
WRAPPER_BYPRODUCTS = frozenset({".solve.log", ".solve.err"})

# [evolve-20261117-19 / rsi-089] Archive-member carrier inspection. RED
# (live, this feed round): 2023-ISCC-Group-stage-match-PWN私地附件 -- a
# zip whose members are register (ELF64 EXEC) + libc.so.6 (glibc 2.27)
# -- routed misc conf=0.75 on zip magic/ext votes alone, because the
# carrier's MEMBERS are invisible to every observation face (inventory
# prunes _extracted/ by anti-self-solve design, and no face ever opens the
# archive). Every remote-pwn attachment archive in the source family
# (libc-shipping pwn zips) therefore misattributes to misc, corrupting
# the fixation-family accounting (bump_success is keyed by category) and
# the deep-path card alignment. Fix: ONE new read-only face -- list the
# members of stdlib-readable archives (zipfile / tarfile), and when a
# SMALL archive (<= ARCHIVE_MEMBER_CAP members) carries BOTH a libc/ld
# shared object AND a non-lib ELF binary, vote pwn with
# PWN_CARRIER_VOTE: weight 4 must clear the archive's own misc votes
# (magic 2 + ext 1) outright, no tie-break luck. Guards, each with a
# live fixture: member count (a /lib dump or image-layer tarball never
# votes), lib-alone never votes (a pure-lib archive is a library set,
# not a challenge binary), the ELF member must not itself be the lib
# (name-checked), ELF is verified by CONTENT (4-byte magic), any read
# error degrades to no vote (corrupted carriers unchanged). rar/7z have
# no stdlib reader -- documented no-op this round. A standalone reverse
# crackme zip (ELF member, NO lib member) stays misc: single-variable
# scope, that family is a separate candidate.
ARCHIVE_MEMBER_CAP = 12
PWN_CARRIER_VOTE = 4
_LIB_MEMBER_RE = re.compile(r"(^|/)(libc\.so|ld-[a-z0-9._-]*\.so)",
                            re.IGNORECASE)


def _archive_members(path, magic):
    """Member-name list for stdlib-readable archives; [] on any failure.
    Read-only: zipfile/tarfile listing only, no extraction."""
    try:
        low = path.lower()
        if magic == "zip":
            import zipfile
            with zipfile.ZipFile(path) as z:
                return z.namelist()
        if magic == "gzip" or low.endswith((".tar", ".tgz", ".tar.gz")):
            import tarfile
            with tarfile.open(path) as t:
                return t.getnames()
    except Exception:
        return []
    return []


def _member_is_elf(path, name, magic):
    """True iff archive member `name` starts with the ELF magic.
    Content check -- a member NAMED like a binary is not trusted."""
    try:
        if magic == "zip":
            import zipfile
            with zipfile.ZipFile(path) as z:
                with z.open(name) as m:
                    return m.read(4) == b"\x7fELF"
        low = path.lower()
        if magic == "gzip" or low.endswith((".tar", ".tgz", ".tar.gz")):
            import tarfile
            with tarfile.open(path) as t:
                f = t.extractfile(name)
                if f is None:
                    return False
                return f.read(4) == b"\x7fELF"
    except Exception:
        return False
    return False


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
    """Return list of file records with basic typing info.

    Engine scratch spaces (_extracted/, _extract/, .temp/, __pycache__/) are
    pruned so a rerun does not "solve" by reading its own previous extraction
    residue."""
    files = []
    for root, dirs, names in os.walk(chall_dir):
        # [evolve-20261117-21 / rsi-091] _extract/ is the LEGACY extraction
        # dir name (pre-_extracted era); a production dir still carries it
        # (2023-Bluehat-Quals-pwn: _extract/heapSpary/{libc.so.6,main} +
        # _extract/takeway/takeway, byte-identical to its _extracted
        # siblings). Incumbent RED: the residue fed every observation face
        # as if it were original attachments -- 3 magic=elf votes and a
        # 2.26MB libc entered the router evidence AND the solve input list
        # (do_solve feeds routing["files"] to both channels), so every
        # rerun re-processed stale engine output. Name-exact like
        # _extracted: a challenge shipping a real attachment named
        # _extract keeps it by a rename.
        dirs[:] = [d for d in dirs if d not in
                   ("_extracted", "_extract", ".temp", "__pycache__")]
        for name in names:
            if name.startswith("_ctfpilot") or name == "flag.txt" \
                    or name in WRAPPER_BYPRODUCTS:
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

    # [evolve-20261117-19 / rsi-089] archive-member pwn-carrier vote:
    # small zip/tar carriers shipping BOTH a libc/ld shared object and a
    # non-lib ELF binary are the remote-pwn attachment pattern (see the
    # block comment at the helper definitions). One vote per archive,
    # guards inside.
    for f in files:
        if f["magic"] not in ("zip", "gzip") \
                and not f["abs"].lower().endswith((".tar", ".tgz",
                                                   ".tar.gz")):
            continue
        names = _archive_members(f["abs"], f["magic"])
        if not 0 < len(names) <= ARCHIVE_MEMBER_CAP:
            continue
        libs = [n for n in names if _LIB_MEMBER_RE.search(n)]
        if not libs:
            continue
        elfs = [n for n in names
                if not _LIB_MEMBER_RE.search(n)
                and _member_is_elf(f["abs"], n, f["magic"])]
        if not elfs:
            continue
        # [evolve-20261117-22 / rsi-092] A carrier archive's OWN opaque-zip
        # misc votes (magic 2 + ext 1) are superseded by its members: the
        # file is a pwn attachment container, not an opaque misc blob.
        # Live RED (rsi-091 pool diff): 2023-Bluehat-Quals-pwn ships two
        # zips, one of them a carrier -- misc 6 (2 zips x 3) beat pwn 4,
        # so the dir routed misc even after the residue votes were pruned.
        # Remove exactly THIS archive's own misc contributions; every other
        # archive keeps its votes (the subtraction is per-file, not global).
        _drop = 0
        if {"zip": "misc", "gzip": "misc"}.get(f["magic"]) == "misc":
            _drop += 2
        if EXT_MAP.get(f["ext"]) == "misc":
            _drop += 1
        if _drop:
            votes["misc"] = max(0, votes.get("misc", 0) - _drop)
        vote("pwn", PWN_CARRIER_VOTE,
             "carrier %s: %d members, lib %s + ELF %s (own misc votes -%d)" %
             (f["path"], len(names), libs[0], elfs[0], _drop))

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
