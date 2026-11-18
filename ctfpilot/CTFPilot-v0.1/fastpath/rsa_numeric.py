"""Fast path: RSA numeric attacks on labeled big integers found in files.

Two generic strategies (offline, pure stdlib). Both re-derive the plaintext
from the published numbers -- nothing is hardcoded per challenge.

1. structured primes with a known cofactor g:
       N = (2g*a + 1)(2g*b + 1),  a,b in a narrow range
   -> h = (N-1)/(2g) = 2gab + a + b; scan ab near (N-1)/(4g^2); the
      discriminant (a+b)^2 - 4ab must be a perfect square.

2. Wiener on an inflated modulus order:
       e = d^-1 mod M with M = (p^k - 1)(q^k - 1) ~ n^k (k = 1,2,3,4,6,8)
   -> continued fraction of e / n^k recovers k/d; phi(n) divides M, so
      m = pow(c, d, n) is valid without factoring n.

Both are gated on a factorization / decryption check before reporting.
"""
import math
import re
import time

MAX_WINDOW = 600_000        # how far below (N-1)/(4g^2) we scan for ab
STRUCTURED_BUDGET_S = 25.0  # wall clock guard for the scan
MAX_CONVERGENTS = 4000

ALIASES = {
    "n": "n", "modulus": "n", "mod": "n", "nn": "n",
    "e": "e", "exp": "e", "ee": "e",
    "c": "c", "enc": "c", "ct": "c", "cipher": "c", "ciphertext": "c",
    "g": "g", "cofactor": "g",
}

LABEL_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(\d+)")
PRINTABLE = set(range(32, 127)) | {9, 10, 13}

_SMALL_PRIMES = None          # sieve <= 65536, built once lazily


def _small_prime_table():
    """Sieve primes <= 65536 once (6542 primes)."""
    global _SMALL_PRIMES
    if _SMALL_PRIMES is None:
        limit = 65536
        sieve = bytearray([1]) * (limit + 1)
        sieve[0:2] = b"\x00\x00"
        i = 2
        while i * i <= limit:
            if sieve[i]:
                sieve[i * i:: i] = bytearray(len(sieve[i * i:: i]))
            i += 1
        _SMALL_PRIMES = [i for i in range(limit + 1) if sieve[i]]
    return _SMALL_PRIMES


def _mr_probable_prime(n):
    """Miller-Rabin, first 12 prime bases (exact for n < 3.3e24)."""
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


ALL_NUM_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(\d{5,})")
STRIP_BUDGET_S = 25.0        # same guard scale as STRUCTURED_BUDGET_S


def _trial_pool(text, n):
    """Divisor candidates: every >=5-digit labeled number (minus the modulus
    itself) plus the 16-bit prime table. The r4q family hands us the 128-bit
    prime r as a label; the odd_factor family hides everything in-table."""
    pool = set()
    for m in ALL_NUM_RE.finditer(text):
        v = int(m.group(2))
        if 3 <= v < n:
            pool.add(v)
    pool.update(_small_prime_table())
    return sorted(pool)


def _small_prime_strip(text, n, e, c, checker):
    """Trial-divide n by labeled numbers + the 16-bit prime table, strip
    repeated factors; fully factored (or probable-prime cofactor) -> phi ->
    decrypt -> verify flag. Returns (plaintext, note)."""
    started = time.monotonic()
    pool = _trial_pool(text, n)
    rest = n
    factors = []
    for cand in pool:
        if cand * cand > rest:
            break
        if rest % cand:
            continue
        exp = 0
        while rest % cand == 0:
            rest //= cand
            exp += 1
        factors.append((cand, exp))
        if time.monotonic() - started > STRIP_BUDGET_S:
            return None, "small-prime strip: budget exhausted after %d^%d" % (cand, exp)
    parts = ["%d^%d" % (f_, a_) for f_, a_ in factors]
    if rest > 1:
        if _mr_probable_prime(rest):
            factors.append((rest, 1))
            parts.append("%d(prime)" % rest)
        else:
            return None, "small-prime strip: %s * unfactored %d-bit cofactor" % (
                "*".join(parts), rest.bit_length())
    if not factors:
        return None, "small-prime strip: no labeled/table factor"
    phi = 1
    for f_, a_ in factors:
        phi *= (f_ - 1) * f_ ** (a_ - 1)
    try:
        d = pow(e, -1, phi)
    except ValueError:
        return None, "small-prime strip: e not invertible mod phi"
    pt = _decrypt(c, d, n)
    if pt is not None:
        found = checker.find(pt.decode("utf-8", errors="replace"))
        if found:
            return pt, "small-prime strip (%s)" % "*".join(parts)
    return None, "small-prime strip: factored %s but no flag" % "*".join(parts)


def _labeled_numbers(text):
    """Collect n/e/c/g from 'name = 123...' patterns (comments included).

    Keep the LARGEST value per alias: challenge scripts often contain code like
    ``N = 2*g*h + 1`` whose first match is the tiny literal ``2``; the real
    modulus/ciphertext is always the long integer.
    """
    out = {}
    for m in LABEL_RE.finditer(text):
        alias = ALIASES.get(m.group(1).lower())
        if not alias:
            continue
        val = int(m.group(2))
        if alias not in out or val > out[alias]:
            out[alias] = val
    return out


