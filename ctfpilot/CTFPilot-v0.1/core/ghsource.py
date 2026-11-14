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
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
UA = "CTFPilot-bench-pull/0.1"
MAX_FILE_BYTES = 100 * 1024 * 1024   # contents/raw API hard limit
MAX_FILES_PER_CHALL = 40            # skip attachments beyond this
MAX_DEPTH = 2                       # recursion below the challenge root
ORG_TRY_REPOS = 12                  # stale repos tolerated per org pull
YEAR_REPO = re.compile(r"^(19|20)\d{2}-")
CHALL_NAME_SAN = re.compile(r'[\\/:*?"<>|\s]+')
TRAILING_SAN = re.compile(r"[. ]+\Z")


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


def _api(path, raw=False, token=""):
    """GET api.github.com<path>; retries network errors (not HTTP errors)."""
    url = API + path
    headers = {"User-Agent": UA,
               "Accept": "application/vnd.github.raw" if raw
               else "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    last = None
    for _ in range(3):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if raw:
                    return r.read()
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise SourceError("HTTP %s on %s" % (e.code, url))
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = e
            time.sleep(2)
    raise SourceError("api unreachable: %s (%s)" % (url, last))


def _entries(owner_repo, subpath, token=""):
    """Contents listing of one directory: list of {type,name,path,size,...}."""
    data = _api("/repos/%s/contents/%s" % (owner_repo,
                                            urllib.parse.quote(subpath)),
                token=token)
    if not isinstance(data, list):
        raise SourceError("unexpected payload for %s/%s" % (owner_repo, subpath))
    return data


def _walk_files(owner_repo, subpath, token, depth=0, budget=None):
    """Recursively collect downloadable file entries below subpath."""
    budget = budget if budget is not None else MAX_FILES_PER_CHALL
    out = []
    try:
        entries = _entries(owner_repo, subpath, token)
    except SourceError:
        return out
    for e in entries:
        if budget <= 0:
            break
        t = e.get("type")
        if t == "file":
            out.append(e)
            budget -= 1
        elif t == "dir" and depth < MAX_DEPTH:
            sub = _walk_files(owner_repo, e["path"], token,
                              depth + 1, budget)
            out.extend(sub)
            budget -= len(sub)
    return out


def _download(owner_repo, entry, dest_dir, token=""):
    if entry.get("size", 0) > MAX_FILE_BYTES:
        return False
    data = _api("/repos/%s/contents/%s" % (owner_repo,
                                            urllib.parse.quote(entry["path"])),
                raw=True, token=token)
    with open(os.path.join(dest_dir, entry["name"]), "wb") as f:
        f.write(data)
    return True


def _sanitized(name):
    return TRAILING_SAN.sub("", CHALL_NAME_SAN.sub("_", name)) or "chall"


def _pick_challenge(owner_repo, used, token):
    """Pick a random unseen challenge inside one repository.

    Returns (key, title, files) or None when the repo holds nothing fresh.
    key is the dedup identity ('owner/repo::relpath', or '::.' for a
    whole-repo challenge)."""
    root = _entries(owner_repo, "", token)
    dirs = [e for e in root if e.get("type") == "dir"]
    files = [e for e in root if e.get("type") == "file"]
    if dirs:
        fresh = [e for e in dirs
                 if "%s::%s" % (owner_repo, e["name"]) not in used]
        if not fresh:
            return None
        pick = random.choice(fresh)
        key = "%s::%s" % (owner_repo, pick["name"])
        inner = _entries(owner_repo, pick["path"], token)
        # category level? (root dir that only holds dirs -> descend once)
        inner_dirs = [e for e in inner if e.get("type") == "dir"]
        inner_files = [e for e in inner if e.get("type") == "file"]
        if inner_dirs and not inner_files:
            fresh_sub = [e for e in inner_dirs
                         if "%s::%s" % (owner_repo, e["name"]) not in used]
            if not fresh_sub:
                return None
            pick = random.choice(fresh_sub)
            key = "%s::%s" % (owner_repo, pick["name"])
        title = "%s-%s" % (owner_repo.split("/")[-1], pick["name"])
        return key, title, _walk_files(owner_repo, pick["path"], token)
    if files:
        key = "%s::." % owner_repo
        if key in used:
            return None
        return key, owner_repo.split("/")[-1], files
    return None


def _org_repos(org, cache_path):
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
        batch = _api("/orgs/%s/repos?per_page=100&page=%d" % (org, page))
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


def gh_pull(spec, dest, used, cache_dir):
    """Pull one unseen challenge for spec.

    Returns (key, chall_dir, nfiles, nskip) on success, or
    (None, reason, 0, 0) when the source is exhausted. Raises SourceError
    for hard failures (bad spec / API unreachable)."""
    os.makedirs(cache_dir, exist_ok=True)
    token = _token()
    if spec.startswith("gh-org:"):
        org = spec[len("gh-org:"):]
        if not org:
            raise SourceError("empty org in %r" % spec)
        repos = _org_repos(org, os.path.join(cache_dir, "org-%s.json" % org))
        if not repos:
            return None, "org %s has no year-named repos" % org, 0, 0
        random.shuffle(repos)
        last_err = "no fresh challenge"
        for repo in repos[:ORG_TRY_REPOS]:
            try:
                found = _pick_challenge("%s/%s" % (org, repo), used, token)
            except SourceError as e:
                last_err = str(e)
                continue                    # try the next repo
            if not found:
                continue
            key, title, entries = found
            d = os.path.join(dest, _sanitized(title))
            os.makedirs(d, exist_ok=True)
            n = sk = 0
            for e in entries:
                try:
                    n += 1 if _download("%s/%s" % (org, repo), e, d, token) else 0
                    if e.get("size", 0) > MAX_FILE_BYTES:
                        sk += 1
                except SourceError:
                    sk += 1
            if n == 0:
                try:
                    os.rmdir(d)
                except OSError:
                    pass
                last_err = "challenge %s had no downloadable files" % title
                continue
            return key, d, n, sk
        return None, last_err, 0, 0
    if spec.startswith("gh:"):
        owner_repo = spec[3:]
        if "/" not in owner_repo:
            raise SourceError("bad spec %r -- want gh:<owner>/<repo>" % spec)
        found = _pick_challenge(owner_repo, used, token)
        if not found:
            return None, "repo %s exhausted" % owner_repo, 0, 0
        key, title, entries = found
        d = os.path.join(dest, _sanitized(title))
        os.makedirs(d, exist_ok=True)
        n = sk = 0
        for e in entries:
            try:
                n += 1 if _download(owner_repo, e, d, token) else 0
                if e.get("size", 0) > MAX_FILE_BYTES:
                    sk += 1
            except SourceError:
                sk += 1
        if n == 0:
            try:
                os.rmdir(d)
            except OSError:
                pass
            return None, "repo %s: no downloadable files" % owner_repo, 0, 0
        return key, d, n, sk
    raise SourceError("unknown source spec: %r" % spec)
