"""Cross-source dedup for bench-pull / learn-writeup (2026-11-16).

The 2026-11-14 ledger (_bench_ledger.json) dedups only inside its own
keyspace (owner/repo::path) for challenges pulled under ``ctf-feed``.
Three older stores hold "already done" identities the ledger never sees:

  A. ctf-feed/done-legacy.json    -- the old 20-minute scheduled feed's dedup
                                    list (271 entries, 'repo/name'), merged
                                    into ctf-feed on 2026-11-16 (formerly
                                    ctf-20min/done.json, another TA account)
  B. challenges table names     -- every solve/learn record since 2026-11-09
  C. ctf-feed legacy dirs      -- the old timer's materialized pulls
                                    (285 dirs, `<比赛>__<题名>` form, merged
                                    2026-11-16) + stray ``...__...`` dirs in
                                    the workspace root
  D. skills/learned/*.json      -- every writeup ever learned (card names)

This module folds them all into one persistent index
(memory/_traj_index.json) and answers is_dup() for bench-pull candidates,
so the skill never re-pulls a challenge another agent (or an older
incarnation of this skill) already did.

Matching is three-factor since evolve-20261116-11 (dup 5th hole):

    competition root (jksn / qwb / lanqiao / ...)   -- from any part
    year       (first 19xx/20xx in the FULL string, '' when absent --
                the year lives in the repo/contest part that
                core_name's tail-splitting discards, so it must be read
                before any tail split)
    core name  ('Web-4.教师面板' -> '教师面板';
                '9.ecn-LongjianCup2025' -> 'ecn';
                category prefixes, leading exercise numbers, CN tier words
                and year digits are stripped, separators dropped, lowered)

  exact-alias (evolve-20261117-06, dup 8th hole): whole-repo candidates
             additionally exact-check category-stripped alias cores --
             the prefix strips run BEFORE the competition word is cut
             out of mid-string, so a whole-repo single-challenge name
             ('2022-xhlj-crypto-myerrorlearn') keeps its category glued
             into the core ('cryptomyerrorlearn') while the index's
             single-form entries hold the clean core ('myerrorlearn'),
             and with fuzzy off for whole-repo (evolve-11) only exact
             can fire and never does (live: the 2026-11-17 re-pull of
             2022 西湖论剑 MyErrorLearn slipped all four layers when the
             old dir's one shared content file had also drifted bytes)

  exact  : (comp, year, core) already in index; a year-carrying candidate
              also checks the yearless key so legacy entries without a
              year keep deduping (no regression)
  fuzzy  : same comp + core names contain each other (guards 'Web-4.'
             style differences between ledger keys and record names),
             YEAR-GATED: when both sides carry years they must agree
             ('2024-Foo__bar' is never a dup of '2025-Foo::bar');
             a whole-repo candidate ('owner/repo::.') skips fuzzy
             entirely -- a contest-root core is by construction a
             substring of many single-challenge cores ('cupfinals' <
             '0x100cupfinals'; live misfire: the 2025 LanqiaoCup-Finals
             whole-repo pick was fuzzy-killed by the 2024 0x100 writeup
             row and its bad key sat in the ledger), so containment
             there can only misfire; whole-repo re-pulls stay covered by
             ledger keys + the attach/blob fingerprint layers.
  attach : content fingerprint (lower(name)+md5) of any non-environment
            file already under a different materialized dir (dup 4th hole:
            same challenge re-packaged under an unrelated name, e.g.
            '2024-qwqsncx' vs '2024-qwqsncx__勒索病毒事件排查' -- name
            matching shares nothing, welpp.exe/dict.txt md5s are identical)
  zip-fp : dup 6th hole (2026-11-16, live case 2024-qwb-qsnxbs):
            attach-fp only saw TOP-LEVEL file md5s, so the same challenge
            re-packaged as a .zip attachment (vs a previously materialized
            EXTRACTED dir) matched nothing -- ezFindShell.zip (1002 inner
            php) vs 2024-qwb-qsnxbs__ezFindShell/* (1004 files) had zero
            file-level overlap, and the >FP_MAX_FILES old dir contributed
            no fps at all. dir_fps now (a) samples FP_KEEP entries of
            oversized dirs instead of dropping identity and (b) folds
            sampled inner-entry fingerprints of .zip attachments, so
            zip-vs-extracted re-packagings collide both ways.
  blob-fp : pre-download layer (evolve-20261116-16): is_dup_blobs() checks
            a GitHub contents/tree listing's per-file git blob SHAs
            against the index BEFORE any content download, so the
            re-pull/re-publish of known content costs 0 bytes instead of
            the 2026-11-16 ISCC burn (734 files / 7 min downloaded for a
            candidate the post-download guards then rejected). Selection
            mirrors dir_fps; zip-vs-extracted stays a post-download
            md5-layer job (inner files have no server-side blob identity).

Nothing here rewrites or deletes existing data; the index is additive and
self-seeding on first use. gh* sources get full is_dup enforcement;
local: pools are left as-is on purpose (arena ablation replays pools by
design and carries its own ledger).
"""
import hashlib
import json
import os
import re
import sqlite3
import time
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS_ROOT = os.path.dirname(PROJECT_ROOT)
INDEX_PATH = os.path.join(PROJECT_ROOT, "memory", "_traj_index.json")
DB_PATH = os.path.join(PROJECT_ROOT, "memory", "trajectories.sqlite")
FEED_DIR = os.path.join(WS_ROOT, "ctf-feed")
LEGACY_JSON = os.path.join(FEED_DIR, "done-legacy.json")
LEGACY_DIRS = FEED_DIR          # merged 2026-11-16: old ctf-20min pulls now
                                # live here as `<比赛>__<题名>` dirs
