"""GitHub challenge-source adapter for bench-pull (2026-11-14).

Sources (spec strings, stable contract for skills / scheduler prompts):
  gh-org:<org>       e.g. gh-org:CTF-Archives -- pick a random year-named
                     repository of the org, then a random challenge inside.
  gh:<owner>/<repo>  one specific repository (same walk); use for newly
                     discovered archives -- no code change needed, this is
                     the "agent finds its own sources" mechanism.

Repo layouts handled (CTF-Archives convention, verified 2026-11-14):
  competition repo:  root holds per-challenge dirs ('Crypto-0_1_Game', ...);
                     a dir that contains only dirs is treated as a category
                     level and descended once.
  single-challenge:  repo root itself is the challenge ('2022-xhlj-web-...').

All traffic goes to api.github.com only (stable channel on this host;
github.com itself is intermittently flaky). Auth: reuse the logged-in gh
CLI token (5000 req/h) when available, else anonymous (60 req/h -- still
enough for a 20-minute feed loop). The org listing is cached 24h; a pull
costs ~2-4 API calls.
"""
import json
import os
import random
import re
import subprocess
import time
import http.client
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
UA = "CTFPilot-bench-pull/0.1"
MAX_FILE_BYTES = 100 * 1024 * 1024   # contents/raw API hard limit
MAX_FILES_PER_CHALL = 40            # skip attachments beyond this
MAX_DEPTH = 2                       # recursion below the challenge root
ORG_TRY_REPOS = 12                  # stale repos tolerated per org pull
PRECHECK_RETRIES = 3                # re-picks inside one repo when the
                                      # pre-download precheck skips a pick
                                      # (evolve-20261116-16)
# rsi-123 (2026-11-18): pull-chain fail-fast time budget. The 30s x3-retry
# per-request worst case (96s/request, 6-8 serial requests = up to ~768s a
# pull) made every api.github.com latency blip blow through the 300s outer
# tool timeout with zero progress output, killing bench-pull AFTER the
# bytes landed but BEFORE the ledger write -- two consecutive rounds
# (n1flgsrv 2026-11-18 00:23, N1drone 2026-11-18 01:01) each needed a manual
# ledger repair. Now one pull gets a hard wall budget and fails fast with
# SourceError (rc=4) instead of hanging past any outer timeout, so the
# ledger is never lost to a timeout kill again. Worst case without budget:
# (15+1)x2 = 32s/request; budget fires first at 240s.
API_TIMEOUT_S = 15                  # was a hardcoded 30 (urlopen timeout)
API_RETRIES = 2                    # total attempts per request (was 3)
RETRY_SLEEP_S = 1                   # backoff between attempts (was 2)
PULL_BUDGET_S = 240                 # hard wall budget for one gh_pull call
YEAR_REPO = re.compile(r"^(19|20)\d{2}-")
CHALL_NAME_SAN = re.compile(r'[\\/:*?"<>|\s]+')
TRAILING_SAN = re.compile(r"[. ]+\Z")

# rsi-050 (2026-11-16): build/deploy manifests that mark a repo ROOT as a
# single-challenge app (Dockerfile + package.json + views/... sits next to
# framework dirs). Competition-repo roots hold README.md and loose
# attachment files only (sampled 2026-11-16: CCB/jksn/LongjianCup/
# LanqiaoCup/Fjgys/WQB -- none match), so manifests never fire there;
# entry-point-ish names (index.php/app.py) are deliberately NOT hints
# because loose web attachments share those names.
ROOT_MANIFESTS = {
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements.txt", "pyproject.toml", "setup.py", "pipfile",
    "go.mod", "cargo.toml", "makefile", "cmakelists.txt", "pom.xml",
    "build.gradle", "gemfile", "composer.json",
}


def _has_root_manifest(files):
    """True when any repo-root file is a build/deploy manifest."""
    return any((e.get("name") or "").lower() in ROOT_MANIFESTS
               for e in files)


class SourceError(Exception):
    """Hard source failure (bad spec, API unreachable, unexpected payload)."""


def _token():
    """Reuse the logged-in gh CLI token; fall back to anonymous."""
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True,
                              text=True, timeout=20)
        tok = (out.stdout or "").strip()
        if out.returncode == 0 and tok:
            return tok
    except Exception:
        pass
    return ""


def _budget_exceeded(deadline):
    """True when a pull budget deadline has passed (rsi-123)."""
    return deadline is not None and time.time() > deadline


