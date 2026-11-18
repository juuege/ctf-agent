"""Flag detection and validation.

Strict-pattern matching only: a flag counts only when it matches a known
CTF flag format. This avoids false positives from random base64 strings.
Anti-cheat note: flags must come from local execution output; anything that
would require fetching a public writeup online is out of scope by default.

Provenance (fix for the 06_zip_nested lesson): a flag that appears VERBATIM
in the original challenge files is a raw echo (challenge-provided flag), not
a derived solve. `flag_in_raw` implements that check; do_solve gates on it via
config `flagcheck.require_derived` (default true).
"""
import os
import re


# [evolve-20261117-31 / rsi-101] anchor necessary-condition gate: the hot
# battery charged every chain-decode node with a full per-pattern re.search
# pass (live RED this round: FlagChecker.find cum 3.292 s = 13.3% of the ISCC
# chain wall over 230,095 calls / 2,300,950 pattern searches; Bluehat rank
# 0.336 s = 31.1% over 848 medium-size nodes). The gate derives, at
# construction, the LEADING LITERAL every pattern's match must contain
# (live config: flag: flag{ FLAG{ ctf{ CTF{ DASCTF{ ctfshow{ NSSCTF{ SUCTF{
# hgame{) and short-circuits find/find_all to None/[] when NONE of those
# literals appears -- pure C-level str substring tests, measured 8-10x the
# incumbent battery on 412 KB miss faces, chain walls -15.9% (ISCC) / -32.1%
# (rank). A gate hit falls back to the ORIGINAL per-pattern loop unchanged,
# so pattern priority and every lookaround scope stay bit-exact; the miss
# equivalence is provable (a match must contain its leading literal, so
# no-literal => no match). Conservative build rules -- any doubt disables
# the gate, semantics untouched: quantifiers truncate the run's last char
# (min-count may be 0); classes, groups other than a simple lookbehind,
# top-level alternation, non-literal escapes, and flags beyond
# UNICODE/ASCII/MULTILINE/DOTALL (IGNORECASE/LOCALE/VERBOSE change match
# semantics) all yield None.
_ANCHOR_STOP = set("[](){}|.*+?^$")
_ANCHOR_LIT_ESCAPES = set("()[]{}|.*+?^$\\")
_ANCHOR_OK_FLAGS_MASK = re.UNICODE | re.ASCII | re.MULTILINE | re.DOTALL


def _anchor_ok_flags(flags):
    # literals stay literals under these; anything else (IGNORECASE/LOCALE/
    # VERBOSE/DEBUG/TEMPLATE...) can change match semantics -> gate disabled
    return not (flags & ~_ANCHOR_OK_FLAGS_MASK)


def _leading_literal(src):
    """Conservative leading-literal extractor: the literal substring every
    match of the pattern must contain, or None when not provable. Zero-width
    prefixes ('^', simple lookbehind without nested parens/alternation) are
    skipped; escaped metachars and \\xNN are unescaped; quantifiers (*?+{)
    drop the run's last char (min-count may be 0); any other construct --
    classes, non-lookbehind groups, top-level '|', non-literal escapes, '$'
    before a run forms -- yields None."""
    i, out, n = 0, [], len(src)
    while i < n:
        c = src[i]
        if c == "\\":
            if i + 1 >= n:
                return None
            nxt = src[i + 1]
            if nxt == "x" and i + 3 < n:
                try:
                    out.append(chr(int(src[i + 2:i + 4], 16)))
                    i += 4
                    continue
                except ValueError:
                    return None
            if nxt in _ANCHOR_LIT_ESCAPES:
                out.append(nxt)
                i += 2
                continue
            return None
        if c == "|":
            return None  # top-level alternation: the run is not necessary
        if c in "([":
            if c == "(" and (src.startswith("(?<=", i) or src.startswith("(?<!", i)):
                close = src.find(")", i)
                body = src[i + 4:close]  # inside the lookbehind, past "(?<="/"(?<!"
                if close < 0 or "(" in body or "|" in body:
                    return None
                i = close + 1
                continue
            if out:
                return "".join(out)
            return None
        if c == "^":
            i += 1
            continue
        if c == "$":
            return "".join(out) if out else None
        if c in _ANCHOR_STOP:
            if c in "*?+{":
                if not out:
                    return None
                run = "".join(out[:-1])
                return run if run else None
            if out:
                return "".join(out)
            return None
        out.append(c)
        i += 1
    return "".join(out) if out else None