LEARNED_DIR = os.path.join(PROJECT_ROOT, "skills", "learned")

# longest first: 'qsnxbs' must beat 'qwb'; 'longjiancup' beats 'longjian'
COMP_WORDS = sorted(set([
    "fjsdxssjan", "shenyangcar", "shangmicup", "qwbs", "longjiancup",
    "longjian", "lanqiao", "fjgys", "zeekr", "qwbs", "jksn", "qsnxbs",
    "ccb", "ciscn", "nco", "jswa", "zjsdxs", "newstar", "qwb", "icsc",
    "wqb", "xhlj", "dfjk", "chb", "mtgxs", "bytectf", "ccbgx", "nisa",
    "starctf", "iscc", "nssctf", "hgame", "shuxin", "bluehat", "zjsdxs",
    "zy", "dyn",
]), key=len, reverse=True)

CAT_PREFIX = re.compile(
    r"^(?:misc|web|crypto|pwn|rev|reverse|forensics|mobile|osint)[-_. ]*",
    re.IGNORECASE)
NUM_PREFIX = re.compile(r"^\d+(?:[.．、]|[-_ ])+")
CN_TIER = re.compile(r"(?:入门夺旗|简单夺旗|中等夺旗|高阶夺旗|多选题|单选题)")
SEP = re.compile(r"[\s_\-－—·．.,:：/\\|!！?？\"'`(){}<>@#$%^&+=~`]+")
YEAR = re.compile(r"(?:19|20)\d{2}")
SEP_KEY = "\x1f"   # (comp, core) pairs stored as json dict keys

# --- attachment fingerprints (evolve-20261116-07, dup 4th hole) -----------
# Environment/runtime files are EXCLUDED on purpose: libc/ld are shared
# across DIFFERENT challenges of the same year (verified in-tree:
# mtgxs-pwn-smtp == xhlj-pwn-message_board libc md5; WQB digtal_bomb ==
# ood_canary libc; hgame week1 == week2 libc/ld), deploy files are
# boilerplate, flag is a deploy template, _ctfpilot* are engine traces.
# Since zip-aware inner fps (dup 6th hole) the SAME classes matter INSIDE
# archives (verified in-tree: docker-github.yml/docker-image.yml and
# sudoers are shared deploy templates across distinct NISA/ccbgx/xhlj
# challenges; ._name AppleDouble junk rides every NCO zip) -- so the
# inner basename runs through this same exclude.
#
# [evolve-20261118-18 / rsi-141] toolchain-boilerplate extension. RED (live,
# this feed round): pre-download blob-fp killed FIVE candidates whose only
# shared file was an ecosystem boilerplate, not challenge identity --
#   nginx.conf              <- 2024-LanqiaoCup-Finals-dirty_data  (killed
#                              2024-VNCTF-OnlyLocalSql, a distinct challenge)
#   libBrokenLocale-2.32.so <- 2023-hgame-week3-pwn-large_note   (killed BOTH
#                              safe_note and note_context, distinct siblings
#                              shipping the same glibc drop)
#   eezzjs.iml              <- 2024-LanqiaoCup-Finals-dirty_data  (killed the
#                              last un-pulled n1ctf-2025 member)
# The old list covered deploy runners (docker/start/xinetd/sudoers) and
# libc/ld but not the rest of the shared-toolchain surface: shared objects
# (lib*.so / lib*.so.*), server configs (nginx*.conf), IDE metadata (*.iml),
# build systems (Makefile/GNUmakefile), and dependency manifests
# (requirements*.txt, package[-lock].json, yarn.lock, pnpm-lock.yaml,
# go.mod/go.sum, pom.xml, build.gradle[.kts], pyproject.toml, setup.py,
# setup.cfg, tox.ini). Direction is the SAME as the original libc exclude:
# a file every scaffold of the ecosystem ships is not identity; a challenge
# whose bytes actually repeat still hits on its business files
# (app.py/main.go/challenge binary/attachment zip), which this list never
# touches. Business-looking names (index.html, main.go, app.py, *.vue,
# *.sql, *.pcap, challenge.so) stay identity-bearing on purpose.
FP_EXCLUDE = re.compile(
    r"^(?:_ctfpilot.*|__pycache__|\.temp.*|libc[^/]*|ld-.*|lib.*\.so(?:\..*)?|"
    r"docker-compose\.ya?ml|dockerfile|(?:docker-)?entrypoint.*|"
    r"docker-.*\.ya?ml|start\.sh|run\.sh|ctf\.xinetd|sudoers|sshd_config|"
    r"nginx(?:[-_.].*)?\.conf|.*\.iml|makefile|gnumakefile|"
    r"misc\.xml|modules\.xml|vcs\.xml|workspace\.xml|php\.xml|"
    r"jsLibraryMappings\.xml|copilotDiffState\.xml|encodings\.xml|"
    r"requirements(?:[-_.].*)?\.txt|package(?:-lock)?\.json|yarn\.lock|"
    r"pnpm-lock\.ya?ml|go\.mod|go\.sum|pom\.xml|build\.gradle(?:\.kts)?|"
    r"pyproject\.toml|setup\.py|setup\.cfg|tox\.ini|"
    r"flag|readme.*|\.git.*|license.*|\._.*|\.dockerignore)$",
    re.IGNORECASE)
