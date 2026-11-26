"""Dup-5th-hole regression set -- run with the project python:

    python -X utf8 tests/test_dedup_year.py

Covers (evolve-20261116-11: year-in-identity + whole-repo exact-only).

Live misfire this set reproduces (backlog evidence, 2026-11-16):
    skip=duplicate(fuzzy(lanqiao/0x100cupfinals ~ cupfinals))
    key=CTF-Archives/2025-LanqiaoCup-Finals::.
A whole-repo 2025 contest candidate was fuzzy-matched against the 2024
single challenge 0x100 -- its writeup row
'writeup:0x100-LanqiaoCup2024Finals' (challenges table, id=692) folds to
core '0x100cupfinals' with the year stripped, so 'cupfinals' (the 2025
contest-root core) is a substring of it and the containment rule killed
the pick; the bad key then sat permanently in the ledger keys.

Post-fix semantics asserted here:
  - year participates in identity: exact and fuzzy only match same-year
    entries when both sides carry a year (cross-year same-name contests /
    challenges are NOT dups); yearless legacy entries keep the old
    any-year match so nothing already deduped re-pulls;
  - whole-repo candidates ('owner/repo::.') never run the fuzzy
    containment layer (exact only): a contest-root core is by
    construction a substring of many single-challenge cores, containment
    there can only misfire;
  - every same-year / yearless / whole-repo-vs-whole-repo behavior that
    previously deduped still dedups (no over-narrowing).

Pre-fix runs print FAIL lines that show the exact misfire reasons; the
is_dup wrapper falls back to the 2-arg call when whole_repo= is not
accepted (pre-fix signature), so the set is the RED/GREEN carrier.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dedup as D  # noqa: E402

FAILURES = []

# Seeds mirror real production index entries (challenges-table rows,
# materialized dirs, writeup rows). Pairs are built through _pair_keys +
# SEP_KEY.join so this set runs unchanged on pre-fix (2-part) and
# post-fix (3-part) key formats.
SEEDS = [
    "2024-LanqiaoCup-Finals__0x100",            # 2024 single (dir form)
    "writeup:0x100-LanqiaoCup2024Finals",        # 2024 writeup row -> 0x100cupfinals
    "2024-LanqiaoCup-Finals-dirty_data",         # 2024 contest-first record
    "2025-LanqiaoCup-Finals__10.应急3",          # 2025 single (legacy dir)
    "writeup:babyRSA-LanqiaoCup2025Finals",      # 2025 writeup embed -> babyrsacupfinals
    "welpp",                                     # yearless legacy (no comp, no year)
    "NSSCTF-Round12-ability__ability",           # yearless gh-style single
    "2024-sanxia",                               # whole-repo materialization (2024)
    "2026-jksn-quals__Web-2.网站诊断",           # Web-4.-style record name
]

WHOLE_REPO_2025 = ("CTF-Archives/2025-LanqiaoCup-Finals::. "
                   "2025-LanqiaoCup-Finals")
WHOLE_REPO_2024 = ("CTF-Archives/2024-LanqiaoCup-Finals::. "
                   "2024-LanqiaoCup-Finals")


def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL ") + name +
          ("  " + detail if detail else ""))
    if not cond:
        FAILURES.append(name)


def is_dup(idx, cand, whole=False):
    """Whole-repo-aware wrapper: falls back to the 2-arg call on pre-fix
    code so the misfire itself (not a TypeError) is what shows up RED."""
    try:
        return D.is_dup(idx, cand, whole_repo=whole)
    except TypeError:
        return D.is_dup(idx, cand)


def main():
    pairs = {}
    for e in SEEDS:
        for pk in D._pair_keys(e):
            pairs.setdefault(D.SEP_KEY.join(pk), set()).add("t")
    idx = {"pairs": {k: sorted(v) for k, v in sorted(pairs.items())}}

    # --- the live misfire, whole-repo 2025 vs 2024 singles (RED pre-fix)
    h, r = is_dup(idx, WHOLE_REPO_2025, whole=True)
    check("A whole-repo-2025 not killed by 2024 singles", not h, r)
    # same structural case with matching years: the 2025 writeup embedder
    # proves the year gate alone cannot save this -- whole-repo must skip
    # fuzzy outright
    h, r = is_dup(idx, WHOLE_REPO_2025, whole=True)
    check("G whole-repo-2025 not killed by 2025 writeup embeds", not h, r)

    # --- cross-year same-name single (year gate; RED pre-fix via exact)
    h, r = is_dup(idx, "CTF-Archives/2025-LanqiaoCup-Finals::0x100")
    check("B cross-year single 0x100 (2025 vs 2024) passes", not h, r)
    # --- whole-repo 2024 form unlocks the same way (RED pre-fix)
    h, r = is_dup(idx, WHOLE_REPO_2024, whole=True)
    check("I whole-repo-2024 not killed by its own singles", not h, r)

    # --- keep-deduping guards (must stay True pre AND post fix)
    h, r = is_dup(idx, "CTF-Archives/2024-LanqiaoCup-Finals::0x100")
    check("C same-year single 0x100 stays a dup", h, r)
    h, r = is_dup(idx, "CTF-Archives/2024-LanqiaoCup-Finals::dirty_data "
                       "2024-LanqiaoCup-Finals-dirty_data")
    check("D same-year fuzzy (ledger tail vs record) stays a dup", h, r)
    h, r = is_dup(idx, "SomeOrg/2024-qwqsncx::welpp welpp")
    check("E yearless legacy entry still catches its re-pull", h, r)
    h, r = is_dup(idx, "CTF-Archives/NSSCTF-Round12-ability::ability "
                       "NSSCTF-Round12-ability-ability")
    check("H yearless candidate vs yearless entry stays a dup", h, r)
    h, r = is_dup(idx, "CTF-Archives/2024-sanxia::. 2024-sanxia",
                 whole=True)
    check("F whole-repo vs whole-repo same-year stays a dup", h, r)
    h, r = is_dup(idx, "CTF-Archives/2026-jksn-quals::Web-2.网站诊断 "
                       "2026-jksn-quals-Web-2.网站诊断")
    check("O Web-4.-style record name stays a dup", h, r)

    # --- year_of vectors (post-fix only; skipped pre-fix)
    if hasattr(D, "year_of"):
        check("J year_of writeup row", D.year_of(
            "writeup:0x100-LanqiaoCup2024Finals") == "2024")
        check("J year_of ledger key", D.year_of(
            "CTF-Archives/2025-LanqiaoCup-Finals::.") == "2025")
        check("J year_of yearless", D.year_of("welpp") == "")
    else:
        print("  skip J year_of vectors (pre-fix code has no year_of)")

    # --- production index live known-answers (real entries, zero API)
    prod, _ip = D.load_index()
    h, r = is_dup(prod, WHOLE_REPO_2025, whole=True)
    # [flipped 2026-11-17 with cause, rsi-078 precedent] this live
    # known-answer encoded the pre-03:01 unseen state: the 2025-
    # LanqiaoCup-Finals whole repo was NOT in the corpus when the set was
    # authored (this file's mtime predates the pull). The 03:01 feed
    # round pulled it (challenges row), every index rebuild since 05:34
    # carries lanqiao/2025/cupfinals, and the name layer now CORRECTLY
    # exact-hits a re-pull of the same contest. The no-false-kill
    # semantics this assertion used to guard stay guarded by A/G on the
    # seeded index (2024 singles / 2025 writeup embeds) and by B
    # (cross-year single).
    check("P live prod index: whole-repo-2025 correctly exact-hits "
          "(pulled 03:01)", h and "cupfinals" in r, r)
    h, r = is_dup(prod, "CTF-Archives/2024-LanqiaoCup-Finals::0x100")
    check("Q live prod index: 2024 single 0x100 stays a dup", h, r)

    if FAILURES:
        print("FAILED: %d (%s)" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("dedup-year regression: ALL PASS")


if __name__ == "__main__":
    main()