def _api(path, raw=False, token="", deadline=None):
    """GET api.github.com<path>; retries network errors (not HTTP errors).
    deadline (rsi-123): when set and already past, fail fast instead of
    spending another API_TIMEOUT_S round trip -- a pull that is out of
    budget must surface promptly, never hang past an outer timeout."""
    if _budget_exceeded(deadline):
        raise SourceError("pull budget exceeded before %s" % (API + path))
    url = API + path
    headers = {"User-Agent": UA,
               "Accept": "application/vnd.github.raw" if raw
               else "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    last = None
    for _ in range(API_RETRIES):
        if _budget_exceeded(deadline):
            raise SourceError("pull budget exceeded at %s (last: %s)"
                             % (url, last))
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT_S) as r:
                if raw:
                    return r.read()
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise SourceError("HTTP %s on %s" % (e.code, url))
        except (urllib.error.URLError, OSError, ValueError,
                http.client.IncompleteRead, http.client.HTTPException) as e:
            last = e
            time.sleep(RETRY_SLEEP_S)
    raise SourceError("api unreachable: %s (%s)" % (url, last))


def _entries(owner_repo, subpath, token="", deadline=None):
    """Contents listing of one directory: list of {type,name,path,size,...}."""
    data = _api("/repos/%s/contents/%s" % (owner_repo,
                                            urllib.parse.quote(subpath)),
                token=token, deadline=deadline)
    if not isinstance(data, list):
        raise SourceError("unexpected payload for %s/%s" % (owner_repo, subpath))
    return data


def _walk_files(owner_repo, subpath, token, depth=0, budget=None,
                deadline=None):
    """Recursively collect downloadable file entries below subpath."""
    budget = budget if budget is not None else MAX_FILES_PER_CHALL
    out = []
    try:
        entries = _entries(owner_repo, subpath, token, deadline=deadline)
    except SourceError:
        # rsi-123: budget exhaustion must propagate (it would otherwise
        # read as a harmless empty walk -> 'no downloadable files' and
        # mask the fail-fast signal); plain network errors stay swallowed
        # per the original tolerance semantics.
        if _budget_exceeded(deadline):
            raise
        return out
    for e in entries:
        if budget <= 0:
            break
        t = e.get("type")
        if t == "file":
            out.append(e)
            budget -= 1
        elif (t == "dir" and depth < MAX_DEPTH
                and e["name"] != ".github"):
            # .github is repo CI metadata, never challenge content
            sub = _walk_files(owner_repo, e["path"], token,
                              depth + 1, budget, deadline=deadline)
            out.extend(sub)
            budget -= len(sub)
    return out


def _download(owner_repo, entry, dest_dir, token="", deadline=None):
    if entry.get("size", 0) > MAX_FILE_BYTES:
        return False
    data = _api("/repos/%s/contents/%s" % (owner_repo,
                                            urllib.parse.quote(entry["path"])),
                raw=True, token=token, deadline=deadline)
    with open(os.path.join(dest_dir, entry["name"]), "wb") as f:
        f.write(data)
    return True


def _sanitized(name):
    return TRAILING_SAN.sub("", CHALL_NAME_SAN.sub("_", name)) or "chall"