FP_MAX_FILES = 40                  # per dir; above this only a sample keeps
FP_KEEP = 24                      # identity (deterministic, name-sorted)
FP_MAX_SIZE = 64 * 1024 * 1024     # per file; huge blobs excluded whole
FP_ZIP_SAMPLE = 24                # inner entries folded per .zip attachment
FP_SCHEMA = 5                     # fps section format (5 = empty files carry
                                    # no identity, evolve-20261117-14; 4 =
                                    # wrapper byproducts excluded,
                                    # evolve-20261117-13; 3 = BOM-rewritten
                                    # files excluded, evolve-20261116-19;
                                    # 2 = zip/sampled; 1 = md5-of-whole-dir
                                    # only)
BLOBS_SCHEMA = 4                   # blobs section format (4 = empty files
                                    # carry no identity,
                                    # evolve-20261117-14; 3 = wrapper-byproduct
                                    # exclusion (evolve-20261117-13); 2 = BOM
                                    # exclusion (evolve-20261116-19); 1 =
                                    # plain top-level git blob SHAs,
                                    # evolve-20261116-16)
PAIRS_SCHEMA = 2                  # pairs section format (2 = year-in-identity
                                   # keys, evolve-20261116-11; 1/absent =
                                   # yearless legacy keys)


# --- wrapper redirection byproducts (evolve-20261117-13) -----------------
# The feed/arena solve wrapper redirects engine stdout/stderr into
# .solve.log/.solve.err INSIDE each challenge dir. rsi-079 closed the three
# solve observation faces; the rsi-080 round caught the fingerprint face
# still open live, and this round's RED quantified it on the production
# index: 14 byproduct fps keys (26 owner-links -- the empty .solve.err md5
# d41d8cd9 alone owned by 13 dirs) plus 14 byproduct blob keys, re-seeded
# on EVERY rebuild and growing one dir per round. These are runtime
# exhaust, never challenge identity: two different challenges whose
# wrappers both left an empty .solve.err collide via attach-fp (RED demo).
# Exclusion is name-exact at the two dir-level selection loops only -- the
# same design as rsi-079; a challenge shipping a file literally named
# .solve.log keeps its identity by a rename, and zip INNER entries keep
# full identity power (wrapper byproducts can never exist inside a
# challenge-shipped archive). The set mirrors the canonical
# core/router.WRAPPER_BYPRODUCTS; the selftest asserts both stay equal.
WRAPPER_BYPRODUCTS = frozenset({".solve.log", ".solve.err"})


# --- empty files carry zero identity (evolve-20261117-14) ----------------
# An empty file (size == 0) is content-free: its md5 is universally
# d41d8cd9... and its git blob sha is universally e69de29bb2..., so a
# 0-byte __init__.py / .gitkeep / placeholder makes two DIFFERENT
# challenges look identical to every fingerprint layer (RED demo on the
# incumbent: attach-fp collision + pre-download blob-fp zero-byte
# false kill). The rsi-081 GREEN audit left exactly 3 real dirs
# seeding the empty sha (ISCC Mobile Application, zjsdxs Misc-Xcode_v5.8,
# CCB-CISCN cython) plus 7 empty-content fps keys -- the next candidate
# listing ANY empty file would false-hit pre-download. Exclusion is
# size-based at the three selection loops and mirrored in the
# is_dup_blobs candidate side; the FP/BLOBS schema bump purges stale
# empty fingerprints via the load_index self-heal rebuild.