def _as_bytes(value, n):
    if value <= 0 or value >= n * n:
        return None
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if not raw:
        return None
    ratio = sum(1 for b in raw if b in PRINTABLE) / len(raw)
    if ratio < 0.85:
        return None
    return raw


def _decrypt(c, d, n):
    try:
        m = pow(c, d, n)
    except (ValueError, OverflowError):
        return None
    return _as_bytes(m, n)


def _structured_primes(n, g, e, c):
    """N = (2ga+1)(2gb+1) with known g. Returns (plaintext, note) or (None, note)."""
    two_g = 2 * g
    if g <= 1 or n % two_g != 1:
        return None, "N != 1 mod 2g"
    h = (n - 1) // two_g
    ab0 = (n - 1) // (4 * g * g)
    if ab0 <= 0:
        return None, "window empty"
    s0 = h - two_g * ab0
    started = time.monotonic()
    for delta in range(MAX_WINDOW):
        if not delta & 0xFFF and time.monotonic() - started > STRUCTURED_BUDGET_S:
            return None, "structured primes: budget exhausted"
        ab = ab0 - delta
        if ab <= 0:
            break
        s = s0 + two_g * delta
        if s <= 0:
            continue
        disc = s * s - 4 * ab
        if disc < 0:
            continue
        root = math.isqrt(disc)
        if root * root != disc or (s + root) & 1:
            continue
        a = (s + root) // 2
        b = (s - root) // 2
        if a * b != ab:
            continue
        p = two_g * a + 1
        q = two_g * b + 1
        if p * q != n:
            continue
        try:
            d = pow(e, -1, (p - 1) * (q - 1))
        except ValueError:
            return None, "structured primes: e not invertible"
        pt = _decrypt(c, d, n)
        return pt, "structured primes (window %d)" % delta
    if time.monotonic() - started > STRUCTURED_BUDGET_S:
        return None, "structured primes: budget exhausted"
    return None, "structured primes: no factor in %d-wide window" % MAX_WINDOW


def _convergents(num, den, cap=MAX_CONVERGENTS):
    a, b = num, den
    p0, p1 = 0, 1
    q0, q1 = 1, 0
    count = 0
    while b and count < cap:
        q = a // b
        a, b = b, a - q * b
        p0, p1 = p1, q * p1 + p0
        q0, q1 = q1, q * q1 + q0
        count += 1
        yield p1, q1


def _wiener(n, e, c, checker):
    """e = d^-1 mod M with M ~ n^k: continued fraction of e/n^k."""
    told = "wiener n^?"
    budget = 8192                      # cap on the inflated modulus size
    for k in (1, 2, 3, 4, 6, 8):
        if n.bit_length() * k > budget:
            continue
        mod = n ** k
        if e >= mod:
            continue
        for kk, d in _convergents(e, mod):
            if kk <= 0 or d <= 0:
                continue
            if (e * d - 1) % kk:
                continue
            pt = _decrypt(c, d, n)
            if pt is not None:
                found = checker.find(pt.decode("utf-8", errors="replace"))
                if found:
                    return pt, "wiener n^%d" % k
            told = "wiener n^%d no flag" % k
    return None, told


def rsa_numeric_scan(checker, inputs):
    """Try RSA numeric attacks over every labeled-number group we can parse.

    Returns (flag, note). flag is None when nothing worked; note is empty when
    no candidate group was found at all (so the caller can stay quiet).
    """
    groups = []
    for src, data in inputs:
        if not isinstance(data, str) or len(data) < 32:
            continue
        nums = _labeled_numbers(data)
        if "n" in nums and "c" in nums and nums["n"] > 2 ** 63:
            groups.append((src, nums, data))
    if not groups:
        return None, ""

    notes = []
    for src, nums, text in groups:
        n, c = nums["n"], nums["c"]
        if c >= n:
            notes.append("%s: c>=n" % src)
            continue
        if "e" in nums:
            e = nums["e"]
            if "g" in nums and nums["g"] > 1:
                pt, note = _structured_primes(n, nums["g"], e, c)
                notes.append("%s: %s" % (src, note))
                if pt is not None:
                    found = checker.find(pt.decode("utf-8", errors="replace"))
                    if found:
                        return found, "%s: %s" % (src, note)
            pt, note = _small_prime_strip(text, n, e, c, checker)
            notes.append("%s: %s" % (src, note))
            if pt is not None:
                found = checker.find(pt.decode("utf-8", errors="replace"))
                if found:
                    return found, "%s: %s" % (src, note)
            pt, note = _wiener(n, e, c, checker)
            notes.append("%s: %s" % (src, note))
            if pt is not None:
                found = checker.find(pt.decode("utf-8", errors="replace"))
                if found:
                    return found, "%s: %s" % (src, note)
    return None, "; ".join(notes[:4])