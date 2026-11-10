"""Fast path: deterministic detectors for common challenge patterns.

Goal: "simple challenges solved in seconds, offline, no LLM".
Each attempt is recorded so the trajectory store knows what was already
tried before the deep path spends budget on it.

Detectors implemented (all pure stdlib, all offline):
  - base64 (std + urlsafe, with/without padding)
  - base32, hex, url-encoding
  - caesar sweep (all 26 shifts), rot13, atbash
  - reverse string
  - zlib/gzip raw inflate
  - single-byte XOR sweep (0..255)
  - chained decoding: recursive unwrap up to depth 8
  - token-key XOR: try words found inside the challenge files as XOR keys
    (this is the bridge into the deep path: hint-driven, still offline)
"""
import base64
import binascii
import codecs
import gzip
import os
import re
import string
import zlib

MAX_CHAIN_DEPTH = 8
PRINTABLE = set(bytes(string.printable, "ascii"))


class Attempt:
    def __init__(self, detector, detail, success, flag=None):
        self.detector = detector
        self.detail = detail
        self.success = success
        self.flag = flag


def _flag_text(checker, data):
    """Best-effort: bytes or str -> flag if printable text contains one."""
    if isinstance(data, bytes):
        printable_ratio = sum(1 for b in data[:512] if b in PRINTABLE) / max(1, min(512, len(data)))
        if printable_ratio < 0.7:
            return None
        text = data.decode("utf-8", errors="replace")
    else:
        text = data
    return checker.find(text)


def _candidate_inputs(chall_dir, files):
    """Decoder inputs: whole-file text + each non-trivial line + raw bytes.
    Per-line candidates matter: real challenges embed payloads in prose."""
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
                for i, line in enumerate(txt.splitlines()):
                    line = line.strip()
                    if len(line) >= 8 and line != txt.strip():
                        inputs.append(("%s #L%d" % (f["path"], i + 1), line))
        inputs.append((f["path"] + " [bytes]", raw))
    return inputs


def _try_base64(data):
    if isinstance(data, str):
        s = data.strip().replace("\n", "").replace(" ", "")
        try:
            raw = s.encode("ascii")
        except UnicodeEncodeError:
            return None
    else:
        raw = data
    for pad in ("", "=" * (-len(raw) % 4)):
        for alt in (False, True):
            try:
                out = base64.b64decode(raw + pad.encode(), validate=False) if not alt \
                    else base64.urlsafe_b64decode(raw + pad.encode())
                if out and _looks_meaningful(out):
                    return out
            except (binascii.Error, ValueError):
                continue
    return None


def _try_base32(data):
    s = data.strip().replace("\n", "").replace(" ", "") if isinstance(data, str) else None
    if not s:
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
    clean = re.sub(r"^0x", "", s, flags=re.IGNORECASE)
    clean = re.sub(r"[\s,]", "", clean)
    if len(clean) < 8 or len(clean) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", clean):
        return None
    try:
        out = bytes.fromhex(clean)
    except ValueError:
        return None
    return out if _looks_meaningful(out) else None


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
    for fn in (zlib.decompress, gzip.decompress,
               lambda b: zlib.decompress(b, -15)):
        try:
            out = fn(data)
            if out and _looks_meaningful(out):
                return out
        except Exception:
            continue
    return None


def _looks_meaningful(data):
    """Heuristic: enough printable ascii to be worth recursing into."""
    if not data:
        return False
    sample = data[:512]
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(sample) > 0.85


def caesar_sweep(text):
    """Yield (shift, decoded) for all 26 shifts; caller checks flags."""
    results = []
    for shift in range(1, 26):
        out = []
        for ch in text:
            if "a" <= ch <= "z":
                out.append(chr((ord(ch) - 97 + shift) % 26 + 97))
            elif "A" <= ch <= "Z":
                out.append(chr((ord(ch) - 65 + shift) % 26 + 65))
            else:
                out.append(ch)
        results.append((shift, "".join(out)))
    return results


def atbash(text):
    out = []
    for ch in text:
        if "a" <= ch <= "z":
            out.append(chr(219 - ord(ch)))
        elif "A" <= ch <= "Z":
            out.append(chr(155 - ord(ch)))
        else:
            out.append(ch)
    return "".join(out)


def xor_sweep_single(data):
    """Yield (key_byte, decoded_bytes) for printable-key candidates."""
    for k in range(1, 256):
        dec = bytes(b ^ k for b in data)
        if _looks_meaningful(dec):
            yield k, dec


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
    return sorted(tokens)[:64]


def xor_with_key(data, key):
    kb = key.encode("utf-8", errors="replace") if isinstance(key, str) else key
    if not kb:
        return None
    return bytes(b ^ kb[i % len(kb)] for i, b in enumerate(data))


def chain_decode(checker, text, max_depth=MAX_CHAIN_DEPTH):
    """BFS unwrap of encoding chains (b64/b32/hex/url/gunzip/reverse/rot13/atbash).
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
            rot = codecs.encode(cur, "rot13")
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

def run_fast_path(chall_dir, files, checker, attempts):
    """Run all fast detectors. Returns flag or None. Appends Attempt records."""
    flag = None
    inputs = _candidate_inputs(chall_dir, files)

    # 1) Chain unwrap on every text input (whole file + each line)
    for src, data in inputs:
        if not isinstance(data, str):
            continue
        found, note = chain_decode(checker, data)
        if found:
            attempts.append(Attempt("chain-decode", "%s: %s" % (src, note), True, found))
            return found
        if src.endswith("#L1") or "[bytes]" not in src and len(inputs) <= 12:
            attempts.append(Attempt("chain-decode", "%s: %s" % (src, note), False))
    for src, data in inputs:
        if not isinstance(data, str):
            continue
        for shift, dec in caesar_sweep(data):
            found = _flag_text(checker, dec)
            if found:
                attempts.append(Attempt("caesar", "%s shift=%d" % (src, shift), True, found))
                return found
        attempts.append(Attempt("caesar", "%s: 25 shifts no flag" % src, False))

    # 3) Single-byte XOR sweep on raw byte inputs
    for src, data in inputs:
        if not isinstance(data, bytes):
            continue
        for k, dec in xor_sweep_single(data):
            found = _flag_text(checker, dec)
            if found:
                attempts.append(Attempt("xor-single", "%s key=0x%02x" % (src, k), True, found))
                return found
        attempts.append(Attempt("xor-single", "%s: 255 keys no flag" % src, False))

    # 4) Token-key XOR (bridge strategy: hint words inside challenge files)
    tokens = extract_tokens(files)
    for src, data in inputs:
        if not isinstance(data, bytes):
            continue
        for tok in tokens:
            dec = xor_with_key(data, tok)
            if dec is None or not _looks_meaningful(dec):
                continue
            found = _flag_text(checker, dec)
            if found:
                attempts.append(Attempt("xor-token", "%s key=%s" % (src, tok), True, found))
                return found
    if tokens:
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
                attempts.append(Attempt("decompress+chain", "%s: %s" % (src, note), True, found))
                return found
        attempts.append(Attempt("decompress", "%s: no" % src, False))

    return flag


def run_quick_pass(chall_dir, files, checker):
    """Convenience wrapper used by deep path as strategy #0."""
    attempts = []
    flag = run_fast_path(chall_dir, files, checker, attempts)
    return flag, attempts