def _file_bom_polluted(path):
    """True when the file starts with a UTF-8 BOM: byte-unfaithful
    relative to any git original (legacy PowerShell rewrites, rsi-060
    evidence) -- its md5/blob-sha fingerprints must never seed the index
    nor match a pre-download candidate (evolve-20261116-19). File-level
    on purpose: pristine siblings of a rewritten file keep their dedup
    power, and the two selection loops already run FP_EXCLUDE first, so
    engine traces (_ctfpilot*, README, deploy boilerplate) never even
    reach this check."""
    try:
        with open(path, "rb") as f:
            return f.read(3) == b"\xef\xbb\xbf"
    except OSError:
        return False


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _zip_inner_fps(path):
    """Sampled inner-entry fingerprints of one .zip attachment:
    [(inner_basename, fp)], fp = lower(basename) + SEP + md5 of the inner
    CONTENT. At most FP_ZIP_SAMPLE qualifying entries (name-sorted);
    FP_EXCLUDE applies to inner basenames (deploy boilerplate inside a
    zip is just as shared as outside); inner size capped at FP_MAX_SIZE;
    depth-1 only (a nested zip entry contributes its opaque content md5
    under its own basename, no recursion)."""
    out = []
    try:
        zf = zipfile.ZipFile(path)
    except Exception:         # best-effort: a broken archive contributes
        return []             # no inner fps, never crashes a pull
    try:
        infos = []
        for i in zf.infolist():
            if i.is_dir():
                continue
            base = os.path.basename(i.filename.replace("\\", "/"))
            if not base or FP_EXCLUDE.match(base):
                continue
            if i.file_size == 0:
                continue          # empty inner entry: zero identity
                                 # (evolve-20261117-14)
            if i.file_size > FP_MAX_SIZE:
                continue
            infos.append((base, i))
        infos.sort(key=lambda t: t[0])
        for base, i in infos[:FP_ZIP_SAMPLE]:
            try:
                h = hashlib.md5()
                with zf.open(i) as f:
                    read = 0
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        read += len(chunk)
                        if read > FP_MAX_SIZE:
                            break
                        h.update(chunk)
                if read > FP_MAX_SIZE:
                    continue
                out.append((base, "%s%s%s" % (base.lower(), SEP_KEY,
                                              h.hexdigest())))
            except Exception:   # one corrupt entry (bad crc/zlib) skips
                continue      # alone; the rest of the sample survives
    finally:
        try:
            zf.close()
        except Exception:
            pass
    return out
    return out


def dir_fps(dirpath):
    """Content fingerprints of one challenge dir: {fp: filename},
    fp = lower(basename) + SEP + md5. Dirs with more than FP_MAX_FILES
    qualifying files keep a deterministic FP_KEEP name-sorted sample
    instead of dropping identity (source-dump false identity was the old
    worry; the live 6th-hole case proved 1000+-file challenge dirs must
    stay matchable against their zip re-packagings). .zip attachments
    also contribute their sampled inner-entry fingerprints (display
    name '<zip>!!<inner>'), so a zip-vs-extracted packaging delta
    cannot hide a re-packaging."""
    try:
        names = sorted(os.listdir(dirpath))
    except OSError:
        return {}
    fps = {}
    for n in names:
        if FP_EXCLUDE.match(n):
            continue
        if n in WRAPPER_BYPRODUCTS:
            continue          # wrapper exhaust: never challenge identity
                                 # (evolve-20261117-13)
        p = os.path.join(dirpath, n)
        if not os.path.isfile(p):
            continue
        try:
            _sz = os.path.getsize(p)
            if _sz == 0:
                continue          # empty content carries zero identity: a
                                 # 0-byte file is never same-challenge
                                 # evidence (evolve-20261117-14)
            if _sz > FP_MAX_SIZE:
                continue
            if _file_bom_polluted(p):
                continue          # rewritten bytes: fp would be unfaithful
                                 # (evolve-20261116-19)
            fps["%s%s%s" % (n.lower(), SEP_KEY, file_md5(p))] = n
            if n.lower().endswith(".zip"):
                for iname, ifp in _zip_inner_fps(p):
                    fps.setdefault(ifp, "%s!!%s" % (n, iname))
        except OSError:
            continue
    if len(fps) > FP_MAX_FILES:
        keep = sorted(fps.items())[:FP_KEEP]
        return dict(keep)
    return fps


def git_blob_sha(path):
    """Git blob SHA-1 of one local file: sha1('blob <size>\\0' + content),
    streamed (size from stat, content chunked). This is exactly the 'sha'
    field GitHub contents/tree listings carry per file -- the whole point
    (evolve-20261116-16) being that a pull candidate can be checked
    against materialized dirs BEFORE downloading a single content byte:
    the 2026-11-16 ISCC burn downloaded 734 files / 7 minutes for a
    candidate the post-download guards then rejected as a duplicate."""
    h = hashlib.sha1()
    h.update(b"blob %d\x00" % os.path.getsize(path))
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dir_blob_shas(dirpath):
    """Top-level git-blob fingerprints of one challenge dir: {blob_sha:
    filename}. Selection mirrors dir_fps exactly (FP_EXCLUDE names,
    FP_MAX_SIZE cap, FP_MAX_FILES/FP_KEEP sampling) so the index side and
    the pre-download candidate side (which sees only name/sha/size from
    the API listing) stay in lockstep. A .zip attachment contributes its
    own blob sha (an identically re-published zip matches byte-for-byte);
    zip-vs-EXTRACTED repackaging keeps being a post-download md5-layer
    job (dir_fps folds inner entries, git blob SHAs of inner files do
    not exist server-side)."""
    try:
        names = sorted(os.listdir(dirpath))
    except OSError:
        return {}
    blobs = {}
    for n in names:
        if FP_EXCLUDE.match(n):
            continue
        if n in WRAPPER_BYPRODUCTS:
            continue          # wrapper exhaust: never challenge identity
                                 # (evolve-20261117-13)
        p = os.path.join(dirpath, n)
        if not os.path.isfile(p):
            continue
        try:
            _sz = os.path.getsize(p)
            if _sz == 0:
                continue          # empty content carries zero identity
                                 # (evolve-20261117-14)
            if _sz > FP_MAX_SIZE:
                continue
            if _file_bom_polluted(p):
                continue          # rewritten bytes: blob sha would be
                                 # unfaithful (evolve-20261116-19)
            blobs[git_blob_sha(p)] = n
        except OSError:
            continue
    if len(blobs) > FP_MAX_FILES:
        keep = sorted(blobs.items())[:FP_KEEP]
        return dict(keep)
    return blobs