class FlagChecker:
    def __init__(self, patterns, extra_regex=None):
        self.patterns = list(patterns)
        if extra_regex:
            try:
                self.patterns = [re.compile(extra_regex)] + self.patterns
            except re.error:
                pass
        # [evolve-20261117-31 / rsi-101] anchor necessary-condition gate:
        # derive per-pattern leading literals; any non-provable pattern
        # (or non re.Pattern object) disables the gate entirely.
        anchors = []
        try:
            for p in self.patterns:
                if not _anchor_ok_flags(p.flags):
                    anchors = None
                    break
                lit = _leading_literal(p.pattern)
                if not lit:
                    anchors = None
                    break
                anchors.append(lit)
        except (AttributeError, TypeError):
            anchors = None
        self._anchors = (list(dict.fromkeys(anchors))
                         if anchors is not None else None)
        # [evolve-20261117-41 / rsi-114] common-char fast pre-gate for the
        # anchor necessary-condition gate. RED (live): after rsi-104's
        # per-line dedup the find() call stream carries almost no duplicate
        # texts (0.0% on the ISCC face: 95,426 calls / 95,393 distinct), so
        # a result memo has no payoff; but 95,424 of those calls are
        # anchor-gate rejections and the gate itself was find's entire
        # remaining cost (1.135 s), each miss paying a full per-anchor
        # substring scan (10 live anchors x avg 1374 B). 80.9% of the
        # misses contained NO '{' at all. Live config fact (v1 misdesign
        # corrected): the anchor set is ['flag:' + 9 brace-literals] --
        # 'flag:' carries NO '{', so the set-wide character intersection
        # is EMPTY and a single-char pre-gate can never engage. The
        # grouped form instead: (1) non-empty intersection -> one-char
        # fast gate (original design); (2) empty intersection -> greedy
        # split char c = the char carried by the MOST anchors (deterministic:
        # min on ties). When c is absent from text, every c-carrying
        # anchor is provably absent and only the anchors LACKING c (the
        # plain group, e.g. ['flag:']) need scanning. Provable
        # equivalence: c-miss => c-anchors all absent => gate result is
        # exactly (any plain anchor in text) -> None iff the plain group
        # is empty or all-miss; c-hit or split disabled -> the exact
        # rsi-108 full loop. Hit-safety: a real flag always contains its
        # pattern's leading literal (the anchor), so a c-carrying anchor
        # implies c is in the flag text (pre-gate passes) and a plain
        # anchor is found by the plain scan (passes). Conservative
        # construction: fewer than 2 anchors carrying c disables the
        # split (a 1-anchor group would make the miss path PAY an extra
        # memchr for nothing).
        self._anchor_pre = None
        self._split_char = None
        self._plain_anchors = None
        if self._anchors:
            _common = set(self._anchors[0])
            for _a in self._anchors[1:]:
                _common &= set(_a)
            if _common:
                self._anchor_pre = min(_common)
            else:
                _cnt = {}
                for _a in self._anchors:
                    for _c in set(_a):
                        _cnt[_c] = _cnt.get(_c, 0) + 1
                _bc, _bn = None, 0
                for _c, _n in _cnt.items():
                    if _n > _bn or (_n == _bn and _c < _bc):
                        _bc, _bn = _c, _n
                if _bc is not None and _bn >= 2:
                    self._split_char = _bc
                    self._plain_anchors = [a for a in self._anchors
                                           if _bc not in a]

    def find(self, text):
        """Return the first flag found in text, else None."""
        if not text:
            return None
        # [evolve-20261117-36 / rsi-108] anchor gate: explicit early-break
        # loop replaces the any(<genexpr>) form. Zero semantic change: a
        # for loop that never breaks (no anchor present in text) falls
        # into the else branch exactly when any() would have returned
        # False. The per-call generator object + any() next() protocol
        # hops (6.03M genexpr calls on the ISCC face alone) disappear.
        # [evolve-20261117-41 / rsi-114] grouped fast pre-gate nested INSIDE
        # the gate (see __init__ for the design + proof): the whole fast
        # path is subordinate to `_anchors is not None`, so the legacy
        # "disable the gate" twin idiom (checker._anchors = None) also
        # disables the pre-gate (live RED: evolve31's >3x miss-wall
        # assertion collapsed when the twin kept the split path). A
        # pre-gate miss returns early; a pre-gate HIT still falls through
        # to the full loop (carrying the pre char does not imply carrying
        # a whole anchor); only the split-miss + plain-hit face skips the
        # redundant full rescan (its verdict is already decided).
        if self._anchors is not None:
            _gated41 = False
            if self._anchor_pre is not None:
                if self._anchor_pre not in text:
                    return None
            elif self._split_char is not None:
                if self._split_char not in text:
                    for a in self._plain_anchors:
                        if a in text:
                            break
                    else:
                        return None
                    _gated41 = True
            if not _gated41:
                for a in self._anchors:
                    if a in text:
                        break
                else:
                    return None
        for pat in self.patterns:
            m = pat.search(text)
            if m:
                return m.group(0)
        return None

    def find_all(self, text):
        found = []
        if not text:
            return found
        # [evolve-20261117-36 / rsi-108] anchor gate: same explicit
        # early-break loop as find() (see above) -- zero semantic change.
        # [evolve-20261117-41 / rsi-114] same grouped pre-gate as find()
        # -- same proof, same twin-idiom safety, same fallback contract.
        if self._anchors is not None:
            _gated41 = False
            if self._anchor_pre is not None:
                if self._anchor_pre not in text:
                    return found
            elif self._split_char is not None:
                if self._split_char not in text:
                    for a in self._plain_anchors:
                        if a in text:
                            break
                    else:
                        return found
                    _gated41 = True
            if not _gated41:
                for a in self._anchors:
                    if a in text:
                        break
                else:
                    return found
        for pat in self.patterns:
            for m in pat.finditer(text):
                cand = m.group(0)
                if cand not in found:
                    found.append(cand)
        return found