def _pick_challenge(owner_repo, used, token, deadline=None):
    """Pick a random unseen challenge inside one repository.

    Returns (key, title, files) or None when the repo holds nothing fresh.
    key is the dedup identity ('owner/repo::relpath', or '::.' for a
    whole-repo challenge)."""
    root = _entries(owner_repo, "", token, deadline=deadline)
    dirs = [e for e in root if e.get("type") == "dir"]
    files = [e for e in root if e.get("type") == "file"]
    # rsi-050 (2026-11-16): a root that carries build/deploy manifests NEXT
    # TO framework dirs (views/, static/, src/, service/, docker/) is a
    # single-challenge app -- the whole repo is the challenge. The old code
    # unconditionally descended into a random subdir and gutted such repos
    # (2023-hgame-week2-web-designer lost index.js/Dockerfile/package.json
    # to views/, flipping triage misc(0.2) -> web(0.67) once restored).
    if dirs and _has_root_manifest(files):
        key = "%s::." % owner_repo
        if key in used:
            return None
        return key, owner_repo.split("/")[-1], _walk_files(
            owner_repo, "", token, deadline=deadline)
    if dirs:
        fresh = [e for e in dirs
                 if "%s::%s" % (owner_repo, e["name"]) not in used]
        if not fresh:
            return None
        # rsi-126 (2026-11-18): a drained category must rotate to the
        # next category, not exhaust the whole repo. Live-fire on
        # Nu1LCTF/n1ctf-2025: misc(1)/pwn(2) challenges were already
        # pulled, random.choice landed on one of them, fresh_sub came up
        # empty and _pick_challenge returned None with crypto(3)/web(4)
        # still fresh -- the whole pull burned to a false 'repo exhausted'
        # (2s, attempt=0, no precheck-duplicate evidence; 400-seed replay
        # on the real repo shape: 49% false-exhausted while 7 challenges
        # remain). Random order keeps selection uniform; a drained
        # category just loses its turn. Whole-repo '::.' keys untouched.
        random.shuffle(fresh)
        for pick in fresh:
            key = "%s::%s" % (owner_repo, pick["name"])
            inner = _entries(owner_repo, pick["path"], token,
                             deadline=deadline)
            # category level? (root dir that only holds dirs -> descend once)
            inner_dirs = [e for e in inner if e.get("type") == "dir"]
            inner_files = [e for e in inner if e.get("type") == "file"]
            if inner_dirs and not inner_files:
                fresh_sub = [e for e in inner_dirs
                             if "%s::%s" % (owner_repo, e["name"]) not in used]
                if not fresh_sub:
                    continue    # this category is drained; next category
                pick = random.choice(fresh_sub)
                key = "%s::%s" % (owner_repo, pick["name"])
            title = "%s-%s" % (owner_repo.split("/")[-1], pick["name"])
            return key, title, _walk_files(owner_repo, pick["path"], token,
                                           deadline=deadline)
        return None
    if files:
        key = "%s::." % owner_repo
        if key in used:
            return None
        return key, owner_repo.split("/")[-1], files
    return None


def _org_repos(org, cache_path, token="", deadline=None):
    """Year-named repositories of an org, cached for 24h."""
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                blob = json.load(f)
            if (time.time() - blob.get("ts", 0) < 86400
                    and blob.get("repos")):
                return blob["repos"]
        except (OSError, ValueError):
            pass
    repos = []
    for page in (1, 2, 3):           # up to 300 repositories
        batch = _api("/orgs/%s/repos?per_page=100&page=%d" % (org, page),
                     token=token, deadline=deadline)
        if not isinstance(batch, list) or not batch:
            break
        repos.extend(r["name"] for r in batch)
        if len(batch) < 100:
            break
    repos = sorted(r for r in set(repos) if YEAR_REPO.match(r))
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "repos": repos}, f)
    except OSError:
        pass
    return repos