def comp_root(s):
    low = str(s or "").lower()
    for w in COMP_WORDS:
        if w in low:
            return w
    return ""


def year_of(s):
    """First 19xx/20xx year in the FULL string, '' when absent. The year
    participates in pair identity (evolve-20261116-11): core_name strips
    year digits from the core tail, and ledger keys keep their year in
    the repo part the '::'-tail split discards, so the year must be read
    before any tail-splitting -- '2024-Foo__bar' and '2025-Foo::bar' are
    different identities, never each other's dups."""
    m = YEAR.search(str(s or ""))
    return m.group(0) if m else ""


def core_name(s):
    s = str(s or "").strip()
    if not s:
        return ""
    if "__" in s:                       # ctf-20min dir / workspace dir form
        s = s.split("__", 1)[-1]
    if "::" in s:                       # ledger key 'owner/repo::path'
        s = s.split("::", 1)[-1]
    if "/" in s:                        # done.json 'repo/name'
        s = s.rsplit("/", 1)[-1]
    s = re.sub(r"^writeup[:_ ]?", "", s, flags=re.IGNORECASE)
    comp_w = comp_root(s)
    for _ in range(4):
        s2 = CAT_PREFIX.sub("", s)
        s2 = NUM_PREFIX.sub("", s2)
        s2 = CN_TIER.sub("", s2)
        if s2 == s:
            break
        s = s2
    s = SEP.sub("", s).lower()
    s = YEAR.sub("", s)
    if comp_w:
        s = s.replace(comp_w, "")
    return s


def _alias_cores(core):
    """Category-stripped alias cores of a comp-removed core
    (evolve-20261117-06, dup 8th hole). core_name runs the CAT/NUM/CN
    prefix strips BEFORE the competition word is cut out of mid-string,
    so a whole-repo single-challenge name ('2022-xhlj-crypto-myerrorlearn')
    keeps its category glued into the core ('cryptomyerrorlearn') while
    the index's single-form entries hold the clean core ('myerrorlearn');
    whole-repo candidates then skip fuzzy by design (evolve-11 misfire)
    and the glued exact key never fires. Re-running the strips on the
    comp-removed core yields the clean alias; whole-repo exact-equality
    against it stays a strict subset of the containment fuzzy already
    accepts for singles, so no new misfire surface opens (the 2025
    LanqiaoCup-Finals 'cupfinals' core carries no leading category word
    and gains no alias)."""
    out = []
    s = str(core or "")
    for _ in range(3):
        s2 = CAT_PREFIX.sub("", s)
        s2 = NUM_PREFIX.sub("", s2)
        s2 = CN_TIER.sub("", s2)
        if not s2 or s2 == s:
            break
        out.append(s2)
        s = s2
    return out


def _pair_keys(entry):
    """All (comp, year, core) index keys a source entry contributes
    (year participates since evolve-20261116-11)."""
    out = []
    cc = core_name(entry)
    cr = comp_root(entry)
    if cc and len(cc) >= 2:
        out.append((cr or "unknown", year_of(entry), cc))
        # dir names also carry the challenge tail after '__' when the name
        # is `repo__title`: core_name already prefers that tail, and comp
        # comes from the whole string, so one pair covers it.
    return out


