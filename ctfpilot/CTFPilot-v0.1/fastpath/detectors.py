"""Fast path: deterministic detectors for common challenge patterns.

Goal: "simple challenges solved in seconds, offline, no LLM".
Each attempt is recorded so the trajectory store knows what was already
tried before the deep path spends budget on it.

Detectors implemented (all pure stdlib, all offline):
  - base64 (std + urlsafe, with/without padding)
  - base32, hex, url-encoding
  - base-family chain layers (rsi-042, 2026-11-16): base92 (thenoviceoof
    fixed-13bit protocol; NOT the basE91 variable 13/14-bit protocol despite
    overlapping alphabets), ascii85 + base85 (RFC1924) via stdlib, base62
    (0-9A-Za-z bigint), base58 (bitcoin bigint), base45 (RFC 9285,
    3-chars->2-bytes groups). All charset-gated so ordinary prose never
    enters the BFS queue; source: 2024-zjsdxs sign-in 7-layer chain
    (base92>a85>b64>b62>b58>b45>b32) that the engine failed in 0.24s.
  - caesar sweep (all 26 shifts), rot13, atbash
  - reverse string
  - zlib/gzip raw inflate
  - single-byte XOR sweep (0..255)
  - chained decoding: recursive unwrap up to depth 8
  - token-key XOR: try words found inside the challenge files as XOR keys
    (this is the bridge into the deep path: hint-driven, still offline)
  - js-unicode-escape: JS string literals of >=6 consecutive \\uXXXX escapes
    (source of 2026-11-16 attachments726 solve: a var assigned a long \\uXXXX run;
    carries a lenient flag-shape fallback for `flag:{...}`-style contest
    formats that the global flag_patterns do not match)
  - mz-xor-key-recovery (2026-11-16, dfjk-resume round): multi-byte cyclic-
    XOR-encrypted PE sample. key[0..3] leaks from the `MZ\x90\x00` known-
    plaintext head; constant-plaintext runs (PE padding) leak the full cycle
    as the stride between repeated V=C^key[0..3] patterns; MZ+PE-sig double
    verify, then flag-scan the decrypted image. Self-reads files (the
    _candidate_inputs 2MB cap excludes multi-MB malware samples; source:
    2024-dfjk-Misc default.a, 7-byte key yyttddd).
  - png-ihdr-crc (rsi-054, 2026-11-16): IHDR width/height tamper recovery.
    Stored crc32 matches the REAL geometry (tampers rarely recompute the
    chunk crc), so brute-force (w,h) against it, validate every row filter
    byte is 0..5, then probe the residual stream / post-image bytes /
    head+tail unfiltered rows for flag text. Tolerates a corrupted trailing
    adler32 (source: 2025-jswa-misc2, stored 2048x864 real 1536x864, fake
    checksum bytes keep all pixel data decompressable).
"""
import base64
import binascii
import gzip
import os
import re
import string
import struct
import time
import zlib

from fastpath.rsa_numeric import rsa_numeric_scan

MAX_CHAIN_DEPTH = 8
PRINTABLE = set(bytes(string.printable, "ascii"))

# [evolve-20261117-38 / rsi-109] per-line input marker produced by
# _candidate_inputs ("%s #L%d"). Used by the caesar stage to recognize a
# per-line sweep and skip it when the parent whole-file sweep is clean.
_CAESAR_LINE_RE = re.compile(r"^(?P<parent>.+) #L\d+$")

# --- base-family alphabets (rsi-042) ---------------------------------------
# base92 = thenoviceoof variant used by basecrack: '!' + '#'..'_' + 'a'..'}'
# (91 chars, excludes " ` ~ and space). Decode protocol: fixed 13 bits per
# char pair (x = d1*91 + d2); a lone tail char contributes 6 bits.
B92_CHARS = frozenset("!" +
                      "".join(chr(c) for c in range(ord("#"), ord("_") + 1)) +
                      "".join(chr(c) for c in range(ord("a"), ord("}") + 1)))
B62_CHARS = frozenset(string.digits + string.ascii_uppercase +
                      string.ascii_lowercase)