def flag_in_raw(chall_dir, flag, max_file=8 * 1024 * 1024,
               max_total=64 * 1024 * 1024):
    """Provenance check: does `flag` appear byte-for-byte in the ORIGINAL
    challenge files? If yes, the "solve" only echoed a challenge-provided
    flag (raw-echo) instead of deriving it. Engine-generated side effects
    (_extracted/, _extract/, .temp/, _ctfpilot* files) are excluded so a
    legitimate extract-then-read solve is not penalized.

    Sidecar symmetry (evolve-20261117-46 / rsi-118, supersedes the
    evolve-20261116-12 sidecar exception): engine-authored artifacts are
    NOT challenge material on either side of the boundary. The solve
    INPUT side already excludes every _ctfpilot* file (router inventory
    + the printable-strings-scan filter closed the last read gap this
    round), so a scanner can no longer echo the sidecar at all; keeping
    the sidecar inside this compare face only retracted LEGITIMATE
    derived solves that merely coincide with a parked sidecar (live
    finding B: 2024-OMCTF pdf-text CID solve retracted against
    _ctfpilot_model_result.json while the flag bytes sat inside a
    compressed FlateDecode stream, verbatim-absent from all real
    material). The sidecar is now exempt exactly like the other
    _ctfpilot* byproducts; challenge material is unchanged.

    Returns (hit_bool, rel_path_of_first_hit_or_None).
    """
    if not flag:
        return False, None
    needle = flag.encode("utf-8", "replace")
    # [evolve-20261117-21 / rsi-091] _extract/ = legacy extraction dir
    # (pre-_extracted era; live: 2023-Bluehat-Quals-pwn). The raw-echo scan
    # must exempt it exactly like _extracted/: a candidate flag matching a
    # string in OLD-run extraction residue is NOT "verbatim in the original
    # files", so counting it would falsely retract a legitimately derived
    # solve -- the exact failure the _extracted exemption exists to prevent
    # (incumbent RED: needle /lib/ld-linux.so.2 hit at
    # _extract\heapSpary\libc.so.6). Name-exact, same discipline as the
    # router inventory face.
    skip_dirs = {"_extracted", "_extract", ".temp", "__pycache__"}
    # [evolve-20261117-46 / rsi-118] _ctfpilot* byproducts (result/trace/
    # step scratch AND the account-model sidecar) are uniformly exempt:
    # they are engine-authored, never challenge material. The fake-SOLVED
    # face the evolve-12 exception guarded (scanner reading the sidecar
    # via the printable-strings-scan gap) is closed at the INPUT side
    # instead (planner strategy filters _ctfpilot paths; router inventory
    # already pruned them) -- since then the exit-side compare face only
    # ever fired on legitimate-coincidence retracts.
    total = 0
    for root, dirs, names in os.walk(chall_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in names:
            if name.startswith("_ctfpilot"):
                continue
            # [evolve-20261117-11] wrapper stdout/stderr redirection: the
            # engine's own printed flag lands in .solve.log DURING solve
            # (feed/arena templates redirect there); treating that as a raw
            # echo would retract a genuine derived solve (fake NOT SOLVED).
            if name in (".solve.log", ".solve.err"):
                continue
            p = os.path.join(root, name)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if size > max_file or total >= max_total:
                continue
            try:
                with open(p, "rb") as fh:
                    data = fh.read(max_file)
            except OSError:
                continue
            total += len(data)
            if needle in data:
                return True, os.path.relpath(p, chall_dir)
    return False, None