def build_index():
    """Fold legacy json + challenges db + legacy dirs + learned cards."""
    pairs = {}
    counts = {"done.json": 0, "challenges": 0, "legacy-dirs": 0,
              "workspace-dirs": 0, "learned-cards": 0}

    def add(entry, src):
        entry = str(entry or "").strip()
        if len(entry) < 3 or entry.lower() in ("none", "nan"):
            return
        for cr, y, cc in _pair_keys(entry):
            pairs.setdefault(SEP_KEY.join((cr, y, cc)), set()).add(src)
        counts[src] = counts.get(src, 0) + 1

    # A. the old timer's dedup json (another TA account, 20-min feed)
    try:
        with open(LEGACY_JSON, encoding="utf-8") as f:
            blob = json.load(f)
        for e in blob.get("done", []):
            add(e, "done.json")
    except (OSError, ValueError):
        pass

    # B. every challenges-table name (solve + learn records)
    try:
        conn = sqlite3.connect(DB_PATH)
        for (n, ) in conn.execute("SELECT name FROM challenges").fetchall():
            add(n, "challenges")
        conn.close()
    except sqlite3.Error:
        pass

    # C. materialized dirs: merged ctf-20min pulls (``...__...`` under
    # ctf-feed) and stray ``...__...`` dirs at the workspace root
    for base, src in ((LEGACY_DIRS, "legacy-dirs"), (WS_ROOT, "workspace-dirs")):
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for n in names:
            if src == "workspace-dirs" and "__" not in n:
                continue                      # only challenge-shaped dirs
            if "__" not in n or not os.path.isdir(os.path.join(base, n)):
                continue
            add(n, src)

    # D. learned card names (minus -NN / -vN-NN chunk suffixes)
    try:
        for fn in os.listdir(LEARNED_DIR):
            if fn.endswith(".json"):
                add(re.sub(r"-(v\d+-)?\d+\.json$", "", fn), "learned-cards")
    except OSError:
        pass

    # E. attachment fingerprints per materialized challenge dir (dup 4th
    # hole). fps maps fp -> {dirname: True}; content files only -- libc/ld
    # and deploy boilerplate are excluded precisely because they ARE shared
    # across distinct challenges.
    # F. git blob SHAs of the same dirs (evolve-20261116-16, pre-download
    # dedup): same selection, so a re-published duplicate can be rejected
    # from the API listing alone, before any content download.
    fps = {}
    blobs = {}
    for base in (LEGACY_DIRS, WS_ROOT):
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for n in names:
            if "__" not in n and not re.match(r"^\d{4}-", n):
                continue                      # only challenge-shaped dirs
            full = os.path.join(base, n)
            if not os.path.isdir(full):
                continue
            for fp in dir_fps(full):
                fps.setdefault(fp, {})[n] = True
            for sha in dir_blob_shas(full):
                blobs.setdefault(sha, {})[n] = True

    return {
        "gen": time.time(),
        "fp_schema": FP_SCHEMA,
        "blobs_schema": BLOBS_SCHEMA,
        "pairs_schema": PAIRS_SCHEMA,
        "counts": counts,
        "pairs": {k: sorted(v) for k, v in sorted(pairs.items())},
        "fps": fps,
        "blobs": blobs,
        "writeups_done": {},
    }


def _needs_rebuild(prior, force=False):
    """True when load_index() must rebuild instead of returning the prior
    on-disk index as-is. Factored out of load_index for
    [evolve-20261117-17]: the read-only self_heal_diff() preview must
    answer the exact same question the mutating path answers, through
    one shared predicate -- a preview that drifts from the real rebuild
    decision would be worse than no preview."""
    if (prior is not None and not force and "fps" in prior
            and prior.get("fp_schema") == FP_SCHEMA
            and "blobs" in prior
            and prior.get("blobs_schema") == BLOBS_SCHEMA
            and prior.get("pairs_schema") == PAIRS_SCHEMA):
        return False
    return True


def load_index(force=False):
    """Return (index, path); build+persist when missing or force=True.
    A forced rebuild KEEPS writeups_done (the incremental learn-md5 memory):
    losing it would let an already-learned writeup be re-learned one more
    time after every rebuild (cards pile up again).
    A prior index WITHOUT the 'fps' section (pre attachment-fingerprint
    builds) is rebuilt too, still keeping writeups_done. Since fp schema 2
    (zip/sampled fingerprints, 2026-11-16) a prior index whose fps were
    computed by the older dir_fps is rebuilt as well -- self-healing, so
    a pull that ran on pre-change code (old in-memory module) cannot pin
    a schema-1 fps section back over a schema-2 rebuild. Since blobs
    schema 1 (evolve-20261116-16) a prior index without the 'blobs'
    section (or a stale blobs_schema) is rebuilt the same way. Since
    pairs schema 2 (evolve-20261116-11, year-in-identity) a prior index
    without a matching pairs_schema is rebuilt the same way too.
    [evolve-20261117-17] the rebuild decision lives in _needs_rebuild()
    now; behavior is byte-for-byte the same. For a read-only preview of
    what a rebuild WOULD change, call self_heal_diff() -- it computes the
    same would-be index without writing anything."""
    prior = None
    try:
        with open(INDEX_PATH, encoding="utf-8") as f:
            prior = json.load(f)
    except (OSError, ValueError):
        prior = None
    if not _needs_rebuild(prior, force):
        return prior, INDEX_PATH
    idx = build_index()
    idx["fp_schema"] = FP_SCHEMA
    if prior is not None:
        idx["writeups_done"] = prior.get("writeups_done") or {}
    try:
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return idx, INDEX_PATH