B58_CHARS = frozenset("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
# RFC 9285: alnum + space $ % * + - . / :  (45 chars)
B45_CHARS = frozenset(string.digits + string.ascii_uppercase + " $%*+-./:")
B45_SPECIALS = frozenset("%*+-./:")
B85_CHARS = frozenset(string.digits + string.ascii_uppercase +
                      string.ascii_lowercase + "!#$%&()*+-;<=>?@^_`{|}~")

# --- base-family alphabets (rsi-042) ---------------------------------------
# base92 = thenoviceoof variant used by basecrack: '!' + '#'..'_' + 'a'..'}'
# (91 chars, excludes " ` ~ and space). Decode protocol: fixed 13 bits per
# char pair (x = d1*91 + d2); a lone tail char contributes 6 bits.
B92_CHARS = frozenset("!" +
                      "".join(chr(c) for c in range(ord("#"), ord("_") + 1)) +
                      "".join(chr(c) for c in range(ord("a"), ord("}") + 1)))
B62_CHARS = frozenset(string.digits + string.ascii_uppercase +
                      string.ascii_lowercase)
B58_CHARS = frozenset("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
# RFC 9285: alnum + space $ % * + - . / :  (45 chars)
B45_CHARS = frozenset(string.digits + string.ascii_uppercase + " $%*+-./:")
B45_SPECIALS = frozenset("%*+-./:")
B85_CHARS = frozenset(string.digits + string.ascii_uppercase +
                      string.ascii_lowercase + "!#$%&()*+-;<=>?@^_`{|}~")

# JS unicode-escape literals: '....' or "...." with >=6 consecutive \uXXXX (2026-11-16)
JS_UE_RE = re.compile(r"['\"]((?:\\u[0-9a-fA-F]{4}){6,})['\"]")
# Lenient contest flag shapes NOT covered by config.flag_patterns (scoped to
# this detector only, e.g. jksn platform format `flag:{ErLaiSiWanBaQianSui}`
# where a colon sits between `flag` and the braced payload)
UE_FLAG_LENIENT = re.compile(r"(?i)\bflag\s*:\s*\{[A-Za-z0-9_\-]{4,128}\}")

# [evolve-20261117-03] garbage-flag markers: U+FFFD (utf-8 decode replace
# artifact = proof of corrupted/undecoded bytes) and C0/C1 control chars.
# Their presence in a regex hit means the "flag" is decode garbage (wrong-key
# XOR output), never a challenge-provided or derived real flag.
_GARBAGE_FLAG_RE = re.compile("[\x00-\x1f\x7f-\x9f\ufffd]")

# [evolve-20261118-17 / rsi-140] decode-family flag plausibility gate.
# RED (live, round-99 pull n1ctf-2025-n1saml): xor-single brute key 0x11
# over go.mod turned the Go dependency path 'crewjam/saml' (substring
# 'rewjam/saml') into 'ctf{p|>bp|}', a regex hit on the generic ctf{...}
# pattern the engine credited as a REAL solve (0.41s fake SOLVED, store
# row 998, adjudicated unsolved the same round). Same family already in
# the store: caesar shift+2 over '.card{background-color:hsl(var(--carp))}'
# produced 'ctf{dcemitqwpf-eqnqt:jun(xct(--ectf))}' (challenge 'x' solved
# row), and chain-decode surfaced C++/JS template fragments like
# 'flag{"<<s<<number<<"}' plus author masks 'CTF{******}' / 'NCO26{.*}'.
# Census (2026-11-18, all 46 solved flags): real brace bodies span
# letters, digits and ONLY ~ - = @ # & _ . (colon-form bodies live
# outside the braces); pipe/lt/gt/quote/paren/bracket/backslash/backtick/
# semicolon/comma appear in ZERO real bodies and are exactly the
# substance of the garbage specimens. Gate = one predicate applied at the
# SAME decode-family acceptance points as _GARBAGE_FLAG_RE (never inside
# FlagChecker: user-supplied --flag-regex and non-brace finds keep full
# freedom). A brace-shaped find is implausible when (a) its body carries
# no word character at all (Unicode-aware -- mask/placeholder; the v1
# ASCII-only [A-Za-z0-9] test was caught by the round's own selftest:
# evolve36-idna-arm's real flag{进化三十五} must pass, and the incumbent
# rsi-097 garbage gate already promises non-ASCII flag text is admitted)
# or (b) its body carries a census-zero metachar. Quiz answers ('C'),
# colon forms 'flag:...:' and the lenient 'flag:{...}' bodies are
# structurally untouched; every one of the 46 census flags passes
# (selftest evolve62-flagplaus face (a)).
_FLAG_BODY_BRACE_RE = re.compile(r"\{(.*)\}", re.S)
_FLAG_BODY_WORD_RE = re.compile(r"\w")
_FLAG_BODY_META_RE = re.compile(r"[|><\"'`\\()\[\];,]")


def _implausible_flag(found):
    """True when a brace-shaped flag find cannot be a real flag body
    (mask without any Unicode word character, or census-zero code/CSS
    metachar). Non-brace-shaped finds are never touched."""
    if not found or "{" not in found or "}" not in found:
        return False
    m = _FLAG_BODY_BRACE_RE.search(found)
    if not m:
        return False
    body = m.group(1)
    if not _FLAG_BODY_WORD_RE.search(body):
        return True
    return bool(_FLAG_BODY_META_RE.search(body))

# --- mz-xor-key-recovery (2026-11-16, dfjk-resume round) ----------------------
# Multi-byte cyclic-XOR-encrypted PE sample (malware-style challenge).
# key[0..3] leaks from the known plaintext head `MZ\x90\x00`; long constant-
# plaintext runs (PE padding is one constant byte, usually 0x00) leak the full
# cycle: inside such a run the ciphertext repeats the 4-byte pattern
# V = C ^ key[0..3] every N bytes. Recover (key, N, phase) from the stride
# between consecutive V hits, verify MZ + PE\x00\x00, decrypt, flag-scan.
# Source: 2024-dfjk-Misc (7-byte key b"yyttddd", 4.6MB default.a) - engine
# previously had zero MZ-derivation attempts and _candidate_inputs caps
# files at 2MB, so the sample never even entered the fast path.
MZ_KNOWN_HEAD = b"MZ\x90\x00"
MZ_XOR_MIN_FILE = 0x200
MZ_XOR_MAX_FILE = 16 * 1024 * 1024
MZ_XOR_MAX_N = 64
MZ_XOR_FIND_BUDGET = 2048
MZ_XOR_DECRYPT_LIMIT = 8 * 1024 * 1024


class Attempt:
    def __init__(self, detector, detail, success, flag=None):
        self.detector = detector
        self.detail = detail
        self.success = success
        self.flag = flag


# [evolve-20261117-04] fastpath brute-stage time budget ----------------------
# config budget.fastpath_time_limit_s (default 60) existed since day one but
# had ZERO call sites (grep: only the definition in core/config.py). The brute
# detectors ran unbounded: live-fire 2023-hgame-week3-pwn-large_note burned
# 323s+ CPU on 40 stock-library blobs (401.5s fake-SOLVED before the evolve-03
# credibility gate; 476.5s honest full-search after it). This helper resolves
# the budget seconds for the brute stages; None = unbounded. An explicit
# value (selftest injection) wins over config; <=0 disables the budget.
def _fastpath_budget_s(explicit=None):
    if explicit is not None:
        return explicit if explicit > 0 else None
    try:
        from core.config import load_config
        v = float((load_config().get("budget") or {})
                  .get("fastpath_time_limit_s") or 0)
        return v if v > 0 else None
    except Exception:
        return None


def _flag_text(checker, data):
    """Best-effort: bytes or str -> flag if printable text contains one."""
    if isinstance(data, bytes):
        printable_ratio = sum(1 for b in data[:512] if b in PRINTABLE) / max(1, min(512, len(data)))
        if printable_ratio < 0.7:
            return None
        text = data.decode("utf-8", errors="replace")
    else:
        text = data
    found = checker.find(text)
    # [evolve-20261117-03] derived-flag credibility gate: wrong-key XOR over
    # a stock binary (0x00-heavy) leaks the key into the padding, and the
    # garbage utf-8 decode (errors="replace") splices U+FFFD runs that still
    # match the generic FLAG{...} pattern (live: large_note key
    # 'FLAG=no_FLAG' over stock libm-2.32.so, 401.5s fake SOLVED, RED replay
    # 401.6s). A real flag never carries U+FFFD or C0/C1 control chars;
    # legitimate non-ASCII flag text (rare) is still admitted.
    if found and (_GARBAGE_FLAG_RE.search(found)
                  or _implausible_flag(found)):
        return None
    return found


def _candidate_inputs(chall_dir, files):
    """Decoder inputs: whole-file text + each non-trivial line + raw bytes.
    Per-line candidates matter: real challenges embed payloads in prose.
    Same-content lines within one file dedup to the first occurrence
    (rsi-104): every per-line consumer is a pure function of line content,
    so duplicate lines only pay redundant miss scans."""
    inputs = []
    for f in files:
        if f["size"] <= 0 or f["size"] > 2_000_000:
            continue
        try:
            with open(f["abs"], "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if f["magic"] is None:
            txt = raw.decode("utf-8", errors="replace").strip()
            if txt:
                inputs.append((f["path"], txt))
                # [evolve-20261117-34 / rsi-104] per-line same-file content
                # dedup: every per-line input independently pays the full
                # downstream battery (chain stage 1 + js-unicode-escape +
                # caesar 25-shift sweep; chain_decode's seen set is
                # call-local so identical lines re-pay everything), and
                # pool-wide 26.5% of per-line inputs (93,396 / 352,750) are
                # same-content duplicates within one file. Live RED walls
                # on the incumbent functions: HuiXiang 13,861 lines -> 6
                # distinct, chain per-line 5.05 -> 0.06 s (-98.8%); AECBS
                # -88.2%; ISCC -63.6%; attachments726 -74.0%. All per-line
                # consumers are pure functions of the line content, and
                # stage-1.2 wrapped_tokens already dedups tokens
                # cross-input by content, so dropping a duplicate keeps
                # hit capability and hit position (the first occurrence
                # stays at its original slot) and only removes redundant
                # miss attempts.
                _seen_lines = set()
                for i, line in enumerate(txt.splitlines()):
                    line = line.strip()
                    if len(line) >= 8 and line != txt.strip() \
                            and line not in _seen_lines:
                        _seen_lines.add(line)
                        inputs.append(("%s #L%d" % (f["path"], i + 1), line))
        inputs.append((f["path"] + " [bytes]", raw))
    return inputs


# [evolve-20261117-27 / rsi-097] chain-decode per-node provable
# preconditions. chain_decode pays the FULL decoder battery on every BFS
# node (ISCC live: 230,095 nodes over 35,045 text inputs, 17.5 s wall;
# hex 6.10 s / b64 9.11 s / gzip-zlib 4.25 s / b32 4.17 s / a85 3.40 s of
# the cProfile pass). The gates below are NECESSARY conditions proven
# against the runtime (preflight .temp/evolve27-profile): gate failure
# means the decoder's own validation is guaranteed to reject the input,
# so skipping it is byte-identical:
#   - hex: every char must be a hex digit / \s / ',' (optional leading
#     0x/0X) or the strict fullmatch below can never pass;
#   - base32: b32decode raises for ANY char outside [A-Z2-7=];
#   - a85: a85decode raises for ANY char outside \x21-\x75, 'z' and the
#     ignorechars b' \t\n\r\v' (all 256 bytes verified);
#   - zlib header: CMF&0x0f==8 and CINFO<=7 -> first byte 0x08/0x18/.../
#     0x78 (exhaustive negative verified); gzip needs the \x1f\x8b magic.
# The b64 redundancy cuts are proven too: urlsafe_b64decode(x) ==
# b64decode(x) while x carries no '-'/'_' (it only translates those two
# first), and the padded try repeats the unpadded one verbatim when the
# computed pad is empty (len(raw) % 4 == 0). No search space is narrowed
# that the decoder itself would not reject; validate=False semantics and
# every meaningful-output filter are untouched.
_HEX_GATE_RE = re.compile(r"(?:0[xX])?[0-9a-fA-F\s,]+")
_HEX_PREFIX_SUB_RE = re.compile(r"^0x", re.IGNORECASE)
_HEX_SEP_SUB_RE = re.compile(r"[\s,]")
_HEX_STRICT_RE = re.compile(r"[0-9a-fA-F]+")
_B32_GATE_RE = re.compile(r"[A-Z2-7=]+")
_A85_GATE_RE = re.compile(r"[\x21-\x75z\x20\x09\x0a\x0b\x0d]+")
_ZLIB_CMF_FIRST = frozenset((0x08, 0x18, 0x28, 0x38, 0x48, 0x58, 0x68, 0x78))
_GZIP_MAGIC = b"\x1f\x8b"


def _try_base64(data):
    if isinstance(data, str):
        s = data.strip()
        # [evolve-20261117-42 / rsi-113] isascii pre-gate: strip() removes
        # only Unicode whitespace and replace() removes only ASCII chars
        # ('\n', ' '), never introducing new characters, so the post-replace
        # string is all-ASCII iff the post-strip one is. s.isascii() is the
        # EXACT same rejection predicate as encode('ascii') raising
        # UnicodeEncodeError (both directions provable: a non-ASCII char
        # survives both replaces so encode raises; an all-ASCII s0 yields an
        # all-ASCII s1 so encode succeeds), but it stops at the first
        # non-ASCII byte in C instead of paying two full replace scans +
        # the partial encode scan + the exception. RED (live faces, entry-61
        # incumbent, wrapper counting): the raise share is 33.4-100% of str
        # calls (ISCC 41,704/94,671 = 44.1% avg 321 chars; DeadGame
        # 226,549/335,842 = 67.5%; jksn-attachments726 96.5% avg 802
        # chars), and the would-raise nodes are on average far longer than
        # the ASCII ones (802 vs 41 on jksn, 321 vs 25 on ISCC).
        if not s.isascii():
            return None
        raw = s.replace("\n", "").replace(" ", "").encode("ascii")
    else:
        raw = data
    # [evolve-20261117-27] proven-redundant combinations skipped: the
    # padded try repeats the unpadded one when pad == b"" (len % 4 == 0),
    # and urlsafe == standard while raw carries no '-'/'_'.
    padb = b"=" * (-len(raw) % 4)
    pads = (b"",) if not padb else (b"", padb)
    alts = (False, True) if (b"-" in raw or b"_" in raw) else (False,)
    for pad in pads:
        for alt in alts:
            try:
                out = base64.b64decode(raw + pad, validate=False) if not alt \
                    else base64.urlsafe_b64decode(raw + pad)
                if out and _looks_meaningful(out):
                    return out
            except (binascii.Error, ValueError):
                continue
    return None


def _try_base32(data):
    s = data.strip().replace("\n", "").replace(" ", "") if isinstance(data, str) else None
    if not s:
        return None
    # [evolve-20261117-27] provable precondition (gate block above):
    # b32decode raises for ANY char outside [A-Z2-7=].
    if not _B32_GATE_RE.fullmatch(s):
        return None
    try:
        raw = s.encode("ascii")
    except UnicodeEncodeError:
        return None
    try:
        out = base64.b32decode(raw + b"=" * (-len(raw) % 8))
        if out and _looks_meaningful(out):
            return out
    except (binascii.Error, ValueError):
        return None
    return None


def _try_hex(data):
    s = data.strip() if isinstance(data, str) else None
    if not s:
        return None
    # [evolve-20261117-27] provable precondition (gate block above): a
    # char outside hex/\s/,/--leading-0x survives both subs and fails the
    # strict fullmatch, so a failed gate == guaranteed None.
    if not _HEX_GATE_RE.fullmatch(s):
        return None
    clean = _HEX_PREFIX_SUB_RE.sub("", s)
    clean = _HEX_SEP_SUB_RE.sub("", clean)
    if len(clean) < 8 or len(clean) % 2 or not _HEX_STRICT_RE.fullmatch(clean):
        return None
    try:
        out = bytes.fromhex(clean)
    except ValueError:
        return None
    return out if _looks_meaningful(out) else None


# [evolve-20261117-35 / rsi-107] C-level charset gates for the base-N
# decoders. RED (measured on the rsi-104 incumbent, real node streams):
# set(data) built a Python set over EVERY node reaching the decoder, and
# whole-file text nodes are huge (2025-jksn-quals-attachments726 SOLVED
# carrier: 768,181 B x 61 giant nodes; 2023-ISCC-Analysis: 767,189 B x 40).
# The four decoders paid 1.38 s of the 2.25 s fast wall on jksn / 1.42 s of
# 8.84 s on ISCC. A compiled char-class fullmatch is the EXACT same
# predicate (every char belongs to the charset) but scans in C and rejects
# giant prose at the first out-of-set char, so the set() build only happens
# on gate-passing nodes (short tokens). Identity pinned in selftest block
# evolve35-basegates (boundary + fuzz + live corpus, output byte-identical).
def _charset_gate(chars):
    return re.compile("[" + "".join(re.escape(c) for c in sorted(chars)) + "]+")


_B62_GATE_RE = _charset_gate(B62_CHARS)
_B58_GATE_RE = _charset_gate(B58_CHARS)
_B45_GATE_RE = _charset_gate(B45_CHARS)
_B92_GATE_RE = _charset_gate(B92_CHARS)
_B85_GATE_RE = _charset_gate(B85_CHARS)


def _base92_ord(ch):
    n = ord(ch)
    if ch == "!":
        return 0
    if ord("#") <= n <= ord("_"):
        return n - ord("#") + 1
    if ord("a") <= n <= ord("}"):
        return n - ord("a") + 62
    raise ValueError("not base92: %r" % ch)


def _try_base92(data):
    """thenoviceoof/basecrack base92: fixed 13-bit pairs, 91-char alphabet."""
    if not isinstance(data, str) or len(data) < 12:
        return None
    if _B92_GATE_RE.fullmatch(data) is None:
        return None
    bitstr = []
    out = bytearray()
    n = len(data)
    try:
        for i in range(n // 2):
            x = _base92_ord(data[2 * i]) * 91 + _base92_ord(data[2 * i + 1])
            bitstr.append(format(x, "013b"))
        if n % 2:
            bitstr.append(format(_base92_ord(data[-1]), "06b"))
    except ValueError:
        return None
    bits = "".join(bitstr)
    for i in range(0, len(bits) - 7, 8):
        out.append(int(bits[i:i + 8], 2))
    if not out or not _looks_meaningful(bytes(out)):
        return None
    return bytes(out)


def _bigint_decode(s, alphabet):
    """Positional base-N -> bytes (big-endian), None on overflow-free failure."""
    b = len(alphabet)
    idx = {c: i for i, c in enumerate(alphabet)}
    v = 0
    for ch in s:
        v = v * b + idx[ch]
    if not v:
        return None
    return v.to_bytes((v.bit_length() + 7) // 8, "big")


def _try_base62(data):
    if not isinstance(data, str) or len(data) < 12:
        return None
    if _B62_GATE_RE.fullmatch(data) is None:
        return None
    if not (any(c.isdigit() for c in data) and any(c.isalpha() for c in data)):
        return None
    out = _bigint_decode(data, string.digits + string.ascii_uppercase +
                         string.ascii_lowercase)
    if out and _looks_meaningful(out):
        return out
    return None


def _try_base58(data):
    if not isinstance(data, str) or len(data) < 12:
        return None
    if _B58_GATE_RE.fullmatch(data) is None:
        return None
    if not (any(c.isdigit() for c in data) and any(c.isalpha() for c in data)):
        return None
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    out = _bigint_decode(data, alphabet)
    if not out:
        return None
    pad = 0
    for ch in data:
        if ch == "1":
            pad += 1
        else:
            break
    out = b"\x00" * pad + out if pad else out
    if _looks_meaningful(out):
        return out
    return None


def _try_base45(data):
    """RFC 9285: groups of 3 chars -> 2 bytes; tail group of 2 -> 1 byte."""
    if not isinstance(data, str) or len(data) < 12:
        return None
    if _B45_GATE_RE.fullmatch(data) is None:
        return None
    cs = set(data)
    if not (cs & B45_SPECIALS):     # require a non-alnum char: real base45
        return None
    alphabet = string.digits + string.ascii_uppercase + " $%*+-./:"
    idx = {c: i for i, c in enumerate(alphabet)}
    out = bytearray()
    body = data.rstrip("=")
    if len(body) % 3 == 1:
        return None
    try:
        for i in range(0, len(body), 3):
            chunk = body[i:i + 3]
            if len(chunk) == 3:
                u = idx[chunk[0]] + idx[chunk[1]] * 45 + idx[chunk[2]] * 45 * 45
                if u > 0xFFFF:
                    return None
                out += u.to_bytes(2, "big")
            else:
                u = idx[chunk[0]] + idx[chunk[1]] * 45
                if u > 0xFF:
                    return None
                out.append(u)
    except KeyError:
        return None
    if not out or not _looks_meaningful(bytes(out)):
        return None
    return bytes(out)


def _try_ascii85(data):
    """a85 (Adobe, no framing) then b85 (RFC 1924)."""
    if not isinstance(data, str) or len(data) < 12:
        return None
    res = []
    try:
        raw = data.encode("ascii")
    except UnicodeEncodeError:
        return None
    # [evolve-20261117-27] provable precondition for the a85 attempt
    # (gate block above): chars outside \x21-\x75/'z'/ignorechars make
    # a85decode raise, so only gate-passing nodes pay the decode.
    if _A85_GATE_RE.fullmatch(data):
        try:
            out = base64.a85decode(raw)
            if out and _looks_meaningful(out):
                res.append(("b85a", out))
        except (ValueError, binascii.Error):
            pass
    if _B85_GATE_RE.fullmatch(data) is not None:
        try:
            out = base64.b85decode(raw)
            if out and _looks_meaningful(out):
                res.append(("b85b", out))
        except (ValueError, binascii.Error):
            pass
    return res or None


def _try_url(data):
    if not isinstance(data, str):
        return None
    if "%" not in data:
        return None
    out = re.sub(r"%([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), data)
    return out if out != data else None


def _try_gzip_zlib(data):
    if not isinstance(data, bytes):
        return None
    # [evolve-20261117-27] provable header precondition (gate block
    # above): with neither a possible zlib CMF first byte nor the gzip
    # magic, the first two tries are guaranteed exceptions; only the
    # headerless raw-deflate try (wbits=-15) can still matter.
    if (data and data[0] in _ZLIB_CMF_FIRST) or data[:2] == _GZIP_MAGIC:
        fns = (zlib.decompress, gzip.decompress,
               lambda b: zlib.decompress(b, -15))
    else:
        fns = (lambda b: zlib.decompress(b, -15),)
    for fn in fns:
        try:
            out = fn(data)
            if out and _looks_meaningful(out):
                return out
        except Exception:
            continue
    return None


# [evolve-20261117-39 / rsi-110] non-printable count table for the
# meaningful filter: one 256-byte translate table built once at import.
# Byte b maps to 1 iff NOT (32 <= b < 127 or b in (9, 10, 13)); every
# printable byte maps to 0. sample.translate(table).count(1) is the EXACT
# same non-printable count as the reference per-byte genexpr + sum, so
# len(sample) - count reproduces the same integer printable count and the
# identical float division/comparison keep the predicate bit-identical.
_MEANINGFUL_COUNT_TABLE = bytes(
    0 if (32 <= b < 127 or b in (9, 10, 13)) else 1 for b in range(256)
)


def _looks_meaningful(data):
    """Heuristic: enough printable ascii to be worth recursing into.

    [evolve-20261117-39 / rsi-110] Table-driven predicate. The reference
    body ran a Python-level per-byte genexpr + sum on every decoder
    candidate -- it is the universal meaningful filter, so every
    base-N/hex/gzip/xor candidate pays it once (live RED on the entry-60
    incumbent, cProfile over full run_fast_path: 2023-StarCTF-DeadGame
    190,034 calls cum 3.807 s of a 45.7 s profiled wall / 21.9 s real;
    2023-ISCC-Analysis 61,166 calls 0.777 s of 12.4 s profiled / 6.0 s
    real). The C-level translate+count evaluates the identical predicate;
    output is byte-identical (pinned in selftest block
    evolve39-meaningful-table over boundary lengths, all 256 byte values,
    seeded fuzz, and live carrier candidate streams, including empty /
    1-byte / 511 / 512 / 513, U+FFFD-heavy binary faces, and bytearray
    inputs, which translate() handles with the same 256-byte table).
    """
    if not data:
        return False
    sample = data[:512]
    nonprintable = sample.translate(_MEANINGFUL_COUNT_TABLE).count(1)
    printable = len(sample) - nonprintable
    return printable / len(sample) > 0.85


# [evolve-20261117-25 / rsi-095] caesar shift tables: one 256-byte
# bytes.translate table per shift, built once at import (26 x 256 B = 6.5 KB).
# Only the ASCII letter bytes 65-90 / 97-122 move; every other byte --
# including all >= 0x80 UTF-8 lead/continuation bytes -- maps to itself, so
# encode("utf-8", "surrogatepass") -> translate -> decode(..., "surrogatepass")
# round-trips ANY str exactly the way the reference char loop left it.


def _caesar_shift_table(shift):
    t = bytearray(range(256))
    for i in range(65, 91):          # A-Z
        t[i] = 65 + (i - 65 + shift) % 26
    for i in range(97, 123):         # a-z
        t[i] = 97 + (i - 97 + shift) % 26
    return bytes(t)


_CAESAR_SHIFT_TABLES = tuple(_caesar_shift_table(s) for s in range(26))


def caesar_sweep(text):
    """Yield (shift, decoded) for all 26 shifts; caller checks flags.

    [evolve-20261117-25 / rsi-095] Table-driven decode: the old body ran a
    Python-level char loop (``for ch in text``) for all 25 shifts on every
    text input, which after rsi-094 was the single largest fastpath cost on
    text inputs (live RED: 2023-Bluehat-Quals-rank 412715 B -> 5.884 s over
    781638 charged chars, whole-file 2.967 s + 105 per-line 2.917 s at ~133
    k chars/s; the text stages stay budget-unbounded, so this is pure wall
    clock, no budget/verdict change). The text is encoded once to UTF-8
    with surrogatepass (any str round-trips, even lone surrogates) and each
    shift is a C-level bytes.translate over a precomputed table plus one
    C-level decode. Output is byte-identical to the reference loop (pinned
    in selftest block evolve25-caesar-table, including empty/1-char/
    511/512/513 chars, mixed case, digit/punctuation pass-through,
    accents/CJK/U+FFFD and lone surrogates).
    """
    raw = text.encode("utf-8", "surrogatepass")
    results = []
    for shift in range(1, 26):
        dec = raw.translate(_CAESAR_SHIFT_TABLES[shift])
        results.append((shift, dec.decode("utf-8", "surrogatepass")))
    return results


# [evolve-20261117-26 / rsi-096] atbash table: one 256-byte translation
# table built once at import. Only ASCII letters move (a-z <-> z-a via
# 219 - b, A-Z <-> Z-A via 155 - b); every other byte -- including all
# >= 0x80 UTF-8 lead/continuation bytes -- maps to itself, so
# encode("utf-8", "surrogatepass") -> translate -> decode(..., "surrogatepass")
# round-trips ANY str exactly the way the reference char loop left it.
def _atbash_table():
    t = bytearray(range(256))
    for i in range(65, 91):          # A-Z -> Z-A
        t[i] = 155 - i
    for i in range(97, 123):         # a-z -> z-a
        t[i] = 219 - i
    return bytes(t)


_ATBASH_TABLE = _atbash_table()


def atbash(text):
    """Involutive ASCII-letter mirror, table-driven.

    [evolve-20261117-26 / rsi-096] The reference body ran a Python-level
    char loop over every chain_decode node; after rsi-094/095 fixed the xor
    and caesar sweeps it was the largest remaining pure-Python per-element
    loop inside chain decode (live RED: 2023-Bluehat-Quals-rank 56.6% of
    chain time, 1.678 s over 848 calls / 6.25 M decode chars; 2023-ISCC-
    Analysis 24.1%, 5.223 s over 230 k calls / 16.37 M chars, ~3.9 M
    chars/s). The text is encoded once to UTF-8 with surrogatepass and
    translated by a precomputed 256-byte table in C plus one C-level
    decode. Output is byte-identical to the reference loop (pinned in
    selftest block evolve26-atbash-table, including empty/1-char/511/512/
    513, mixed case, digit/punctuation pass-through, accents/CJK/U+FFFD
    and lone surrogates).
    """
    raw = text.encode("utf-8", "surrogatepass")
    return raw.translate(_ATBASH_TABLE).decode("utf-8", "surrogatepass")


# [evolve-20261117-28 / rsi-098] rot13 table: shift-13 is exactly the
# rsi-095 caesar shift table, so the proven bytes.translate mechanism
# applies unchanged; only ASCII letter bytes move (a-m <-> n-z,
# A-M <-> N-Z), every other byte -- including all >= 0x80 UTF-8
# lead/continuation bytes -- maps to itself, so
# encode("utf-8", "surrogatepass") -> translate -> decode(...,
# "surrogatepass") round-trips ANY str exactly the way the previous
# codecs.encode(cur, "rot13") call left it (equivalence pinned in selftest
# block evolve28-rot13-table over boundary cases, non-ASCII, lone
# surrogates and a 412 KB live-shape blob).
_ROT13_TABLE = _CAESAR_SHIFT_TABLES[13]


def rot13(text):
    """ASCII-letter rot13, table-driven.

    [evolve-20261117-28 / rsi-098] chain_decode used to call
    codecs.encode(cur, "rot13") on every BFS node: a per-call
    codec-registry lookup plus a str.translate over a dict inside the
    rot_13 codec (live RED, read-only cProfile on the incumbent:
    2023-Bluehat-Quals-rank 0.429 s cumtime over 848 calls = 34.7% of the
    1.239 s chain wall, 6.8x the already-table-driven atbash;
    2023-ISCC-Analysis 1.902 s over 230,095 calls = 7.0% of chain). The
    text is encoded once to UTF-8 with surrogatepass and translated by
    the precomputed shift-13 table in C plus one C-level decode.
    Output is byte-identical to codecs.encode (pinned in selftest block
    evolve28-rot13-table, incl. empty/1-char/511/512/513, mixed case,
    digit/punctuation pass-through, accents/CJK/U+FFFD, lone
    surrogates). Text stages stay budget-unbounded, so this is pure
    wall clock -- no budget/verdict change is claimed.
    """
    raw = text.encode("utf-8", "surrogatepass")
    return raw.translate(_ROT13_TABLE).decode("utf-8", "surrogatepass")


# [evolve-20261117-40 / rsi-110] progressive-caesar anchor back-solve.
# RED (live, engine-verified before this change): 2024-newstar__Strange_King
# challenge.txt -- ciphertext "ksjr{EcxvpdErSvcDgdgEzxqjql}" where the
# letter-shift per position follows an arithmetic progression delta_i =
# a + b*i (mod 26) with a=5, b=2 ("some emperor progresses every day,
# takes a mod and returns" -- NewStar 2024 W1); plaintext is
# flag{PleaseDoNotStopLearing}. The incumbent caesar stage only sweeps
# 25 CONSTANT shifts, so every progressive variant is out of reach.
# Pool-wide carrier search (this round, 3,606 files, read-only anchor
# back-solve scan): the a!=0/b!=0 family exists exactly once in the
# current source distribution (Strange_King + its newstar-category /
# newstar-week re-publish mirrors, same payload md5) -- the family is
# single-point, so waiting for a "second live RED carrier" (rsi-109
# next#3) would block indefinitely; this round adopts with the
# rsi-105 evidence structure instead: one live RED + green-side scan
# evidence + crafted hit-reachability fixtures.
# Method: for every "letters-then-{" position, hypothesize the plaintext
# prefix is one of the checker's flag anchors (flag{, ctf{, ... -- the
# rsi-101 leading-literal set), back-solve (a, b) from the first two
# prefix deltas and verify the remaining prefix letters agree; a
# consistent back-solve decodes the brace payload with shift_i =
# (a + b*i) % 26 (i = offset INSIDE the bracketed segment, matching the
# Strange_King encoding where '{' occupies a slot) and the decode is
# credited only when it passes the standard _flag_text + garbage gate.
# b == 0 is skipped: a constant shift is the incumbent caesar stage's
# domain (25 shifts, no per-position progression), so this stage never
# duplicates it. O(candidate-positions x anchors) per input, no miss
# notes by design (green faces keep the exact incumbent attempt count).
_PROG_CAND_RE = re.compile(r"[A-Za-z]{3,12}\{")
_PROG_MAX_POS = 2000
_PROG_SEG_CAP = 160


def progressive_caesar_sweep(checker, text):
    """Back-solve arithmetic-progressing caesar shifts from flag anchors.

    Returns (flag or None, note). Note is non-empty only on a hit."""
    if not isinstance(text, str) or len(text) < 12:
        return None, ""
    anchors = getattr(checker, "_anchors", None)
    if not anchors:
        return None, ""
    npos = 0
    for m in _PROG_CAND_RE.finditer(text):
        npos += 1
        if npos > _PROG_MAX_POS:
            break
        pos = m.start()
        for anc in anchors:
            pref = anc[:-1]
            L = len(pref)
            if L < 3 or pos + L >= len(text) or text[pos + L] != "{":
                continue
            deltas = []
            broken = False
            for k, pc in enumerate(pref):
                o1 = ord(text[pos + k])
                o2 = ord(pc)
                v1 = (o1 - 97) if 97 <= o1 <= 122 else \
                     ((o1 - 65) if 65 <= o1 <= 90 else None)
                v2 = (o2 - 97) if 97 <= o2 <= 122 else \
                     ((o2 - 65) if 65 <= o2 <= 90 else None)
                if v1 is None or v2 is None:
                    broken = True
                    break
                deltas.append((v1 - v2) % 26)
            if broken or len(deltas) < 3:
                continue
            a = deltas[0]
            b = (deltas[1] - deltas[0]) % 26
            if b == 0:
                continue  # constant shift: the caesar stage's domain
            ok = True
            for k in range(2, L):
                if (a + b * k) % 26 != deltas[k]:
                    ok = False
                    break
            if not ok:
                continue
            end = text.find("}", pos)
            if end != -1:
                seg = text[pos:end + 1]
            else:
                seg = text[pos:min(len(text), pos + _PROG_SEG_CAP)]
            if len(seg) > _PROG_SEG_CAP:
                seg = seg[:_PROG_SEG_CAP]
            out = []
            for j, ch in enumerate(seg):
                o = ord(ch)
                s = (a + b * j) % 26
                if 97 <= o <= 122:
                    out.append(chr((o - 97 - s) % 26 + 97))
                elif 65 <= o <= 90:
                    out.append(chr((o - 65 - s) % 26 + 65))
                else:
                    out.append(ch)
            dec = "".join(out)
            found = _flag_text(checker, dec)
            if found:
                return found, "back-solved %s from %r at pos %d (a=%d b=%d)" % (
                    anc, text[pos:pos + 12], pos, a, b)
    return None, ""


# [evolve-20261117-24 / rsi-094] single-byte XOR tables: one 256-byte
# translation table per key, built once at import (256 x 256 B = 64 KB).
# bytes.translate() consumes a 256-entry table mapping each input byte to its
# output byte, so table[k][i] == i ^ k reproduces the reference generator.
_XOR_BYTE_TABLES = tuple(bytes(i ^ k for i in range(256)) for k in range(256))


def xor_sweep_single(data):
    """Yield (key_byte, decoded_bytes) for printable-key candidates.

    [evolve-20261117-24 / rsi-094] Table-driven decode: the old body ran
    `bytes(b ^ k for b in data)` -- a Python-level byte loop -- for all 255
    keys on every byte input, which was the single largest fastpath cost on
    large blobs (live RED: 2023-Bluehat-Quals-rank 412715 B -> 12.244 s of
    the 24.67 s solve, 255 x 412 KB = 105 MB of interpreter-level XOR).
    bytes.translate applies a precomputed 256-byte table in C, and the
    printability sample only needs the first 512 bytes, so the full decode
    now happens only for keys that can actually be credited. Semantics are
    byte-identical to the reference loop (asserted in selftest block
    evolve24-xor-table, including the 0/1/511/512/513-byte boundaries).
    """
    if not isinstance(data, bytes):
        data = bytes(data)
    head = data[:512]
    for k in range(1, 256):
        tab = _XOR_BYTE_TABLES[k]
        if not _looks_meaningful(head.translate(tab)):
            continue
        yield k, data.translate(tab)



# [evolve-20261118-16 / rsi-136] stage-4 xor-token pool tier bias. The
# incumbent's raw lexicographic cut (sorted()[:64]) is a noise-first knife:
# ASCII sorts punctuation (0x21-0x2f) and digits (0x30-0x39) before letters,
# so whenever a tree yields more than 64 tokens the 64 key slots fill with
# '!#p'-class junk drawn from magic-less binary blobs while the "hint words"
# this bridge strategy exists for are starved. Live census on the trajectory
# store: 123 cap-hit challenges / 202 "64 token keys tried" miss rows; the
# kept-64 samples on MISC2 (126 tokens), easy_api (29122), DHRSA (1064),
# 瓷砖计算机 (1004) and 简单的压缩包 (1234) are dominated by punctuation-
# and digit-initial noise while dropped tails hold flask/atoi/flag_zip-class
# real words. Fix = tier-BEFORE-cut, never a filter: tokens whose first char
# is a letter or underscore take the 64 slots first (lexicographic order
# inside each tier), leftover slots spill to the noise tier; pools of <= 64
# return the full lexicographic list and stay byte-identical to the
# incumbent, and every token the candidate drops is one the incumbent also
# dropped -- only the tier boundary of the cut changed.
_TOK_LETTER_INIT = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")


def extract_tokens(files, max_len=32):
    """Words found in challenge files; used as XOR keys / passwords."""
    tokens = set()
    for f in files:
        if 0 < f["size"] <= 200_000 and f["magic"] is None:
            try:
                with open(f["abs"], "r", encoding="utf-8", errors="replace") as fh:
                    txt = fh.read()
            except OSError:
                continue
            token_pat = "[A-Za-z0-9_@!#%^&*+=]{3," + str(max_len) + "}"
            for w in re.findall(token_pat, txt):
                if w.lower() not in ("the", "and", "flag", "key", "ctf"):
                    tokens.add(w)
    toks = sorted(tokens)
    if len(toks) <= 64:
        return toks
    tier0 = [t for t in toks if t[0] in _TOK_LETTER_INIT]
    tier1 = [t for t in toks if t[0] not in _TOK_LETTER_INIT]
    return (tier0 + tier1)[:64]


# [evolve-20261117-30 / rsi-100] xor_with_key: the multi-byte cyclic XOR paid a
# pure-Python per-element loop with a modular index on every byte (bytes(b ^
# kb[i % len(kb)] for i, b in enumerate(data)), ~4.3 MB/s measured) -- the last
# remaining per-element XOR loop after rsi-094 tableized the single-byte sweep.
# Live RED (this feed round): the token stage burns 48.19 s = 76% of baby_re's
# 63.7 s solve and 69.78 s = 86% of WAF-bypass's 81.1 s (60 s budget then
# overruns to 80.8 s because an in-flight 2 MB input's 64-token pass cannot be
# interrupted); pool-wide 288 dirs project 7.8 GB of token-stage Python XOR.
# The rewrite is the proven rsi-094/095 table mechanism applied to the cyclic
# case: position p is served by key byte kb[p % k], so the k strided slices
# data[i::k] are each a SINGLE-byte XOR face -> one C-level bytes.translate
# over the rsi-094 _XOR_BYTE_TABLES per slice, written back with strided
# bytearray assignment. Output is byte-identical to the reference loop
# (selftest evolve30-xorkey-slice pins the boundary battery + fuzz vs an
# inline reference implementation). k == 1 is the rsi-094 single-table path
# with zero copies; exotic non-bytes data falls back to the reference loop so
# the input domain is unchanged.
def xor_with_key(data, key):
    kb = key.encode("utf-8", errors="replace") if isinstance(key, str) else key
    if not kb:
        return None
    k = len(kb)
    if not isinstance(data, (bytes, bytearray)):
        return bytes(b ^ kb[i % k] for i, b in enumerate(data))
    if k == 1:
        return bytes(data.translate(_XOR_BYTE_TABLES[kb[0]]))
    out = bytearray(len(data))
    for i in range(k):
        out[i::k] = data[i::k].translate(_XOR_BYTE_TABLES[kb[i]])
    return bytes(out)


def js_unicode_escape(checker, text):
    r"""Decode JS unicode-escape string literals (\uXXXX runs) and flag-check.
    Returns (flag_or_None, note). Provenance stays derived: the decoded form
    is not present verbatim in the source (source stores \u0066.. escapes)."""
    hits = 0
    for m in JS_UE_RE.finditer(text):
        raw = m.group(1)
        try:
            dec = raw.encode("ascii").decode("unicode_escape")
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
        hits += 1
        found = _flag_text(checker, dec)
        if found:
            return found, "escape-run (%d chars) -> strict flag shape" % len(dec)
        lm = UE_FLAG_LENIENT.search(dec)
        if lm:
            return lm.group(0), "escape-run -> lenient flag shape %r" % lm.group(0)
    return None, ("%d escape literals, no flag" % hits) if hits else "no escape literals"


def _mz_xor_candidate(data):
    """Recover (key_cycle, phase_anchor) for a cyclic-XOR'd PE, else None.

    key_cycle[j] = data[anchor + j] ^ C for a constant-plaintext run at
    `anchor`; decrypt byte i with key_cycle[(i - anchor) % N]. Phase is
    carried by the anchor itself so alignment never needs modular guessing.
    """
    if len(data) < MZ_XOR_MIN_FILE or data[:2] == b"MZ":
        return None
    k0 = bytes(a ^ b for a, b in zip(data[:4], MZ_KNOWN_HEAD))
    if k0 == b"\x00\x00\x00\x00":
        return None  # head already equals the known plaintext: not XORed
    for C in range(256):  # C=0 first: zero padding dominates real samples
        V = bytes(k ^ C for k in k0)
        p1 = data.find(V)
        budget = MZ_XOR_FIND_BUDGET
        while p1 >= 0 and budget > 0:
            budget -= 1
            p2 = data.find(V, p1 + 1)
            if p2 < 0:
                break
            N = p2 - p1
            if 4 <= N <= MZ_XOR_MAX_N:
                seg_len = 4 * N
                seg = data[p1:p1 + seg_len + N]
                if len(seg) >= seg_len + N and seg[:seg_len] == seg[N:seg_len + N]:
                    key_cycle = bytes(b ^ C for b in data[p1:p1 + N])
                    return key_cycle, p1
            p1 = p2
    return None


def _mz_xor_decrypt(data, key_cycle, anchor, limit=MZ_XOR_DECRYPT_LIMIT):
    """Decrypt with stream[i] = key_cycle[(i - anchor) % N]. Big-int XOR.
    """
    n = min(len(data), limit)
    N = len(key_cycle)
    reps = (n // N) + 2
    big = key_cycle * reps
    shift = anchor % N
    stream = (big[shift:] + big[:shift])[:n]
    a = int.from_bytes(data[:n], "big")
    b = int.from_bytes(stream, "big")
    return (a ^ b).to_bytes(n, "big")


def mz_xor_key_recovery(checker, files):
    """Cyclic-XOR-encrypted PE detector. Self-reads files (bypasses the 2MB
    _candidate_inputs cap; malware samples are multi-MB). Returns (flag|None,
    note). A successfully recovered-but-flagless PE is reported in the note:
    honest attempt trail for the deep path and for trajectory analysis."""
    for f in files:
        size = f.get("size") or 0
        if size < MZ_XOR_MIN_FILE or size > MZ_XOR_MAX_FILE:
            continue
        try:
            with open(f["abs"], "rb") as fh:
                data = fh.read(MZ_XOR_MAX_FILE)
        except OSError:
            continue
        cand = _mz_xor_candidate(data)
        if not cand:
            continue
        key_cycle, anchor = cand
        N = len(key_cycle)
        head = _mz_xor_decrypt(data, key_cycle, anchor, limit=0x400)
        if head[:4] != MZ_KNOWN_HEAD:
            continue  # wrong phase/period coincidence
        e_lfanew = struct.unpack("<I", head[0x3C:0x40])[0]
        if not 0x40 <= e_lfanew <= len(data) - 0x100:
            continue
        sig = _mz_xor_decrypt(data, key_cycle, anchor, limit=e_lfanew + 8)
        if sig[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
            continue
        dec = _mz_xor_decrypt(data, key_cycle, anchor)
        found = checker.find(dec.decode("latin-1", errors="replace"))
        if found and not _implausible_flag(found):
            return found, ("%s: cyclic XOR key N=%d %r via MZ head + const-run, "
                           "flag in plaintext PE" % (f["path"], N, key_cycle))
        return None, ("%s: PE recovered under cyclic XOR key N=%d %r "
                      "(MZ+PE sig verified), no flag string in plaintext"
                      % (f["path"], N, key_cycle))
    return None, "no cyclic-XOR-encrypted PE candidate"


# [evolve-20261117-29 / rsi-099] wrapped-token extraction for the chain
# stage. RED (live, this feed round): 2024-newstar-category crypto.md #L5
# embeds the week-1 Base checkin payload as a BACKTICK-WRAPPED hex run
# (`4C4A...3D3D3D3D` -> hex > b32 > b64 -> flag); _candidate_inputs emits
# whole-file + per-line text and stage 1 chain-decodes each verbatim, so
# the line candidate still carries the ` ` wrapper, every rsi-097 charset
# gate rejects it at depth 0 and the 3-layer chain is never entered
# (engine 1.75 s / 329 attempts honest NOT SOLVED; model channel solved
# the unwrapped span in ~120 s, account-model row same round). Real
# archive briefs wrap payloads in `...` / "..." / '...' spans
# (CTF-Archives README convention), so stage 1.2 extracts those spans and
# chain-decodes each as its own input. The four pure-run gates below are
# the rsi-097 necessity conditions moved to the extraction boundary: a
# span that is not a pure hex/b32/b64/a85 run can never pass the
# decoder's own validation, so prose, URLs, braces-bearing format hints
# and md links are dropped for zero chain cost.
_TOK_WRAP_RE = re.compile(
    r"`([^`\r\n]{16,512})`"
    r"|\"([^\"\r\n]{16,512})\""
    r"|'([^'\r\n]{16,512})'")
_TOK_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_TOK_B32_RE = re.compile(r"[A-Z2-7]+={0,6}")
_TOK_B64_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_TOK_A85_RE = re.compile(r"[\x21-\x75]+")
TOK_MIN_LEN = 16
TOK_MAX_PER_FILE = 48
TOK_MAX_TOTAL = 256


# [evolve-20261118-10 / rsi-130] caesar stage total-attempt cap. The rsi-109
# per-line subsumption only skips the line sweep of a whole-file CLEAN input;
# a masked input (whole-file sweep hit a raw-echo or garbage-gated match)
# falls back to the full per-line sweep with NO bound, and every swept input
# appends 1 miss note + up to 25 provenance rows. Live-fire: 2023-ISCC-
# Analysis 35045 caesar attempts per solve (x3 replays), 2024-CCB lwewl
# 29974, 2025-jksn attachments726 16470+14576 -- attempts table 96%+ caesar
# junk, fixation stats drowned (the 01:32:44 rsi-101..105 pseudo-batch
# came from these inflated counts), single-solve fixture 6004 rows/20.7s.
# Caesar has exactly 1 success in the whole 332k-attempt table (a suspect
# CSS-fragment hit), so the cut search space is near-zero-yield by record.
# Distribution check: median challenge spends 41 caesar+provenance rows,
# p75=156, 86% of challenges stay under 500 -- the cap only bites the
# explosion family. Charging semantics mirror rsi-099 TOK_MAX_TOTAL (count-
# based, not wall-clock: rsi-095 already made the sweep itself fast, the
# remaining cost IS the attempt-row flood). A credited find (non-raw-echo
# flag) never consumes nor respects the budget -- it returns immediately.
CAESAR_MAX_ATTEMPTS = 500


# [evolve-20261118-13 / rsi-133] chain stage-1 total-row cap. Stage 1 chain-
# unwraps every text input (whole file + each line) and a flag-shaped plain-
# text line is found at depth 0 -- when the find is raw-echo (the bait sits
# verbatim in the file) one provenance row is appended per line with NO
# bound, the same unbounded-masked-fallback shape rsi-130 closed in the
# caesar stage. Measured: the rsi-130 evolve55 face (600 plaintext bait
# lines) drives 601 stage-1 rows per run_fast_path call, a 1500-line face
# drives 1501; today's live-fire census is thin (222 stage-1-shaped
# provenance rows across the 332k-attempt store, max 14 per challenge), so
# this cap is defense-in-depth on the same family, not a live-fire rescue.
# The cap charges every appended stage-1 row (chain-decode miss note or
# raw-echo provenance; a credited find returns before any charge); a
# legitimate many-file extraction stays safe -- 300 members spend ~300 #L1
# notes, under the cap. Charging semantics mirror rsi-130 (count-based:
# the row flood IS the remaining cost).
CHAIN1_MAX_ATTEMPTS = 500


def wrapped_tokens(text):
    """Deduped encoder-charset tokens from `...`/"..."/'...' spans.

    Only spans of TOK_MIN_LEN..512 chars that FULLMATCH one of the four
    encoder charsets are returned (<= TOK_MAX_PER_FILE, first-come); the
    len guard also drops sub-TOK_MIN_LEN spans the regex cannot match.
    """
    out, seen = [], set()
    for m in _TOK_WRAP_RE.finditer(text):
        tok = next(g for g in m.groups() if g is not None)
        if len(tok) < TOK_MIN_LEN or tok in seen:
            continue
        seen.add(tok)
        for gate in (_TOK_HEX_RE, _TOK_B32_RE, _TOK_B64_RE, _TOK_A85_RE):
            if gate.fullmatch(tok):
                out.append(tok)
                break
        if len(out) >= TOK_MAX_PER_FILE:
            break
    return out


# [evolve-20261118-14 / rsi-134] stage-1.2 consumption-order bias. Evidence
# census (this round, read-only): the ONLY stage-1.2 success in the whole
# 332k-attempt store is the newstar prose carrier (w1.md, 112-char hex token
# -> depth-3 b64 chain, flag{B@sE_0f_CrYpt0_N0W}), while live bigfish shows
# 185 tokens of which 84.9% come from .min.js/.min.css/.map/node_modules
# shaped asset files (jquery.min.js 48 = per-file cap, bootstrap.min.js 48,
# bootstrap.js 30, bootstrap.min.css.map 25 ...); source/prose files carry
# 1-3 tokens each. The first-come order burns TOK_MAX_TOTAL=256 on asset
# noise before prose/source tokens are ever seen. Length prefilter is
# REJECTED by data: the sole real token is 112 chars (distribution tail;
# bigfish p75=27 max=122) -- any semantic-free breadth cut risks the exact
# payload class entry-71 warned about. The change is ORDER-ONLY: collection
# keeps the incumbent first-come cross-input dedup verbatim (tok_seen
# semantics unchanged, first occurrence keeps its slot), then consumption
# visits non-asset groups first and asset-shaped groups last (stable sort,
# first-come preserved within each tier). Nothing is dropped: trees with
# <= TOK_MAX_TOTAL tokens consume every token exactly as before (only
# per-file miss-note order can change); when the budget runs dry the
# sacrifice is the ASSET-tier tail, never an unvisited source/prose file.
_TOK_ASSET_RE = re.compile(
    r"(?:\.min\.(?:js|css)|\.map|\.css)(?:$|[/\\])"
    r"|(^|[/\\])node_modules([/\\])",
    re.IGNORECASE)


# [evolve-20261117-36 / rsi-105] IDNA/punycode domain-label tokens. RED
# (live, this feed round): 2024-ShuXinCup-sjaq-IDN的绘画 描述.txt embeds
# xn--7nraaa738k9tnba223fba479qe42gda whose stdlib idna decode is
# 叁柒叁柒比幺零比玖叁零叁 (3737:10:9303, the intended key-material step
# before the netdisk image stage); the engine ran 24 attempts all-miss --
# chain-decode covers b64/hex/b32/... but has NO idna arm, while the model
# channel solved the decode in one line via the stdlib codec. Labels use
# the extraction charset [A-Za-z0-9.-{}] (NOT the proposal's [^\s]+: the
# live token is followed by a CJK 。 that would make .encode('ascii')
# raise and drop the REAL label) in lower-case 'xn--' only (the stdlib
# ToUnicode recognizes only that exact prefix, so an IGNORECASE regex
# would only extract labels the decoder itself rejects). '{'/'}' stay in
# the set because constructed carriers embed flag skeletons in the
# punycode BASIC part ('flag{...}' ASCII frame survives the encode) and
# the stdlib codec accepts such labels -- the extractor must not be
# narrower than the decoder; the codec's round-trip check is the junk
# filter. Pool-wide trigger face (read-only scan, 6822 files): 2 files /
# 2 tokens, so the 'xn--' substring gate leaves the other 6820 files
# untouched by construction.
_XN_TOKEN_RE = re.compile(r"xn--[A-Za-z0-9.\-{}]+")
IDNA_MAX_PER_FILE = 8
IDNA_MAX_TOTAL = 32


def idna_tokens(text):
    """Lowercase xn-- domain labels from whole-file text (deduped, capped)."""
    out, seen = [], set()
    for m in _XN_TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= IDNA_MAX_PER_FILE:
            break
    return out


def chain_decode(checker, text, max_depth=MAX_CHAIN_DEPTH):
    """BFS unwrap of encoding chains
    (b64/b32/hex/url/gunzip/reverse/rot13/atbash + base92/a85/b85/b62/b58/b45).
    Returns (flag or None, tried_note). Flag-checked at every level."""
    seen = set()
    queue = [("direct", text)]
    for depth in range(max_depth):
        next_queue = []
        for origin, cur in queue:
            if not isinstance(cur, str):
                continue
            key = cur[:200]
            if key in seen:
                continue
            seen.add(key)
            found = _flag_text(checker, cur)
            if found:
                return found, "depth=%d via %s" % (depth, origin)
            steps = []
            b64 = _try_base64(cur)
            if b64 is not None:
                steps.append(("b64", b64.decode("utf-8", errors="replace")))
            b32 = _try_base32(cur)
            if b32 is not None:
                steps.append(("b32", b32.decode("utf-8", errors="replace")))
            b92 = _try_base92(cur)
            if b92 is not None:
                steps.append(("b92", b92.decode("utf-8", errors="replace")))
            b85s = _try_ascii85(cur)
            if b85s:
                for nm, ob in b85s:
                    steps.append((nm, ob.decode("utf-8", errors="replace")))
            b62 = _try_base62(cur)
            if b62 is not None:
                steps.append(("b62", b62.decode("utf-8", errors="replace")))
            b58 = _try_base58(cur)
            if b58 is not None:
                steps.append(("b58", b58.decode("utf-8", errors="replace")))
            b45 = _try_base45(cur)
            if b45 is not None:
                steps.append(("b45", b45.decode("utf-8", errors="replace")))
            hx = _try_hex(cur)
            if hx is not None:
                steps.append(("hex", hx.decode("utf-8", errors="replace")))
            url = _try_url(cur)
            if url is not None:
                steps.append(("url", url))
            gz = _try_gzip_zlib(cur.encode("utf-8", errors="replace"))
            if gz is not None:
                steps.append(("gunzip", gz.decode("utf-8", errors="replace")))
            rev = cur[::-1].strip()
            if rev and rev != cur:
                steps.append(("reverse", rev))
            rot = rot13(cur)
            if rot != cur:
                steps.append(("rot13", rot))
            ab = atbash(cur)
            if ab != cur:
                steps.append(("atbash", ab))
            for name, decoded in steps:
                next_queue.append((name, decoded))
        queue = next_queue
        if not queue:
            break
    return None, "no flag in %d-depth chain" % max_depth

def _png_pixels(data):
    """Decode 8-bit PNG (color type 0/2/4/6, non-interlaced) -> (w,h,channels,bytearray)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, ihdr = 8, b"", None
    while pos + 8 <= len(data):
        ln = int.from_bytes(data[pos:pos + 4], "big")
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            ihdr = body
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        pos += 12 + ln
    if not ihdr or not idat:
        return None
    w, h, bitd, ct, _, _, inter = struct.unpack(">IIBBBBB", ihdr[:13])
    if bitd != 8 or inter != 0 or ct not in (0, 2, 4, 6):
        return None
    ch = {0: 1, 2: 3, 4: 2, 6: 4}[ct]
    try:
        raw = zlib.decompress(idat)
    except Exception:
        return None
    stride, bpp = w * ch, ch
    if len(raw) < h * (stride + 1):
        return None
    out, prev, p = bytearray(), bytearray(stride), 0
    for _ in range(h):
        f = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                b = prev[i]
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out += line
        prev = line
    return w, h, ch, out


def _bits_to_bytes(bits):
    out = bytearray()
    for i in range(0, len(bits) - 7, 8):
        v = 0
        for b in bits[i:i + 8]:
            v = (v << 1) | b
        out.append(v)
    return bytes(out)


def _png_idat_tolerant(idat, need=None):
    """Decompress the IDAT stream, tolerating a corrupted trailing adler32.

    Tampered PNGs (rsi-054 source: 2025-jswa-misc2) keep every pixel byte
    usable: zlib only reports `incorrect data check` when it verifies the
    4 checksum bytes at the very end of the stream, after all output has
    been produced. Chunked feed keeps earlier output alive; on error a
    byte-by-byte re-run salvages the final chunk's output too (the check
    fires on the last checksum byte, so nothing is lost before it).
    """
    d = zlib.decompressobj()
    out = bytearray()
    try:
        for i in range(0, len(idat), 4096):
            out += d.decompress(idat[i:i + 4096])
            if need is not None and len(out) >= need:
                break
        return bytes(out)
    except zlib.error:
        pass
    d2 = zlib.decompressobj()
    out = bytearray()
    try:
        for i in range(len(idat)):
            out += d2.decompress(idat[i:i + 1])
            if need is not None and len(out) >= need:
                break
    except zlib.error:
        pass
    return bytes(out)


def png_ihdr_crc_fix(checker, data):
    """IHDR width/height tamper recovery (rsi-054, 2026-11-16).

    Source: 2025-jswa-misc2 stored 2048x864 while the real geometry is
    1536x864 -- the stored IHDR crc32 matches the REAL body (tamper tools
    rarely recompute the chunk crc). Brute-force (w,h) against the stored
    crc, then validate on the decoded scanlines (every row filter byte must
    be 0..5) before trusting it.

    Returns (flag, note). flag is only produced when the recovered stream
    carries a flag pattern; otherwise the structural finding is still
    reported through the note so the deep path / model channel can skip the
    geometry brute-force step entirely.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 8 + 29:
        return None, "not a png"
    ln = int.from_bytes(data[8:12], "big")
    if data[12:16] != b"IHDR" or ln < 13 or len(data) < 16 + ln + 4:
        return None, "no ihdr"
    ihdr = data[16:16 + ln]
    crc_stored = int.from_bytes(data[16 + ln:20 + ln], "big")
    body = ihdr[:13]
    if zlib.crc32(b"IHDR" + body) & 0xffffffff == crc_stored:
        return None, "ihdr crc ok (no tamper)"
    w0, h0 = struct.unpack(">II", body[:8])
    bd, ct, cm, fm, il = body[8], body[9], body[10], body[11], body[12]
    if bd != 8 or il != 0 or ct not in (0, 2, 4, 6):
        return None, "ihdr crc mismatch but unsupported bd/ct/il"
    ch = {0: 1, 2: 3, 4: 2, 6: 4}[ct]
    rest = bytes(body[8:])
    real = None
    for w in range(1, 12001):  # width-only tamper: the common case
        if zlib.crc32(b"IHDR" + struct.pack(">II", w, h0) + rest) & 0xffffffff == crc_stored:
            real = (w, h0)
            break
    if real is None:  # height-only tamper
        for h in range(1, 12001):
            if zlib.crc32(b"IHDR" + struct.pack(">II", w0, h) + rest) & 0xffffffff == crc_stored:
                real = (w0, h)
                break
    if real is None:
        return None, "ihdr crc mismatch, no single-field recovery (w0=%d h0=%d)" % (w0, h0)
    w, h = real
    pos, idat = 8, b""
    while pos + 8 <= len(data):
        l2 = int.from_bytes(data[pos:pos + 4], "big")
        t2 = data[pos + 4:pos + 8]
        if pos + 12 + l2 > len(data):
            break
        if t2 == b"IDAT":
            idat += data[pos + 8:pos + 8 + l2]
        elif t2 == b"IEND":
            break
        pos += 12 + l2
    stride = w * ch
    need = h * (stride + 1)
    raw = _png_idat_tolerant(idat)  # whole stream: post-image bytes are a
    # classic payload slot (2025-jswa-misc2 carries 199,929 of them)
    if len(raw) < need:
        return None, ("ihdr tamper: crc says %dx%d but stream gives %d/%d bytes"
                      % (w, h, len(raw), need))
    bad = sum(1 for r in range(h) if raw[r * (stride + 1)] > 5)
    if bad:
        return None, ("ihdr tamper: crc match %dx%d but %d/%d bad row filters"
                      % (w, h, bad, h))
    extra = raw[need:]
    # Cheap scans before unfiltering: raw residual stream + post-image bytes
    found = _flag_text(checker, raw.decode("latin-1", errors="replace"))
    note = ("ihdr width tamper: real %dx%d (stored %dx%d), %d/%d rows valid, %d post-image bytes"
            % (w, h, w0, h0, h, h, len(extra)))
    if found:
        return found, note + ", flag in residual stream"
    if extra:
        found = _flag_text(checker, extra.decode("latin-1", errors="replace"))
        if found:
            return found, note + ", flag in post-image bytes"
    # Sampled unfilter (budget-bounded): head + tail rows, look for a flag.
    # Reconstruction gaps between the two windows are acceptable: sampled rows
    # are only a cheap probe for flag text, filter validation already passed.
    SAMPLE_N = 64
    sample_rows = sorted(set(list(range(min(h, SAMPLE_N))) +
                             list(range(max(0, h - SAMPLE_N), h))))
    prev = bytearray(stride)
    for r in range(h):
        if r not in sample_rows:
            continue
        p = r * (stride + 1)
        f = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        for i in range(stride):
            a = line[i - ch] if i >= ch else 0
            b = prev[i]
            c = prev[i - ch] if i >= ch else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif f == 4:
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        prev = line
        found = _flag_text(checker, bytes(line).decode("latin-1", errors="replace"))
        if found:
            return found, note + ", flag in unfiltered row %d" % r
    return None, note + ", no flag in sampled rows/extra"


def png_lsb_scan(checker, data, max_bits=8000):
    """Extract bit0 of each channel / interleaved RGB streams, look for ASCII flag."""
    dec = _png_pixels(data)
    if not dec:
        return None, "not a plain 8-bit PNG"
    w, h, ch, px = dec
    names = {1: ["gray"], 2: ["gray", "A"], 3: ["R", "G", "B"], 4: ["R", "G", "B", "A"]}[ch]
    streams = [(n, list(px[i::ch])) for i, n in enumerate(names)]
    if ch >= 3:
        r, g, b = px[0::ch], px[1::ch], px[2::ch]
        streams.append(("RGB", [v for t in zip(r, g, b) for v in t]))
        streams.append(("BGR", [v for t in zip(b, g, r) for v in t]))
    for name, vals in streams:
        for bit in range(3):
            bits = [(v >> bit) & 1 for v in vals[:max_bits]]
            text = _bits_to_bytes(bits).decode("latin-1")
            found = _flag_text(checker, text)
            if found:
                return found, "PNG %dx%d %s bit%d LSB" % (w, h, name, bit)
    return None, "PNG %dx%d %d streams x3 bits no flag" % (w, h, len(streams))


# [evolve-20261117-44 / rsi-116] PDF FlateDecode text extraction.
# RED (live, writeup-in-archive family): contest archives increasingly
# ship writeup.pdf as the ONLY evidence object (2024-OMCTF: README.md
# 19-challenge index + writeup.pdf 1.37MB, challenge bodies all outside
# the tree; engine 1.87s / 15 attempts NOT SOLVED while the model channel
# extracted 14,766 chars + 7 contest flags in ~60s via pypdf). This is
# structural blindness, not a tuning miss: the flag rides a CID-encoded
# hex-Tj operand inside a zlib FlateDecode stream (probe D: every flag
# needle ABSENT from all 57 inflated spans; 3,012 hex-Tj ops, zero
# literal-Tj, 9 ToUnicode CMaps), so no byte-level detector can see it
# and _candidate_inputs emits only the opaque [bytes] node for a
# magic!=None file. The detector reproduces the pypdf path in pure
# stdlib, probe-verified end-to-end on the live carrier:
#   1. true-stream census -- 'stream\r?\n' also matches the tail of
#      every 'endstream\r\n' (probe F: exactly one phantom span per
#      real span, head='endobj'); the 3 bytes before a match must not
#      be 'end'. rsi-097 zlib necessary-condition gate on every span
#      payload before inflate. zlib ONLY: raw deflate has no magic, so
#      random-image spans 'successfully' inflate into garbage (probe A
#      hung 90s on that decompression-bomb shape) -- no -15 fallback.
#   2. decompression-bomb cap: decompressobj(max_length) +
#      unconsumed_tail check drops any span inflating past the cap.
#   3. pipeline A: direct flag scan on every inflated span
#      (plain-ASCII content streams; C-level find).
#   4. pipeline B: ToUnicode CMaps (bfchar pairs + bfrange triple /
#      array forms, range width capped) -> hex-Tj / literal-Tj /
#      TJ-array reassembly -> checker.find (garbage gate applies).
# Self-reads files like the mz-xor detector (writeup carriers exceed
# the 2MB _candidate_inputs cap in the other direction: they enter
# [bytes] fine, but as opaque zlib noise). Non-PDF files pay one
# 1024-byte magic probe; no PDF -> no attempt record, so non-PDF
# challenges keep attempt streams byte-identical to the incumbent.
PDF_MAX_FILE = 16 * 1024 * 1024
PDF_MAX_SPANS = 512
PDF_SPAN_DEC_CAP = 64 * 1024 * 1024
PDF_CMAP_RANGE_CAP = 65536
PDF_MAGIC = b"%PDF-"
_PDF_STREAM_RE = re.compile(rb"stream\r?\n")
_PDF_HEX_TJ_RE = re.compile(rb"<([0-9A-Fa-f\s]+)>\s*Tj")
_PDF_LIT_TJ_RE = re.compile(rb"\(((?:[^()\\]|\\.)+)\)\s*Tj")
_PDF_TJ_ARR_RE = re.compile(rb"\[(.*?)\]\s*TJ", re.S)
_PDF_ARR_TOK_RE = re.compile(rb"<([0-9A-Fa-f\s]+)>|\(((?:[^()\\]|\\.)*)\)")
_PDF_BFCHAR_RE = re.compile(rb"beginbfchar(.*?)endbfchar", re.S)
_PDF_BFRANGE_RE = re.compile(rb"beginbfrange(.*?)endbfrange", re.S)
_PDF_CMAP_PAIR_RE = re.compile(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")
_PDF_CMAP_TRIPLE_RE = re.compile(
    rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")
_PDF_CMAP_RANGE_ARR_RE = re.compile(
    rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[((?:\s*<[0-9A-Fa-f]+>)+)\]")


def _pdf_cmap_table(data):
    """ToUnicode CMap stream -> {cid: unicode char}. bfchar pairs +
    bfrange (triple lo/hi/u0 and array forms). Malformed entries drop
    harmlessly; bfrange width is capped (bomb guard)."""
    table = {}
    for m in _PDF_BFCHAR_RE.finditer(data):
        for pm in _PDF_CMAP_PAIR_RE.finditer(m.group(1)):
            try:
                table[int(pm.group(1), 16)] = bytes.fromhex(
                    pm.group(2).decode("ascii")).decode("utf-16-be", "replace")
            except ValueError:
                continue
    for m in _PDF_BFRANGE_RE.finditer(data):
        body = m.group(1)
        for pm in _PDF_CMAP_TRIPLE_RE.finditer(body):
            lo, hi, u0 = (int(pm.group(i), 16) for i in (1, 2, 3))
            if hi - lo > PDF_CMAP_RANGE_CAP:
                continue
            for cid in range(lo, hi + 1):
                try:
                    table[cid] = chr(u0 + cid - lo)
                except ValueError:
                    break
        for pm in _PDF_CMAP_RANGE_ARR_RE.finditer(body):
            lo, hi = int(pm.group(1), 16), int(pm.group(2), 16)
            if hi - lo > PDF_CMAP_RANGE_CAP:
                continue
            for k, uh in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>",
                                              pm.group(3))):
                cid = lo + k
                if cid > hi:
                    break
                try:
                    table[cid] = bytes.fromhex(
                        uh.decode("ascii")).decode("utf-16-be", "replace")
                except ValueError:
                    continue
    return table


def _pdf_unescape_literal(lit):
    """PDF literal-string unescape: \\( \\) \\\\ \\n \\r \\t \\b \\f
    \\ooo (up to 3 octal digits), backslash-EOL continuation. latin-1
    pass-through; operands here are short text strings, not payloads."""
    out = []
    i, n = 0, len(lit)
    while i < n:
        c = lit[i]
        if c == 0x5C and i + 1 < n:
            d = lit[i + 1]
            if 0x30 <= d <= 0x37:
                j = i + 1
                val = cnt = 0
                while j < n and cnt < 3 and 0x30 <= lit[j] <= 0x37:
                    val = val * 8 + (lit[j] - 0x30)
                    j += 1
                    cnt += 1
                out.append(chr(val & 0xFF))
                i = j
                continue
            mapped = {0x6E: "\n", 0x72: "\r", 0x74: "\t",
                      0x62: "\b", 0x66: "\f"}.get(d)
            if mapped is not None:
                out.append(mapped)
                i += 2
                continue
            if d == 0x0A:
                i += 2
                continue
            if d == 0x0D:
                i = (i + 3) if (i + 2 < n and lit[i + 2] == 0x0A) else (i + 2)
                continue
            out.append(chr(d))
            i += 2
            continue
        out.append(chr(c))
        i += 1
    return "".join(out)


def _pdf_map_hex(bs, cmaps):
    """Hex-string operand -> visible text: 2-byte CIDs through every
    CMap (longest mapping wins); latin-1 fallback when nothing maps
    (1-byte simple-font operands)."""
    if cmaps:
        best = ""
        for t in cmaps:
            s = "".join(t.get(bs[i] * 256 + bs[i + 1], "")
                        for i in range(0, len(bs) - 1, 2))
            if len(s) > len(best):
                best = s
        if best:
            return best
    return bs.decode("latin-1", errors="replace")


def _pdf_cid_reassemble(text, cmaps):
    """Content stream -> visible text: hex-Tj / literal-Tj / TJ-array
    operands concatenated in stream order. Carrier evidence: 3,012 hex-Tj,
    zero literal-Tj, zero TJ arrays -- hex-Tj alone carries the corpus."""
    out = []
    for m in _PDF_HEX_TJ_RE.finditer(text):
        try:
            bs = bytes.fromhex(re.sub(rb"\s", b"", m.group(1)).decode("ascii"))
        except ValueError:
            continue
        out.append(_pdf_map_hex(bs, cmaps))
    for m in _PDF_LIT_TJ_RE.finditer(text):
        out.append(_pdf_unescape_literal(m.group(1)))
    for m in _PDF_TJ_ARR_RE.finditer(text):
        for om in _PDF_ARR_TOK_RE.finditer(m.group(1)):
            tok = om.group(0)
            if tok.startswith(b"<"):
                try:
                    bs = bytes.fromhex(
                        re.sub(rb"\s", b"", om.group(1)).decode("ascii"))
                except ValueError:
                    continue
                out.append(_pdf_map_hex(bs, cmaps))
            else:
                out.append(_pdf_unescape_literal(om.group(2)))
    return "".join(out)


def _pdf_text_scan_bytes(checker, data):
    """One PDF file -> (flag|None, evidence note)."""
    spans = []
    for m in _PDF_STREAM_RE.finditer(data):
        if m.start() >= 3 and data[m.start() - 3:m.start()] == b"end":
            continue  # the tail of 'endstream' is not a stream start
        start = m.end()
        end = data.find(b"endstream", start)
        if end == -1:
            continue
        payload = data[start:end]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        elif payload and payload[-1:] in (b"\n", b"\r"):
            payload = payload[:-1]
        if payload:
            spans.append(payload)
        if len(spans) >= PDF_MAX_SPANS:
            break
    if not spans:
        return None, "%PDF magic but no true stream"
    inflated = []
    for payload in spans:
        if payload[0] not in _ZLIB_CMF_FIRST:
            continue  # rsi-097 necessary condition: not a zlib header
        d = zlib.decompressobj()
        try:
            out = d.decompress(payload, PDF_SPAN_DEC_CAP)
            if d.unconsumed_tail:  # inflates past the cap: bomb, drop
                continue
            out += d.flush()
        except zlib.error:
            continue
        if out:
            inflated.append(out)
    if not inflated:
        return None, "no inflatable stream"
    # pipeline A: direct scan (plain-ASCII content streams)
    for out in inflated:
        found = _flag_text(checker, out)
        if found:
            return found, "flag in inflated PDF stream (%d span(s))" \
                % len(inflated)
    # pipeline B: content-stream reassembly. ToUnicode CMaps (bfchar
    # pairs + bfrange triple/array) map CID hex operands; literal-Tj
    # operands unescape directly and work with NO CMap at all (escaped
    # simple-font text); unmapped hex falls back to latin-1 harmlessly.
    cmaps = []
    for out in inflated:
        if b"beginbfchar" in out or b"beginbfrange" in out:
            t = _pdf_cmap_table(out)
            if t:
                cmaps.append(t)
    reassembled = 0
    for out in inflated:
        if b"BT" not in out or b" Tf" not in out:
            continue
        text = _pdf_cid_reassemble(out, cmaps)
        if not text:
            continue
        reassembled += len(text)
        found = checker.find(text)
        if found and not _GARBAGE_FLAG_RE.search(found) \
                and not _implausible_flag(found):
            return found, ("flag in CID-reassembled text (%d chars, "
                           "%d CMap(s))" % (len(text), len(cmaps)))
    if reassembled:
        if cmaps:
            return None, ("%d CMap(s), content streams reassembled %d "
                          "chars, no flag" % (len(cmaps), reassembled))
        return None, ("content streams reassembled %d chars (no CMap), "
                      "no flag" % reassembled)
    return None, "%d inflated span(s), no content stream" % len(inflated)


def pdf_text_scan(checker, files):
    """PDF FlateDecode text extraction (writeup-in-archive family face).
    Self-reads files like mz_xor_key_recovery. Returns (flag|None, note):
    the note carries per-file evidence (span/CMap/char census) so the
    deep path and the model channel see exactly what was already tried."""
    notes = []
    for f in files:
        size = f.get("size") or 0
        if size <= 0 or size > PDF_MAX_FILE:
            continue
        try:
            with open(f["abs"], "rb") as fh:
                data = fh.read(PDF_MAX_FILE)
        except OSError:
            continue
        if PDF_MAGIC not in data[:1024]:
            continue
        found, note = _pdf_text_scan_bytes(checker, data)
        if found:
            return found, "%s: %s" % (f["path"], note)
        notes.append("%s: %s" % (f["path"], note))
    if notes:
        return None, "; ".join(notes)
    return None, "no PDF text-stream candidate"


# [evolve-20261118-17 / rsi-139] Known-key symmetric decrypt stage -----------
# RED (live specimens, read-only census): the engine had ZERO symmetric-
# crypto capability -- every AES-family challenge with a derivable flag
# ended NOT SOLVED on the engine and only the model channel solved it:
# (1) 2025-jksn-quals 小学组 Crypto/AES: 题面.txt hands the 16-byte ASCII
#     key ThatsASecretKeyO plus two hex segments (32B + 16B); AES-128-ECB
#     decrypts to flag:{EncryptedMessages} (PKCS#7 8x\x08 valid, model
#     channel ~1min; engine 3.1s honest miss, writeup logged the fix
#     candidate "AES 已知密钥模式三分法" two rounds ago);
# (2) 2026-ICSC-ICS-Champ GY10-工控网关密钥泄露: config.json inside the
#     firmware squashfs carries "aes_key_hex": "A1B2C3D4E5F67890ABCDEF1234
#     567890" and flag.enc (48B, 3 AES blocks) sits in-tree; AES-128-CBC
#     with IV=first-16B decrypts to flag{ICS_Gateway_Crack_2026}
#     (PKCS#7 4x\x04 valid) -- verified offline this round. The mode
#     triage (ECB / CBC-zero-IV / CBC-IV-prefix) is exactly the three
#     modes the two specimens plus the WQB2025-new_trick writeup used.
# Stage contract mirrors rsa_numeric_scan: returns (flag|None, note);
# note empty when no labeled key exists at all (challenges without key
# material keep a byte-identical attempt stream). Pure stdlib AES
# decrypt core (GF(2^8) tables built at import, ~ms); FIPS-197 KATs
# live in the ctfpilot.py selftest block evolve61-aeskey. Bounded:
# AES_MAX_KEYS labeled keys, AES_MAX_CT ciphertext candidates, first-
# block printable prune kills wrong (key, ct, mode) combos at
# one-block cost, AES_BUDGET_S shared wall guard emits ONE visible
# `budget` attempt on cut (rsi-130 semantics). The 16-multiple blob
# cap only admits 16..8192-byte candidates so the O(files x keys)
# face stays closed; provenance gate identical to every other stage.
AES_MAX_KEYS = 8
AES_MAX_CT = 8
AES_BLOB_MAX_BYTES = 8192
AES_BUDGET_S = 10.0
AES_HEAD_PRINTABLE = 0.5
_AES_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80,
             0x1B, 0x36, 0x6C, 0xD8, 0xAB, 0x4D)


def _gf_mul(a, b):
    p = 0
    while b:
        if b & 1:
            p ^= a
        a <<= 1
        if a & 0x100:
            a ^= 0x11B
        b >>= 1
    return p & 0xFF


def _build_aes_tables():
    # GF(2^8) inverse via log/antilog (generator 3), then the AES affine
    # transform; S(0) = 0x63 falls out of the same loop (inv[0] = 0).
    exp = [0] * 510
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x = _gf_mul(x, 3)
    for i in range(255):
        exp[i + 255] = exp[i]
    sbox = [0] * 256
    for a in range(256):
        b = exp[255 - log[a]] if a else 0
        s = b ^ 0x63
        for _ in range(4):
            b = ((b << 1) | (b >> 7)) & 0xFF
            s ^= b
        sbox[a] = s & 0xFF
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    mul = {n: [_gf_mul(n, b) for b in range(256)] for n in (9, 11, 13, 14)}
    return sbox, inv, mul


_AES_SBOX, _AES_INV_SBOX, _AES_MUL = _build_aes_tables()


def _aes_expand_key(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_AES_SBOX[b] for b in t]
            t[0] ^= _AES_RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            t = [_AES_SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return w, nr


def _aes_dec_block(w, nr, block):
    # state[r][c] held flat as s[4*c + r]; standard InvCipher.
    s = list(block)
    for c in range(4):
        for r in range(4):
            s[4 * c + r] ^= w[nr * 4 + c][r]
    for rnd in range(nr - 1, 0, -1):
        t = [0] * 16
        for r in range(4):
            for c in range(4):
                t[4 * c + r] = s[4 * ((c - r) % 4) + r]
        s = [_AES_INV_SBOX[b] for b in t]
        for c in range(4):
            for r in range(4):
                s[4 * c + r] ^= w[rnd * 4 + c][r]
        t = [0] * 16
        for c in range(4):
            a0, a1, a2, a3 = s[4 * c:4 * c + 4]
            t[4 * c + 0] = (_AES_MUL[14][a0] ^ _AES_MUL[11][a1]
                            ^ _AES_MUL[13][a2] ^ _AES_MUL[9][a3])
            t[4 * c + 1] = (_AES_MUL[9][a0] ^ _AES_MUL[14][a1]
                            ^ _AES_MUL[11][a2] ^ _AES_MUL[13][a3])
            t[4 * c + 2] = (_AES_MUL[13][a0] ^ _AES_MUL[9][a1]
                            ^ _AES_MUL[14][a2] ^ _AES_MUL[11][a3])
            t[4 * c + 3] = (_AES_MUL[11][a0] ^ _AES_MUL[13][a1]
                            ^ _AES_MUL[9][a2] ^ _AES_MUL[14][a3])
        s = t
    t = [0] * 16
    for r in range(4):
        for c in range(4):
            t[4 * c + r] = s[4 * ((c - r) % 4) + r]
    s = [_AES_INV_SBOX[b] for b in t]
    for c in range(4):
        for r in range(4):
            s[4 * c + r] ^= w[c][r]
    return bytes(s)


def _aes_ecb_decrypt(key, data):
    w, nr = _aes_expand_key(key)
    out = bytearray()
    for i in range(0, len(data), 16):
        out += _aes_dec_block(w, nr, data[i:i + 16])
    return bytes(out)


def _aes_cbc_decrypt(key, iv, data):
    w, nr = _aes_expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        blk = data[i:i + 16]
        dec = _aes_dec_block(w, nr, blk)
        out += bytes(a ^ b for a, b in zip(dec, prev))
        prev = blk
    return bytes(out)


def _pkcs7_valid(pt):
    if not pt:
        return False
    n = pt[-1]
    if not 1 <= n <= 16 or len(pt) < n:
        return False
    return all(b == n for b in pt[-n:])


def _printable_ratio(bs):
    if not bs:
        return 0.0
    ok = sum(1 for b in bs if 32 <= b < 127 or b in (9, 10, 13))
    return ok / len(bs)


# keyword (key / aes / 密钥) within a short label window of a 16..64
# printable run; post-filter keeps only hex-key (32/48/64 hex chars)
# and exact ASCII-key (16/24/32 bytes) shapes. Greedy capture stops at
# the first non-printable, so '密钥（ThatsASecretKeyO）' and
# '"aes_key_hex": "A1B2..."' both land intact while 'key=abcdefghijklmnop'
# style prose noise is bounded by the exact-length post-filter.
_AES_KEYCTX_RE = re.compile(r"(?:key|aes|密钥)[^\n]{0,12}?([!-~]{16,64})",
                            re.IGNORECASE)
_AES_HEXCT_RE = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{32,4096})(?![0-9A-Fa-f])")
_AES_BIN_MAGIC_SKIP = (b"\x89PNG", b"PK\x03\x04", b"PK\x05\x06",
                       b"PK\x07\x08", b"\x1f\x8b", b"%PDF", b"\x7fELF",
                       b"hsqs", b"sqsh", b"Rar!")


def _aes_extract_keys(text):
    keys = []
    for m in _AES_KEYCTX_RE.finditer(text):
        # greedy capture stops at the first non-printable, so a quoted
        # value lands with its closing quote absorbed ("A1B2...\" = 33);
        # strip the quote chars before the exact-shape filter.
        cand = m.group(1).strip("\"'")
        k = None
        if len(cand) in (32, 48, 64):
            try:
                k = bytes.fromhex(cand)
            except ValueError:
                k = None
        elif len(cand) in (16, 24, 32):
            k = cand.encode("ascii")
        if k and k not in keys:
            keys.append(k)
            if len(keys) >= AES_MAX_KEYS:
                break
    return keys


def _aes_ct_candidates(inputs):
    """(src, bytes) ciphertext candidates: 16..8192B 16-multiples.
    Text hex blobs (whole-file text only; per-line inputs are line
    slices of the same text) plus raw [bytes] files with no known
    container magic. Dedup by content across both faces."""
    cts = []
    seen = set()
    for src, data in inputs:
        if isinstance(data, str) and "#L" not in src:
            for m in _AES_HEXCT_RE.finditer(data):
                h = m.group(1)
                if len(h) % 32 or len(h) // 2 > AES_BLOB_MAX_BYTES:
                    continue
                try:
                    b = bytes.fromhex(h)
                except ValueError:
                    continue
                if b not in seen:
                    seen.add(b)
                    cts.append(("%s #hex" % src, b))
        elif isinstance(data, bytes):
            if not 16 <= len(data) <= AES_BLOB_MAX_BYTES or len(data) % 16:
                continue
            if data[:4] in _AES_BIN_MAGIC_SKIP or data[:2] in _AES_BIN_MAGIC_SKIP:
                continue
            if data not in seen:
                seen.add(data)
                cts.append((src, data))
        if len(cts) >= AES_MAX_CT:
            break
    return cts


def aes_known_key_scan(checker, inputs):
    """Known-key AES triage: ECB / CBC-zero-IV / CBC-IV-prefix.

    Returns (flag|None, note). Empty note when no labeled key exists
    (attempt-stream parity for every non-AES challenge)."""
    keys = []
    for src, data in inputs:
        if not isinstance(data, str) or "#L" in src or "[bytes]" in src:
            continue
        for k in _aes_extract_keys(data):
            if k not in keys:
                keys.append(k)
        if len(keys) >= AES_MAX_KEYS:
            break
    if not keys:
        return None, ""
    cts = _aes_ct_candidates(inputs)
    if not cts:
        return None, "%d labeled key(s), no ciphertext candidate" % len(keys)
    sched = {}
    t0 = time.perf_counter()
    for _i61, (src, ct) in enumerate(cts):
        if time.perf_counter() - t0 > AES_BUDGET_S:
            return None, ("budget: %d/%d ct candidate(s) tried, AES wall "
                          "budget %.0fs exhausted [evolve-20261118-17]"
                          % (_i61, len(cts), AES_BUDGET_S))
        for k in keys:
            if k not in sched:
                sched[k] = _aes_expand_key(k)
            w, nr = sched[k]
            for mode in ("ecb", "cbc0", "ivpfx"):
                if mode == "ecb":
                    head = _aes_dec_block(w, nr, ct[:16])
                    body_src, iv, body = None, None, ct
                elif mode == "cbc0":
                    head = bytes(a ^ b for a, b in zip(
                        _aes_dec_block(w, nr, ct[:16]), b"\x00" * 16))
                    iv, body = b"\x00" * 16, ct
                else:
                    if len(ct) < 32:
                        continue
                    head = bytes(a ^ b for a, b in zip(
                        _aes_dec_block(w, nr, ct[16:32]), ct[:16]))
                    iv, body = ct[:16], ct[16:]
                if _printable_ratio(head) < AES_HEAD_PRINTABLE:
                    continue
                if mode == "ecb":
                    pt = _aes_ecb_decrypt(k, body)
                else:
                    pt = _aes_cbc_decrypt(k, iv, body)
                if not _pkcs7_valid(pt) and _printable_ratio(pt) < 0.97:
                    continue
                if _pkcs7_valid(pt):
                    pt = pt[:-pt[-1]]
                found = _flag_text(checker, pt)
                if found:
                    _mode_names = {"ecb": "ECB", "cbc0": "CBC-zero-IV",
                                   "ivpfx": "CBC-IV-prefix"}
                    return found, ("%s: AES-%d-%s known key (labeled in "
                                   "text, %d key(s) x 3 modes triage)"
                                   % (src, len(k) * 8, _mode_names[mode],
                                      len(keys)))
    return None, ("%s: no flag under %d labeled key(s) x 3 modes"
                  % (cts[0][0], len(keys)))


# Stage 7.6 hprof-literal-triage -----------------------------------------------
# [evolve-20261118-19 / rsi-142] NEW fastpath stage 7.6 hprof-literal-triage:
# Java heap dump (hprof) string-literal extraction. Origin specimen:
# 2024-ShuXinCup-sjaq-easyjava (ledger 114, engine honest NOT SOLVED
# 2.81s/18 attempts -- the store's FIRST hprof-family specimen): the
# 1.9MB "JAVA PROFILE 1.0.1" dump carried the flag inside heap char[]
# arrays, and the hand-crafted dump stored them BIG-ENDIAN, so every
# naive UTF-16LE read produced shifted CJK junk (the real ASCII lived
# on the hi-byte lane, proven live by JVM messages 'Stream closed' /
# 'malformed input: partial character at end' in the same dump).
# This stage parses the HPROF record stream directly (no strings-mode
# collateral): STRING records (0x01) and primitive-array records (0x23)
# inside HEAP_DUMP segments, emitting FOUR decode lanes per char[]
# (UTF-16BE / UTF-16LE / hi-byte stream / lo-byte stream) plus one
# UTF-8 lane per byte[]; every lane runs the SAME _flag_text acceptance
# chain as every other stage (printable gate + _GARBAGE_FLAG_RE +
# rsi-140 _implausible_flag + checker.find), and the run_fast_path
# call site applies the identical raw-echo provenance gate. Sub-record
# walking skips CLASS DUMP (id + u4 + 6*id + u4 header, cp/static
# value-size map), INSTANCE / OBJECT-ARRAY / root records via the
# layouts verified on the live specimen (4 honest resyncs there);
# unknown tags resync to the next 0x23 candidate. Bounded:
# HPROF_MAX_BYTES file cap, HPROF_MAX_ARRAYS array cap,
# HPROF_ARRAY_MAX_BYTES per-array cap, HPROF_BUDGET_S shared wall guard
# emitting ONE visible budget attempt on cut (rsi-130 semantics).
# Stage QUIET (empty note, zero attempts) when no file starts with the
# b"JAVA PROFILE 1.0" magic, so non-hprof trees keep a byte-identical
# attempt stream. No other stage, collection regex, provenance rule or
# the ledger keyspace is touched (single-variable change).
HPROF_MAGIC = b"JAVA PROFILE 1.0"
HPROF_MAX_BYTES = 8 * 1024 * 1024
HPROF_MAX_ARRAYS = 4096
HPROF_ARRAY_MAX_BYTES = 128 * 1024
HPROF_BUDGET_S = 10.0
_HPROF_PRIM_ESIZE = {4: 1, 5: 2, 6: 4, 7: 8, 8: 1, 9: 2, 10: 4, 11: 8}
_HPROF_ROOT_EXTRA = {0x01: 0, 0x02: 8, 0x03: 8, 0x04: 8, 0x05: 4,
                     0x06: 4, 0x07: 4, 0x08: 0, 0x0A: 8}


def _hprof_skip_class_dump(body, q):
    """CLASS DUMP (0x20) length walk with the id+u4+6*id+u4 header verified
    on the live 1.0.1 specimen; returns next offset (best-effort)."""
    q += 1 + 8 + 4 + 6 * 8 + 4
    for _ in range(struct.unpack_from(">H", body, q)[0]):
        q += 2 + 1 + _HPROF_PRIM_ESIZE.get(body[q - 1], 8)
    for _ in range(struct.unpack_from(">H", body, q)[0]):
        q += 8 + 1 + _HPROF_PRIM_ESIZE.get(body[q - 1], 8)
    nif = struct.unpack_from(">H", body, q)[0]
    return q + 2 + nif * 9


def _hprof_iter_arrays(body):
    """Yield (tc, raw) for each primitive-array record inside a heap
    segment body. Self-describing sub-records advance exactly; unknown
    tags resync to the next 0x23 byte (the live specimen needed 4)."""
    q = 0
    L = len(body)
    while q < L:
        try:
            st = body[q]
            if st == 0x23:
                nelem = struct.unpack_from(">I", body, q + 13)[0]
                tc = body[q + 17]
                esize = _HPROF_PRIM_ESIZE.get(tc)
                if esize is None:
                    q += 18
                    continue
                nbytes = nelem * esize
                if nbytes > HPROF_ARRAY_MAX_BYTES:
                    q += 18 + nbytes
                    continue
                yield tc, body[q + 18:q + 18 + nbytes]
                q += 18 + nbytes
            elif st == 0x20:
                q = _hprof_skip_class_dump(body, q)
            elif st == 0x21:
                nb = struct.unpack_from(">I", body, q + 21)[0]
                q += 1 + 8 + 4 + 8 + 4 + nb
            elif st == 0x22:
                nelem = struct.unpack_from(">I", body, q + 13)[0]
                q += 1 + 8 + 4 + 4 + 8 + nelem * 8
            elif st in _HPROF_ROOT_EXTRA:
                q += 1 + 8 + _HPROF_ROOT_EXTRA[st]
            elif st == 0xFF:
                q += 1
            else:
                nxt = body.find(b"\x23", q + 1)
                q = nxt if nxt >= 0 else L
        except (struct.error, IndexError):
            nxt = body.find(b"\x23", q + 1)
            q = nxt if nxt >= 0 else L


def hprof_literal_scan(checker, files):
    """hprof string-literal triage: STRING records + heap char[]/byte[]
    lanes (BE / LE / hi-byte / lo-byte) -> _flag_text.

    Returns (flag|None, note). Empty note when no hprof file exists
    (attempt-stream parity for every non-hprof challenge)."""
    t0 = time.perf_counter()
    hp = None
    for f in files:
        try:
            if (f.get("size") or 0) <= 0 or (f.get("size") or 0) > HPROF_MAX_BYTES:
                continue
            with open(f["abs"], "rb") as fh:
                head = fh.read(len(HPROF_MAGIC))
                if head.startswith(HPROF_MAGIC):
                    fh.seek(0)
                    hp = (f["path"], fh.read(HPROF_MAX_BYTES + 1))
                    break
        except (OSError, KeyError):
            continue
    if hp is None or len(hp[1]) > HPROF_MAX_BYTES:
        return None, ""
    src, data = hp
    nul = data.find(b"\x00", 0, 40)
    if nul < 0:
        return None, ""
    idsize = struct.unpack_from(">I", data, nul + 1)[0]
    if idsize not in (4, 8):
        return None, ""
    pos = nul + 1 + 4 + 8
    n_str = n_arr = 0
    while pos + 9 <= len(data):
        if time.perf_counter() - t0 > HPROF_BUDGET_S:
            return None, ("budget: hprof wall budget %.0fs exhausted, %d "
                          "string record(s) + %d array(s) seen [evolve-"
                          "20261118-19]" % (HPROF_BUDGET_S, n_str, n_arr))
        tag = data[pos]
        rlen = struct.unpack_from(">I", data, pos + 5)[0]
        if pos + 9 + rlen > len(data):
            break
        body = data[pos + 9:pos + 9 + rlen]
        if tag == 0x01:
            if len(body) > idsize:
                text = body[idsize:].decode("utf-8", "replace")
                n_str += 1
                found = _flag_text(checker, text)
                if found:
                    return found, ("%s: hprof STRING record #%d"
                                   % (src, n_str))
        elif tag in (0x0C, 0x2C):
            for tc, raw in _hprof_iter_arrays(body):
                if n_arr >= HPROF_MAX_ARRAYS:
                    break
                if time.perf_counter() - t0 > HPROF_BUDGET_S:
                    return None, ("budget: hprof wall budget %.0fs exhausted,"
                                  " %d string record(s) + %d array(s) seen"
                                  " [evolve-20261118-19]"
                                  % (HPROF_BUDGET_S, n_str, n_arr))
                n_arr += 1
                if tc == 5 and len(raw) >= 4:
                    lanes = (
                        ("BE", raw.decode("utf-16-be", "replace")),
                        ("LE", raw.decode("utf-16-le", "replace")),
                        ("hi-byte", bytes(raw[i + 1]
                                          for i in range(0, len(raw) - 1, 2))),
                        ("lo-byte", bytes(raw[i]
                                          for i in range(0, len(raw) - 1, 2))),
                    )
                elif tc == 8 and len(raw) >= 4:
                    lanes = (("bytes", raw),)
                else:
                    continue
                for lane_name, lane in lanes:
                    found = _flag_text(checker, lane)
                    if found:
                        return found, ("%s: hprof %s lane, array #%d"
                                       % (src, lane_name, n_arr))
        pos += 9 + rlen
    if n_str == 0 and n_arr == 0:
        return None, ""
    return None, ("%s: hprof triage %d string record(s) + %d array(s),"
                  " no flag" % (src, n_str, n_arr))


def run_fast_path(chall_dir, files, checker, attempts, time_budget_s=None):
    """Run all fast detectors. Returns flag or None. Appends Attempt records.

    rsi-049 (2026-11-16): raw-echo provenance moved INTO the fast path.
    A flag found verbatim in the original challenge files is a challenge-
    provided placeholder, not a derived solve -- previously such a hit
    short-circuited run_fast_path before later candidates were scanned
    (2024-zjsdxs sign-in: the whole-README placeholder masked the per-line
    base92 payload). Now raw-echo hits are logged as `provenance` attempts
    and scanning continues; do_solve's own provenance gate is unchanged.

    [evolve-20261117-04] The O(files x keys) brute stages (xor-single,
    mz-xor, xor-token, png-ihdr, png-lsb) share one wall-clock budget
    (budget.fastpath_time_limit_s, default 60s, previously never read).
    When the deadline passes, the remaining files of a brute stage are
    skipped and a visible `budget` attempt is recorded per cut stage.
    The check is per-file so an in-flight file always completes (normal
    1-3-file challenges keep their full search space); text stages
    (chain / caesar / js-unicode) stay unbounded this round.
    """
    from core.flagcheck import flag_in_raw   # lazy: keep import cycle impossible

    # [evolve-20261117-04] perf_counter, NOT monotonic: on Windows
    # time.monotonic() ticks at 15.625ms, so every check inside one tick
    # reads elapsed == 0.0 and a tight budget never fires. perf_counter is
    # the high-resolution duration clock (~100ns on Windows) and is what
    # budget deadlines must use.
    budget_s = _fastpath_budget_s(time_budget_s)
    t_fast0 = time.perf_counter()

    def _budget_left():
        return budget_s is None or (time.perf_counter() - t_fast0) < budget_s

    def _budget_cut(stage, scanned, total):
        attempts.append(Attempt(
            "budget",
            "%s: fastpath brute budget %.0fs exhausted, %d/%d inputs scanned,"
            " rest skipped [evolve-20261117-04]"
            % (stage, budget_s, scanned, total),
            False))

    def _raw_echo(f):
        return bool(f) and flag_in_raw(chall_dir, f)[0]

    flag = None
    inputs = _candidate_inputs(chall_dir, files)

    # 1) Chain unwrap on every text input (whole file + each line)
    # [evolve-20261118-13 / rsi-133] every appended stage-1 row (miss note
    # or raw-echo provenance) charges 1; when dry the stage stops and ONE
    # visible `budget` attempt records the cut, so the deep path and the
    # ledger see an honest cut line instead of a silent truncation.
    _chain1_budget = CHAIN1_MAX_ATTEMPTS
    _chain1_cut = False
    for src, data in inputs:
        if not isinstance(data, str):
            continue
        found, note = chain_decode(checker, data)
        if found:
            if _raw_echo(found):
                if _chain1_budget <= 0:
                    _chain1_cut = True
                    break
                _chain1_budget -= 1
                attempts.append(Attempt("provenance",
                                        "raw-echo in %s (fastpath skip): %s" % (src, note), False))
                continue
            attempts.append(Attempt("chain-decode", "%s: %s" % (src, note), True, found))
            return found
        if src.endswith("#L1") or "[bytes]" not in src and len(inputs) <= 12:
            _chain1_budget -= 1
            attempts.append(Attempt("chain-decode", "%s: %s" % (src, note), False))
            if _chain1_budget <= 0:
                _chain1_cut = True
                break
    if _chain1_cut:
        attempts.append(Attempt(
            "budget",
            "chain: stage-1 row cap %d exhausted, %d row(s) charged, remaining "
            "inputs skipped [evolve-20261118-13]"
            % (CHAIN1_MAX_ATTEMPTS, CHAIN1_MAX_ATTEMPTS - _chain1_budget),
            False))

    # 1.2) Chain unwrap on wrapped encoder tokens
    # [evolve-20261117-29 / rsi-099] backtick/quote spans carry payloads
    # in prose briefs; whole-file text only (spans never cross lines, so
    # per-line inputs would re-extract the same tokens). Budget-capped:
    # TOK_MAX_TOTAL bounds the whole stage, TOK_MAX_PER_FILE bounds each
    # file; provenance gate identical to stage 1 (a token that decodes
    # to a raw-echo flag is retracted and scanning continues).
    tok_budget = TOK_MAX_TOTAL
    tok_seen = set()
    # [evolve-20261118-14 / rsi-134] two-pass stage 1.2: collect exactly as
    # the incumbent did (first-come cross-input dedup, per-file cap kept by
    # wrapped_tokens itself), then consume non-asset groups before
    # asset-shaped groups (stable sort). Order-only: nothing is dropped, a
    # credited find returns exactly as before, and a tree whose token total
    # stays under TOK_MAX_TOTAL is consumed in full -- only the per-file
    # miss-note order can differ from the incumbent walk order.
    tok_groups = []
    for src, data in inputs:
        if not isinstance(data, str) or "#L" in src:
            continue
        toks = [t for t in wrapped_tokens(data) if t not in tok_seen]
        if not toks:
            continue
        tok_seen.update(toks)
        tok_groups.append((src, toks))
    tok_groups.sort(key=lambda g: 1 if _TOK_ASSET_RE.search(g[0]) else 0)
    for src, toks in tok_groups:
        if tok_budget <= 0:
            continue
        for tok in toks:
            if tok_budget <= 0:
                break
            tok_budget -= 1
            found, note = chain_decode(checker, tok)
            if found:
                if _raw_echo(found):
                    attempts.append(Attempt(
                        "provenance",
                        "raw-echo in %s #tok (fastpath skip): %s" % (src, note),
                        False))
                    continue
                attempts.append(Attempt("chain-decode",
                                         "%s #tok: %s" % (src, note),
                                         True, found))
                return found
        attempts.append(Attempt(
            "chain-decode",
            "%s #tok: no flag in %d wrapped token(s)" % (src, len(toks)),
            False))

    # 1.3) IDNA/punycode domain labels
    # [evolve-20261117-36 / rsi-105] xn-- labels carry punycode-decodable
    # evidence. Whole-file text only (labels never cross lines); strict
    # stdlib idna codec, decode failures and pure-ASCII results drop
    # harmlessly (the codec round-trip-checks, so a pure-ASCII label like
    # xn--flag{abc}- raises UnicodeError by itself); a non-ASCII decode
    # without a flag is reported as a decoded-value evidence note so the
    # deep path / model channel gets the value without re-decoding.
    # Provenance gate identical to stages 1/1.2.
    idna_budget = IDNA_MAX_TOTAL
    idna_seen = set()
    for src, data in inputs:
        if (not isinstance(data, str) or "#L" in src
                or idna_budget <= 0 or "xn--" not in data):
            continue
        toks = [t for t in idna_tokens(data) if t not in idna_seen]
        if not toks:
            continue
        idna_seen.update(toks)
        notes = []
        for tok in toks:
            if idna_budget <= 0:
                break
            idna_budget -= 1
            try:
                dec = tok.encode("ascii").decode("idna")
            except (UnicodeError, ValueError):
                continue
            if not dec or all(ord(c) < 128 for c in dec):
                continue
            found = _flag_text(checker, dec)
            if found:
                if _raw_echo(found):
                    attempts.append(Attempt(
                        "provenance",
                        "raw-echo in %s #xn (fastpath skip): idna %s"
                        % (src, tok), False))
                    continue
                attempts.append(Attempt(
                    "idna", "%s #xn: %s decodes to %r, flag in decoded text"
                    % (src, tok, dec[:60]), True, found))
                return found
            notes.append("%r" % dec[:40])
        if notes:
            attempts.append(Attempt(
                "idna",
                "%s #xn: %d punycode label(s) decode, no flag in decoded text (%s)"
                % (src, len(notes), ", ".join(notes[:4])), False))

    # 1.5) JS unicode-escape literals (var secret='\u0066..' style payloads)
    for src, data in inputs:
        if not isinstance(data, str):
            continue
        ue_found, ue_note = js_unicode_escape(checker, data)
        if ue_found:
            if _raw_echo(ue_found):
                attempts.append(Attempt("provenance",
                                        "raw-echo in %s (fastpath skip)" % src, False))
                continue
            attempts.append(Attempt("js-unicode-escape", "%s: %s" % (src, ue_note), True, ue_found))
            return ue_found
    attempts.append(Attempt("js-unicode-escape", "all text inputs: no escape literals or no flag", False))

    # [evolve-20261117-38 / rsi-109] per-line caesar subsumption: _candidate_inputs
    # emits "<path> #L<n>" lines right after their whole-file text, and the
    # caesar sweep used to pay all 25 shifts on every one of them. A shift is a
    # byte-wise translation, so shifted(L) is a literal substring of shifted(F)
    # for every line L of file F: with F's whole-file sweep present, a per-line
    # sweep can only add a hit when the whole-file sweep found a match it did
    # NOT credit -- a RAW-ECHO hit (first match masks a later line's derived
    # flag at the same shift) or a garbage-gated match (_flag_text returns None
    # on U+FFFD/control chars, again masking a later clean line match). Files
    # whose whole sweep is clean in both senses have provably all-miss per-line
    # passes; those passes are skipped. Raw/garbage-masked files fall back to
    # the exact incumbent behavior. Verdict/flag semantics unchanged; the only
    # observable diff is fewer per-line caesar miss notes.
    _caesar_whole_srcs = set()
    for _s38, _d38 in inputs:
        if isinstance(_d38, str) and not _CAESAR_LINE_RE.match(_s38):
            _caesar_whole_srcs.add(_s38)
    _caesar_last_whole = None
    _caesar_last_clean = False
    # [evolve-20261118-10 / rsi-130] total-attempt budget for this stage.
    # Every appended caesar/provenance miss row charges 1; a credited find
    # returns before any charge. When the budget runs dry the stage stops
    # and ONE visible `budget` attempt is recorded (evolve-20261117-04
    # semantics), so the deep path and the ledger see an honest cut line
    # instead of a silent truncation.
    _caesar_budget = CAESAR_MAX_ATTEMPTS
    _caesar_cut = False
    for src, data in inputs:
        if not isinstance(data, str):
            continue
        _m38 = _CAESAR_LINE_RE.match(src)
        _parent38 = (_m38.group("parent")
                     if (_m38 and _m38.group("parent") in _caesar_whole_srcs)
                     else None)
        if _parent38 is not None and _parent38 == _caesar_last_whole \
                and _caesar_last_clean:
            continue
        _raw38 = False
        _mask38 = False
        for shift, dec in caesar_sweep(data):
            if _parent38 is None:
                # whole-file probe: exact str-branch replica of _flag_text
                # (checker.find + garbage gate) that also reports a masked
                # match, so the clean/masked distinction is observable
                # without paying the regex twice.
                found = checker.find(dec) if dec else None
                if found and (_GARBAGE_FLAG_RE.search(found)
                              or _implausible_flag(found)):
                    _mask38 = True
                    found = None
            else:
                found = _flag_text(checker, dec)
            if found:
                if _raw_echo(found):
                    _raw38 = True
                    if _caesar_budget <= 0:
                        _caesar_cut = True
                        break
                    _caesar_budget -= 1
                    attempts.append(Attempt("provenance",
                                            "raw-echo in %s shift=%d (fastpath skip)" % (src, shift), False))
                    continue
                attempts.append(Attempt("caesar", "%s shift=%d" % (src, shift), True, found))
                return found
        if _caesar_cut:
            break
        if _parent38 is None:
            _caesar_last_whole = src
            _caesar_last_clean = not (_raw38 or _mask38)
        _caesar_budget -= 1
        attempts.append(Attempt("caesar", "%s: 25 shifts no flag" % src, False))
        if _caesar_budget <= 0:
            _caesar_cut = True
            break
    if _caesar_cut:
        attempts.append(Attempt(
            "budget",
            "caesar: attempt cap %d exhausted, %d row(s) charged, remaining "
            "inputs skipped [evolve-20261118-10]"
            % (CAESAR_MAX_ATTEMPTS, CAESAR_MAX_ATTEMPTS - _caesar_budget),
            False))

    # 2.5) Progressive/affine caesar (per-position arithmetic shifts,
    # delta_i = a + b*i mod 26, b != 0) via flag-anchor back-solve.
    # [evolve-20261117-40 / rsi-110] constant shifts above stay the
    # incumbent caesar stage's domain; this arm only covers shifts the
    # 25-shift sweep provably cannot reach. Zero miss notes by design
    # (the green side keeps the exact incumbent attempt count); the
    # provenance gate is identical to every other text stage.
    for src, data in inputs:
        if not isinstance(data, str):
            continue
        pg_found, pg_note = progressive_caesar_sweep(checker, data)
        if pg_found:
            if _raw_echo(pg_found):
                attempts.append(Attempt(
                    "provenance",
                    "raw-echo in %s (fastpath skip): %s" % (src, pg_note),
                    False))
                continue
            attempts.append(Attempt(
                "progressive-caesar", "%s: %s" % (src, pg_note),
                True, pg_found))
            return pg_found

    # 3) Single-byte XOR sweep on raw byte inputs [evolve-20261117-04]:
    # per-file budget check keeps single-file search integrity.
    n_bytes = sum(1 for _s, d in inputs if isinstance(d, bytes))
    scanned = 0
    for src, data in inputs:
        if not isinstance(data, bytes):
            continue
        if not _budget_left():
            _budget_cut("xor-single", scanned, n_bytes)
            break
        scanned += 1
        for k, dec in xor_sweep_single(data):
            found = _flag_text(checker, dec)
            if found:
                if _raw_echo(found):
                    attempts.append(Attempt("provenance",
                                            "raw-echo in %s key=0x%02x (fastpath skip)" % (src, k), False))
                    continue
                attempts.append(Attempt("xor-single", "%s key=0x%02x" % (src, k), True, found))
                return found
        attempts.append(Attempt("xor-single", "%s: 255 keys no flag" % src, False))

    # 3.5) Multi-byte cyclic XOR over MZ-encrypted PE samples (self-reads
    # files: _candidate_inputs caps at 2MB and malware-sample challenges
    # exceed it; source 2024-dfjk-Misc default.a 4.6MB)
    # [evolve-20261117-04] one-shot brute stage: budget check before the call.
    if _budget_left():
        mz_found, mz_note = mz_xor_key_recovery(checker, files)
        if mz_found:
            if _raw_echo(mz_found):
                attempts.append(Attempt("provenance",
                                        "raw-echo (fastpath skip): %s" % mz_note, False))
            else:
                attempts.append(Attempt("mz-xor-key-recovery", mz_note, True, mz_found))
                return mz_found
        attempts.append(Attempt("mz-xor-key-recovery", mz_note, False))
    else:
        _budget_cut("mz-xor-key-recovery", 0, len(files))

    # 4) Token-key XOR (bridge strategy: hint words inside challenge files)
    # [evolve-20261117-04] per-file budget check, same shape as xor-single.
    tokens = extract_tokens(files)
    scanned_tok = 0
    for src, data in inputs:
        if not isinstance(data, bytes):
            continue
        if not _budget_left():
            _budget_cut("xor-token", scanned_tok, n_bytes)
            break
        scanned_tok += 1
        for tok in tokens:
            dec = xor_with_key(data, tok)
            if dec is None or not _looks_meaningful(dec):
                continue
            found = _flag_text(checker, dec)
            if found:
                if _raw_echo(found):
                    attempts.append(Attempt("provenance",
                                            "raw-echo in %s key=%s (fastpath skip)" % (src, tok), False))
                    continue
                attempts.append(Attempt("xor-token", "%s key=%s" % (src, tok), True, found))
                return found
    if tokens and scanned_tok:
        attempts.append(Attempt("xor-token", "%d token keys tried, no flag" % len(tokens), False))

    # 5) Decompress binaries, then chain-decode the inner text
    for src, data in inputs:
        if not isinstance(data, bytes):
            continue
        out = _try_gzip_zlib(data)
        if out is not None:
            inner = out.decode("utf-8", errors="replace")
            found, note = chain_decode(checker, inner)
            if found:
                if _raw_echo(found):
                    attempts.append(Attempt("provenance",
                                            "raw-echo in %s (fastpath skip): %s" % (src, note), False))
                    continue
                attempts.append(Attempt("decompress+chain", "%s: %s" % (src, note), True, found))
                return found
        attempts.append(Attempt("decompress", "%s: no" % src, False))

    # 5.5) PNG IHDR width/height tamper recovery (rsi-054): recover the real
    # geometry from the stored crc, validate on row filters, probe for flags.
    # Reads files directly: large PNGs exceed _candidate_inputs' 2 MB cap
    # (2025-jswa-misc2 is 2.5 MB and would otherwise never be seen here).
    # [evolve-20261117-04] per-file budget check.
    scanned_png = 0
    for f in files:
        if not _budget_left():
            _budget_cut("png-ihdr-crc", scanned_png, len(files))
            break
        scanned_png += 1
        try:
            with open(f["abs"], "rb") as fh:
                data = fh.read()
        except (OSError, KeyError):
            continue
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            continue
        found, note = png_ihdr_crc_fix(checker, data)
        if found:
            if _raw_echo(found):
                attempts.append(Attempt("provenance",
                                        "raw-echo in %s (fastpath skip): %s" % (f["path"], note), False))
                continue
            attempts.append(Attempt("png-ihdr-crc", "%s: %s" % (f["path"], note), True, found))
            return found
        if note and note != "ihdr crc ok (no tamper)":
            attempts.append(Attempt("png-ihdr-crc", "%s: %s" % (f["path"], note), False))

    # 5.6) PDF FlateDecode text extraction (writeup-in-archive family:
    # 2024-OMCTF writeup.pdf, RED 1.87s NOT SOLVED -> GREEN via CID CMap
    # reassembly, [evolve-20261117-44 / rsi-116]). One-shot brute-stage
    # shape: budget check before the call. Non-PDF files never reach an
    # attempt record, so non-PDF challenges keep attempt streams
    # byte-identical to the incumbent.
    if _budget_left():
        pdf_found, pdf_note = pdf_text_scan(checker, files)
        if pdf_found:
            if _raw_echo(pdf_found):
                attempts.append(Attempt(
                    "provenance",
                    "raw-echo (fastpath skip): %s" % pdf_note, False))
            else:
                attempts.append(Attempt("pdf-text", pdf_note, True, pdf_found))
                return pdf_found
        if pdf_note and pdf_note != "no PDF text-stream candidate":
            attempts.append(Attempt("pdf-text", pdf_note, False))
    else:
        _budget_cut("pdf-text", 0, len(files))

    # 6) PNG LSB stego (channels' bit0..2, interleaved RGB/BGR)
    # [evolve-20261117-04] per-file budget check after the PNG filter.
    scanned_lsb = 0
    for src, data in inputs:
        if not isinstance(data, bytes) or b"\x89PNG\r\n\x1a\n" not in data[:16]:
            continue
        if not _budget_left():
            _budget_cut("png-lsb", scanned_lsb, n_bytes)
            break
        scanned_lsb += 1
        found, note = png_lsb_scan(checker, data)
        if found:
            if _raw_echo(found):
                attempts.append(Attempt("provenance",
                                        "raw-echo in %s (fastpath skip): %s" % (src, note), False))
                continue
            attempts.append(Attempt("png-lsb", "%s: %s" % (src, note), True, found))
            return found
        attempts.append(Attempt("png-lsb", "%s: %s" % (src, note), False))

    # 7) RSA numeric attacks (structured primes / Wiener on n^k)
    found, note = rsa_numeric_scan(checker, inputs)
    if found:
        if _raw_echo(found):
            attempts.append(Attempt("provenance",
                                    "raw-echo (fastpath skip): %s" % note, False))
            found = None
        else:
            attempts.append(Attempt("rsa-numeric", note, True, found))
            return found
    if note:
        attempts.append(Attempt("rsa-numeric", note, False))

    # 7.5) Known-key symmetric decrypt (AES ECB / CBC0 / CBC-IV-prefix)
    # [evolve-20261118-17 / rsi-139] one-shot brute-stage shape: budget
    # check before the call, ONE row per outcome, empty note when no
    # labeled key exists so non-AES trees keep a byte-identical
    # attempt stream. Provenance gate identical to every other stage.
    if _budget_left():
        aes_found, aes_note = aes_known_key_scan(checker, inputs)
        if aes_found:
            if _raw_echo(aes_found):
                attempts.append(Attempt(
                    "provenance",
                    "raw-echo (fastpath skip): %s" % aes_note, False))
            else:
                attempts.append(Attempt("aes-known-key", aes_note,
                                        True, aes_found))
                return aes_found
        if aes_note:
            attempts.append(Attempt("aes-known-key", aes_note, False))
    else:
        _budget_cut("aes-known-key", 0, len(inputs))

    # 7.6) Java heap dump (hprof) string-literal triage
    # [evolve-20261118-19 / rsi-142] one-shot brute-stage shape: budget
    # check before the call, ONE row per outcome, empty note when no
    # b"JAVA PROFILE 1.0" file exists so non-hprof trees keep a
    # byte-identical attempt stream. Provenance gate identical to every
    # other stage.
    if _budget_left():
        hprof_found, hprof_note = hprof_literal_scan(checker, files)
        if hprof_found:
            if _raw_echo(hprof_found):
                attempts.append(Attempt(
                    "provenance",
                    "raw-echo (fastpath skip): %s" % hprof_note, False))
            else:
                attempts.append(Attempt("hprof-literal", hprof_note,
                                        True, hprof_found))
                return hprof_found
        if hprof_note:
            attempts.append(Attempt("hprof-literal", hprof_note, False))
    else:
        _budget_cut("hprof-literal", 0, len(files))

    return flag


def run_quick_pass(chall_dir, files, checker):
    """Convenience wrapper used by deep path as strategy #0."""
    attempts = []
    flag = run_fast_path(chall_dir, files, checker, attempts)
    return flag, attempts