def gh_pull(spec, dest, used, cache_dir, precheck=None, deadline=None):
    """Pull one unseen challenge for spec.

    Returns (key, chall_dir, nfiles, nskip) on success, or
    (None, reason, 0, 0) when the source is exhausted. Raises SourceError
    for hard failures (bad spec / API unreachable).

    deadline (rsi-123): epoch-seconds wall budget for this one call; when
    None a fresh PULL_BUDGET_S budget starts now. Every API round trip in
    the pick/walk/download chain checks it and fails fast with
    SourceError('pull budget exceeded ...'), so a latency blip ends the
    pull promptly (caller maps it to rc=4) instead of hanging past the
    outer tool timeout AFTER bytes landed but BEFORE the caller's ledger
    write -- the exact truncation shape that lost the ledger twice on
    2026-11-18 (n1flgsrv / N1drone pulls).

    precheck (evolve-20261116-16) -- optional callback
    ``precheck(key, title, entries) -> reason``: called with the picked
    candidate's ledger key, display title and the entries list (the very
    API listing dicts, so name/sha/size are all known) BEFORE any content
    download and before the destination dir exists. A non-empty return
    rejects the pick pre-download (the pick's key is consumed locally
    and another candidate is tried); an empty string proceeds. It powers
    the pre-download duplicate guard: the 2026-11-16 ISCC burn downloaded
    734 files / 7 minutes for a candidate the post-download guards then
    rejected, so name/blob dedup now runs before the bytes flow."""
    dl = deadline if deadline is not None else time.time() + PULL_BUDGET_S
    os.makedirs(cache_dir, exist_ok=True)
    token = _token()
    if _budget_exceeded(dl):
        raise SourceError("pull budget exceeded (pre-flight, spec=%s)" % spec)
    if spec.startswith("gh-org:"):
        org = spec[len("gh-org:"):]
        if not org:
            raise SourceError("empty org in %r" % spec)
        repos = _org_repos(org, os.path.join(cache_dir, "org-%s.json" % org),
                           token=token, deadline=dl)
        if not repos:
            return None, "org %s has no year-named repos" % org, 0, 0
        random.shuffle(repos)
        last_err = "no fresh challenge"
        pre_skips = []
        for repo in repos[:ORG_TRY_REPOS]:
            if _budget_exceeded(dl):
                raise SourceError("pull budget exceeded (repo walk, %s)" % repo)
            found = None
            for _retry in range(PRECHECK_RETRIES + 1):
                try:
                    found = _pick_challenge("%s/%s" % (org, repo), used,
                                            token, deadline=dl)
                except SourceError as e:
                    last_err = str(e)
                    found = None
                    # rsi-123: a budget exhaustion inside the pick must not
                    # be downgraded to 'repo exhausted, try next repo' --
                    # the whole pull is out of time, fail fast.
                    if _budget_exceeded(dl):
                        raise
                    break
                if not found:
                    break
                key, title, entries = found
                why = ""
                if precheck is not None:
                    why = precheck(key, title, entries)
                if not why:
                    break                     # clean candidate, download it
                pre_skips.append("%s %s" % (key, why))
                used = used | {key}           # consume the pick locally so
                found = None                  # a re-pick rotates candidates
            if not found:
                continue                    # try the next repo
            key, title, entries = found
            d = os.path.join(dest, _sanitized(title))
            os.makedirs(d, exist_ok=True)
            n = sk = 0
            for e in entries:
                if _budget_exceeded(dl):
                    raise SourceError(
                        "pull budget exceeded (download, %s/%d files)"
                        % (title, n))
                try:
                    n += 1 if _download("%s/%s" % (org, repo), e, d, token,
                                        deadline=dl) else 0
                    if e.get("size", 0) > MAX_FILE_BYTES:
                        sk += 1
                except SourceError:
                    if _budget_exceeded(dl):
                        raise SourceError(
                            "pull budget exceeded (download, %s/%d files)"
                            % (title, n))
                    sk += 1
            if n == 0:
                try:
                    os.rmdir(d)
                except OSError:
                    pass
                last_err = "challenge %s had no downloadable files" % title
                continue
            return key, d, n, sk
        if pre_skips:
            last_err = ("all candidates duplicate pre-download "
                        "(%d skips, last: %s)"
                        % (len(pre_skips), pre_skips[-1]))
        return None, last_err, 0, 0
    if spec.startswith("gh:"):
        owner_repo = spec[3:]
        if "/" not in owner_repo:
            raise SourceError("bad spec %r -- want gh:<owner>/<repo>" % spec)
        pre_skip = ""
        found = None
        for _retry in range(PRECHECK_RETRIES + 1):
            if _budget_exceeded(dl):
                raise SourceError(
                    "pull budget exceeded (gh pick retry %d, last skip: %s)"
                    % (_retry, pre_skip or "-"))
            found = _pick_challenge(owner_repo, used, token, deadline=dl)
            if not found:
                break
            key, title, entries = found
            why = ""
            if precheck is not None:
                why = precheck(key, title, entries)
            if not why:
                break
            pre_skip = "%s %s" % (key, why)
            used = used | {key}
            found = None
        if not found:
            if pre_skip:
                return None, ("repo %s: precheck-duplicate(%s)"
                               % (owner_repo, pre_skip)), 0, 0
            return None, "repo %s exhausted" % owner_repo, 0, 0
        key, title, entries = found
        d = os.path.join(dest, _sanitized(title))
        os.makedirs(d, exist_ok=True)
        n = sk = 0
        for e in entries:
            if _budget_exceeded(dl):
                raise SourceError(
                    "pull budget exceeded (download, %s/%d files)"
                    % (title, n))
            try:
                n += 1 if _download(owner_repo, e, d, token,
                                    deadline=dl) else 0
                if e.get("size", 0) > MAX_FILE_BYTES:
                    sk += 1
            except SourceError:
                if _budget_exceeded(dl):
                    raise SourceError(
                        "pull budget exceeded (download, %s/%d files)"
                        % (title, n))
                sk += 1
        if n == 0:
            try:
                os.rmdir(d)
            except OSError:
                pass
            return None, "repo %s: no downloadable files" % owner_repo, 0, 0
        return key, d, n, sk
    raise SourceError("unknown source spec: %r" % spec)