def self_heal_diff(force=False):
    """[evolve-20261117-17] Read-only preview of the load_index()
    self-heal: computes the would-be rebuilt index IN MEMORY and diffs it
    against the on-disk prior without writing a single byte anywhere
    (build_index() only reads its sources; this function never opens the
    index for writing). Closes the evolve-20261117-14 finding (b)
    papercut: schema-bump rounds had to mutate the production index first
    and hand-diff file copies aside to describe the self-heal effect
    (rsi-081 hand-counted its 14 polluted fps keys; later chain rows
    described rebuilds secondhand).

    Returns a report dict: will_rebuild (bool); reason
    (missing-index | forced | schema-drift | schemas-current); prior/new
    fp_schema/blobs_schema/pairs_schema; per-section {prior, new, added,
    purged, kept} for fps/blobs/pairs; build source counts; and
    writeups_done_carried (the incremental learn-md5 memory load_index
    would keep across the rebuild). The mutating path answers the SAME
    question through the SAME _needs_rebuild() predicate, so the preview
    cannot drift from what the next load_index() would actually do."""
    prior = None
    try:
        with open(INDEX_PATH, encoding="utf-8") as f:
            prior = json.load(f)
    except (OSError, ValueError):
        prior = None

    def _sec(prior_sec, new_sec):
        p = set((prior_sec or {}).keys())
        n = set((new_sec or {}).keys())
        return {"prior": len(p), "new": len(n), "added": len(n - p),
                "purged": len(p - n), "kept": len(p & n)}

    if not _needs_rebuild(prior, force):
        return {"will_rebuild": False, "reason": "schemas-current",
                "index_path": INDEX_PATH,
                "fp_schema": FP_SCHEMA, "blobs_schema": BLOBS_SCHEMA,
                "pairs_schema": PAIRS_SCHEMA,
                "fps": _sec((prior or {}).get("fps"),
                            (prior or {}).get("fps")),
                "blobs": _sec((prior or {}).get("blobs"),
                              (prior or {}).get("blobs")),
                "pairs": _sec((prior or {}).get("pairs"),
                              (prior or {}).get("pairs")),
                "writeups_done_carried":
                    len((prior or {}).get("writeups_done") or {})}

    idx = build_index()
    idx["fp_schema"] = FP_SCHEMA
    carried = {}
    if prior is not None:
        carried = prior.get("writeups_done") or {}
        idx["writeups_done"] = carried
    reason = ("missing-index" if prior is None
              else "forced" if force else "schema-drift")
    return {"will_rebuild": True, "reason": reason,
            "index_path": INDEX_PATH,
            "fp_schema_prior": (prior or {}).get("fp_schema"),
            "fp_schema_new": FP_SCHEMA,
            "blobs_schema_prior": (prior or {}).get("blobs_schema"),
            "blobs_schema_new": BLOBS_SCHEMA,
            "pairs_schema_prior": (prior or {}).get("pairs_schema"),
            "pairs_schema_new": PAIRS_SCHEMA,
            "fps": _sec((prior or {}).get("fps"), idx.get("fps")),
            "blobs": _sec((prior or {}).get("blobs"), idx.get("blobs")),
            "pairs": _sec((prior or {}).get("pairs"), idx.get("pairs")),
            "counts": idx.get("counts") or {},
            "writeups_done_carried": len(carried)}


def _save(index, path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def is_dup(index, candidate, whole_repo=False):
    """True when `candidate` (a ledger key, gh title, or folder name) was
    already processed by any known source. Returns (bool, reason).
    Multi-part candidates ('key title') are checked token by token.

    evolve-20261116-11 (dup 5th hole): the YEAR participates in identity
    -- exact and fuzzy only match same-year (or yearless-legacy) entries,
    so different-year same-name contests/challenges are NOT dups; and
    whole-repo candidates ('owner/repo::.') never run the fuzzy
    containment layer at all: a contest-root core is by construction a
    substring of many single-challenge cores ('cupfinals' <
    '0x100cupfinals'), so containment there can only misfire (live
    misfire: the 2025 LanqiaoCup-Finals whole-repo pick was fuzzy-killed
    by the 2024 0x100 writeup row). Whole-repo re-pulls stay covered by
    ledger keys + the attach/blob fingerprint layers."""
    idx_pairs = index.get("pairs") or {}
    reason = ""
    for tok in str(candidate).split():
        if not tok:
            continue
        cc = core_name(tok)
        cr = comp_root(tok)
        y = year_of(tok)
        if not cc or len(cc) < 2:
            continue
        exact_key = SEP_KEY.join(((cr or "unknown"), y, cc))
        if exact_key in idx_pairs:
            return True, "exact(%s <- %s)" % (
                exact_key.replace(SEP_KEY, "/"),
                ",".join(idx_pairs[exact_key]))
        if y:                    # year-carrying candidates still dedup
            legacy_key = SEP_KEY.join(((cr or "unknown"), "", cc))
            if legacy_key in idx_pairs:   # against yearless legacy entries
                return True, "exact(%s <- %s, yearless-legacy)" % (
                    legacy_key.replace(SEP_KEY, "/"),
                    ",".join(idx_pairs[legacy_key]))
        # evolve-20261117-06 (dup 8th hole): whole-repo candidates keep a
        # mid-name category word glued into the core ('cryptomyerrorlearn')
        # because the prefix strips run before comp removal, while index
        # singles hold the clean core ('myerrorlearn') -- and fuzzy is off
        # for whole-repo by design, so only exact can fire and never does.
        # Exact-check the category-stripped alias cores too (same-comp only;
        # alias-equality is narrower than the containment fuzzy already
        # tolerates for singles, so the 2025-LanqiaoCup-Finals misfire
        # precedent gains no new surface). Live case: the 2026-11-17 pull
        # of CTF-Archives/2022-xhlj-crypto-myerrorlearn::. slipped ledger
        # (other-era keyspace), exact (glued core), fuzzy (skipped), blob-fp
        # (old dir's main.py drifted LF 602B vs repo CRLF 634B) and
        # attach-fp (same drift) all at once.
        if whole_repo and cr:
            for alt in _alias_cores(cc):
                if len(alt) < 3:
                    continue
                alt_key = SEP_KEY.join((cr, y, alt))
                if alt_key in idx_pairs:
                    return True, "exact-alias(%s <- %s)" % (
                        alt_key.replace(SEP_KEY, "/"),
                        ",".join(idx_pairs[alt_key]))
                if y:
                    alt_legacy = SEP_KEY.join((cr, "", alt))
                    if alt_legacy in idx_pairs:
                        return True, \
                            "exact-alias(%s <- %s, yearless-legacy)" % (
                                alt_legacy.replace(SEP_KEY, "/"),
                                ",".join(idx_pairs[alt_legacy]))
        if cr and not whole_repo:
            prefix = cr + SEP_KEY
            for k in idx_pairs:
                if not k.startswith(prefix):
                    continue
                parts = k.split(SEP_KEY)
                # pairs keys are comp<SEP>year<SEP>core since schema 2;
                # tolerate legacy 2-part keys read from a stale index
                if len(parts) == 3:
                    kyear, old = parts[1], parts[2]
                else:
                    kyear, old = "", parts[-1]
                if not old or len(old) < 3:
                    continue
                if y and kyear and kyear != y:
                    continue        # cross-year: different identity, not a dup
                if old in cc or cc in old:
                    reason = "fuzzy(%s/%s ~ %s, y=%s)" % (cr, old, cc,
                                                          kyear or "-")
                    return True, reason
    return False, ""


def is_dup_dir(index, dirpath, self_name=None):
    """Attachment-fingerprint dedup (dup 4th hole): True when any CONTENT
    file (lower(name)+md5) of `dirpath` already exists under a different
    materialized challenge dir in the index. `self_name` (default: the
    dir's own basename) is excluded so re-checks of known dirs pass."""
    if self_name is None:
        self_name = os.path.basename(os.path.abspath(str(dirpath)))
    idx_fps = index.get("fps") or {}
    if not idx_fps:
        return False, ""
    for fp, fname in dir_fps(dirpath).items():
        owners = idx_fps.get(fp)
        if not owners:
            continue
        other = [o for o in owners if o != self_name]
        if not other:
            continue
        return True, "attach-fp(%s <- %s)" % (fname, ",".join(sorted(other)))
    return False, ""


def note_pull_fp(index, path, chall_dir):
    """Fold a freshly materialized challenge dir's fingerprints into the
    index, so a later re-packaging of the same challenge cannot pass."""
    name = os.path.basename(os.path.abspath(str(chall_dir)))
    for fp in dir_fps(chall_dir):
        index.setdefault("fps", {}).setdefault(fp, {})[name] = True
    _save(index, path)


def is_dup_blobs(index, pairs, self_name=None):
    """Pre-download fingerprint dedup (evolve-20261116-16): `pairs` is an
    iterable of (name, git_blob_sha, size) exactly as a GitHub contents/
    tree listing reports them -- no local content needed. True when any
    qualifying content blob already sits under a different materialized
    identity in the index. FP_EXCLUDE names never count (shared libc /
    deploy boilerplate must not fabricate a hit) and oversize files are
    ignored to mirror what dir_blob_shas registers."""
    idx_blobs = index.get("blobs") or {}
    if not idx_blobs:
        return False, ""
    for name, sha, size in pairs:
        try:
            if int(size) == 0:
                continue   # empty content: zero identity, mirrors the
                            # registration side (evolve-20261117-14)
            if size and int(size) > FP_MAX_SIZE:
                continue
        except (TypeError, ValueError):
            pass
        if not sha or FP_EXCLUDE.match(str(name or "")):
            continue
        owners = idx_blobs.get(str(sha))
        if not owners:
            continue
        other = [o for o in owners if o != self_name]
        if other:
            return True, "blob-fp(%s <- %s)" % (name, ",".join(sorted(other)))
    return False, ""


def note_pull_blobs(index, path, chall_dir):
    """Fold a freshly materialized challenge dir's git blob SHAs into the
    index, so a later re-publication of the same content precheck-hits
    pre-download instead of burning a full attachment download."""
    name = os.path.basename(os.path.abspath(str(chall_dir)))
    for sha in dir_blob_shas(chall_dir):
        index.setdefault("blobs", {}).setdefault(sha, {})[name] = True
    _save(index, path)


def note_learned(index, path, name, md5):
    """Record a learned writeup so future learn calls with the same content
    skip, and bench-pull never re-pulls this challenge."""
    try:
        index.setdefault("writeups_done", {})[name] = md5
    except TypeError:
        pass
    for cr, y, cc in _pair_keys(name):
        index.setdefault("pairs", {}) \
             .setdefault(SEP_KEY.join((cr, y, cc)), ["dedup.note"])
    _save(index, path)


def md5_text(text):
    return hashlib.md5(text.encode("utf-8", "replace")).hexdigest()
