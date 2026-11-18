"""Deep-path planner.

Two brains:
1. LLM brain (local Ollama / intranet gateway): reads a *segmented* context
   (challenge summary + category skill card + recalled lessons + what was
   already tried) and emits ONE next action as strict JSON:
   {"action": "python", "code": "..."} or {"action": "shell", "cmd": "..."}
   Invalid JSON or non-whitelisted action -> rejection observation fed back
   (schema-mediated grounding, the STRIATUM-CTF design principle).
2. Mock brain (offline fallback): a deterministic strategy ladder per
   category that mirrors the skill cards. Keeps the loop runnable and
   testable with zero LLM, which is also how the smoke tests run.
"""
import json
import os
import re

SKILL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")

ALLOWED_ACTIONS = ("python", "shell", "giveup")


def load_skill_card(category):
    path = os.path.join(SKILL_DIR, "%s.md" % category)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()[:4000]
    path = os.path.join(SKILL_DIR, "misc.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()[:4000]
    return "(no skill card)"


def load_learned_cards(category, top_k=3):
    """Relevance-aware selection: read ALL learned cards of the category,
    keep only the newest version per writeup name (created_at/version), then
    take the k most recent. Previously this took the first 3 files in
    alphabetical order, which let stale or unrelated cards crowd out useful
    ones as the library grew."""
    cards = []
    learned_dir = os.path.join(SKILL_DIR, "learned")
    if not os.path.isdir(learned_dir):
        return cards
    by_name = {}
    for name in os.listdir(learned_dir):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(learned_dir, name), "r", encoding="utf-8") as f:
                card = json.load(f)
        except (OSError, ValueError):
            continue
        if card.get("category") != category:
            continue
        nm = card.get("name") or name
        key = (float(card.get("created_at", 0) or 0), int(card.get("version", 1) or 1))
        prev = by_name.get(nm)
        if prev is None or key > prev[0]:
            by_name[nm] = (key, card)
    cards = [c for _k, c in by_name.values()]
    cards.sort(key=lambda c: -(float(c.get("created_at", 0) or 0)))
    return cards[:top_k]


def _short_tags(tags):
    """tags may be a JSON string (from SQLite lessons) or a dict (from cards)."""
    if isinstance(tags, str) and tags.strip():
        try:
            tags = json.loads(tags)
        except ValueError:
            return tags[:80]
    if isinstance(tags, dict):
        return "; ".join("%s=%s" % (k, str(v)[:40]) for k, v in tags.items() if v)
    return ""


def _case_line(c):
    """One case card as a compact, evidence-linked context line (Zep-style
    short card + pointer). unsolved cards are failure-sourced (WASP-Mem:
    self-judged failure traces add noise unless verified) and are marked so
    the reader treats them as pitfall/boundary references only."""
    tag = "solved" if c.get("solved") else "unsolved·失败来源"
    parts = ["- CASE %s [%s]" % (c.get("name", "?"), tag)]
    for label, key, lim in (("信号", "signals", 90), ("链", "chain", 110),
                            ("坑", "pitfalls", 80), ("迁移", "transferable", 80),
                            ("区别", "distinct", 70), ("排除", "excludes", 70)):
        v = str(c.get(key) or "").strip()
        if v:
            parts.append("%s: %s" % (label, v[:lim]))
    if c.get("failure"):
        parts.append("失败: %s" % str(c["failure"])[:80])
    if c.get("source"):
        parts.append("证据: %s" % c["source"])
    return " | ".join(parts)


def load_cases(category, query_tokens=None, top_k=3):
    """SOTA: case cards (skills/knowledge/cases.jsonl) ranked by signal-token
    overlap with the current challenge. Returns None when the knowledge file
    does not exist, so the caller can fall back to legacy learned cards."""
    path = os.path.join(SKILL_DIR, "knowledge", "cases.jsonl")
    if not os.path.exists(path):
        return None
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except ValueError:
                continue
            if c.get("category") == category:
                cases.append(c)
    if not cases:
        return []
    if query_tokens:
        try:
            from knowledge import rank_cases
            return rank_cases(cases, category, query_tokens, top_k)
        except ImportError:
            pass
    cases.sort(key=lambda c: -float(c.get("created") or 0))
    return cases[:top_k]


def build_context(chall_name, routing, tried_summary, lessons, checker_hint):
    skill = load_skill_card(routing["category"])
    qtokens = None
    try:
        from knowledge import tokenize
        qtokens = tokenize(routing.get("summary", "") + " " +
                           " ".join(f.get("path", "") for f in routing.get("files", [])))
    except ImportError:
        pass
    cases = load_cases(routing["category"], qtokens, 3)
    if cases is None:
        learned = load_learned_cards(routing["category"])
        learned_txt = "\n".join(
            "- technique: %s | when: %s%s%s" % (
                c.get("technique", "?"),
                c.get("when_to_use", "?"),
                (" | signals: %s" % str(c.get("signals", ""))[:100]) if c.get("signals") else "",
                (" | pitfalls: %s" % str(c.get("failure_paths", ""))[:100]) if c.get("failure_paths") else "")
            for c in learned) or "(none)"
    else:
        learned_txt = "\n".join(_case_line(c) for c in cases) or "(none)"
    lessons_txt = "\n".join(
        "- %s: %s%s%s" % (
            l["technique"], l["when_to_use"],
            (" | tags: %s" % _short_tags(l.get("tags"))) if l.get("tags") else "",
            (" | evidence: %s" % str(l.get("snippet", ""))[:140]) if l.get("snippet") else "")
        for l in lessons) or "(none)"
    return (
        "CHALLENGE: %s\nCATEGORY: %s (confidence %.2f)\n"
        "FLAG FORMAT HINT: %s\n\nFILES AND PREVIEWS:\n%s\n\n"
        "SKILL CARD (%s):\n%s\n\nCASE MEMORY (signal-ranked, evidence-linked; descent before trusting):\n%s\n\n"
        "PAST LESSONS (own trajectories):\n%s\n\n"
        "ALREADY TRIED (do not repeat):\n%s\n"
    ) % (chall_name, routing["category"], routing["confidence"], checker_hint,
          routing["summary"][:3000], routing["category"], skill, learned_txt,
          lessons_txt, tried_summary or "(nothing yet)")


SYSTEM_PROMPT = (
    "You are CTFPilot, an autonomous CTF solver working fully offline in a "
    "sandboxed Windows directory. You must reply with ONE action as strict JSON "
    "and nothing else:\n"
    '{"action": "python", "code": "<full python script>"}  '
    'or {"action": "shell", "cmd": "<one whitelisted command>"}  '
    'or {"action": "giveup", "reason": "..."}\n'
    "Rules: no network access (curl/wget/http are blocked); "
    "each script must print anything it finds; prefer small, verifiable steps; "
    "use z3-solver style math when a constraint problem appears; "
    "if a previous attempt failed, choose a DIFFERENT technique.\n"
    "CASE MEMORY descent rules (WikiTrace): a case card is compact -- before "
    "relying on it, open and read the writeup at its 证据 path when (a) the card "
    "lacks details you need, (b) the card is unsolved·失败来源 (use such cards as "
    "pitfall/boundary reference only, never as a proven path), (c) two cards "
    "conflict, or (d) any previous action failed."
)


def parse_action(text):
    """Extract the first JSON object with an allowed action. Strict validation."""
    if not text:
        return None, "empty response"
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None, "no JSON object in response"
    try:
        obj = json.loads(m.group(0))
    except ValueError as e:
        return None, "invalid JSON: %s" % e
    action = obj.get("action")
    if action not in ALLOWED_ACTIONS:
        return None, "action '%s' not in %s" % (action, ALLOWED_ACTIONS)
    if action == "python" and not obj.get("code", "").strip():
        return None, "python action requires non-empty code"
    if action == "shell" and not obj.get("cmd", "").strip():
        return None, "shell action requires non-empty cmd"
    return obj, ""


# ---------------- Mock brain: deterministic strategy ladder ----------------

def _peek_patterns():
    """[evolve-20261118-05 / rsi-124] Flag patterns for the unpack _peek
    harvest priority (config.json flag_patterns first, via core.config,
    stdlib defaults as the fallback so an isolated-root copy without
    config.json still prioritizes). CLI --flag-regex extras do not flow
    here (planner has no cfg handle); a custom-format flag still gets
    PRINTED in its glob-order turn -- priority is ordering only, never
    a filter -- so the exit-side checker faces no coverage loss."""
    try:
        from core.config import DEFAULT_FLAG_PATTERNS, load_config
        pats = list(load_config().get("flag_patterns", DEFAULT_FLAG_PATTERNS))
        if pats:
            return [str(p) for p in pats]
    except Exception:
        pass
    return [r"flag\{[^\s{}]{4,256}\}", r"FLAG\{[^\s{}]{4,256}\}"]


def _mock_strategy(category, routing, step_no, sandbox, files, chall_dir):
    """Return a python-code action implementing the next strategy from the
    ladder. Offline, deterministic, mirrors the skill cards."""
    d = chall_dir.replace("\\", "/")

    strategies = [
        ("read+strings all files",
         "import glob,os\n"
         "for p in glob.glob('" + d + "/**/*', recursive=True):\n"
         "    if os.path.isfile(p) and '_ctfpilot' not in p:\n"
         "        try:\n"
         "            d=open(p,'rb').read()\n"
         "            print('==',p,len(d)); print(d[:1500].decode('utf-8','replace'))\n"
         "        except Exception as e: print('ERR',p,e)\n"),
        ("printable strings scan",
         "import re,glob,os\n"
         "pat=re.compile(rb'[\\x20-\\x7e]{6,}')\n"
         "for p in glob.glob('" + d + "/**/*', recursive=True):\n"
         # [evolve-20261117-46 / rsi-118] _ctfpilot filter closes the
         # last recon read gap: this strategy globs the whole dir, so
         # without the filter it read the account-model sidecar
         # _ctfpilot_model_result.json verbatim and 'solved' from the
         # model channel's answer bytes (dfjk-Misc fake-SOLVED shape,
         # previously caught only by the exit-side raw-echo guard);
         # read+strings above already carried the filter.
         "    if os.path.isfile(p) and '_ctfpilot' not in p:\n"
         "        for s in pat.findall(open(p,'rb').read())[:80]: print(p, s[:200])\n"),
        ("file-stat + entropy heuristic",
         "import glob,os,math,collections\n"
         "for p in glob.glob('" + d + "/**/*', recursive=True):\n"
         "    if os.path.isfile(p):\n"
         "        d=open(p,'rb').read()\n"
         "        if not d: continue\n"
         "        c=collections.Counter(d); H=-sum(v/len(d)*math.log2(v/len(d)) for v in c.values())\n"
         "        print(p,'bytes',len(d),'entropy','%.2f'%H)\n"),
        ("unpack archives (zip/tar/rar, incl. token passwords)",
           "import zipfile,tarfile,glob,os,itertools,re,hashlib\n"
           "_pks=[]\n"
           "words=['flag','ctf','password','admin','123456','0000']\n"
            # [evolve-20261118-12 / rsi-132] wrap-round extraction
            # idempotence. RED (entry-78/79/80/81 next_candidate, measured
            # live on the n1cat pair + the flip fixtures): the mock ladder
            # wraps modulo its strategy list, and EVERY wrap round's unpack
            # step re-runs extractall on carriers it already extracted in
            # an earlier round -- the members are re-written with FRESH
            # mtimes, so (a) the wrap guard's _dir_signature
            # (relpath,size,mtime) never repeats on a carrier tree and the
            # intended no-new-files halt never fires (live n1cat honest 38
            # attempts, DeadGame 196, all re-extract cycling), and (b) each
            # round re-pays extractall plus the Windows Defender first-open
            # scan on every member (n1cat round-2: 2.9s extract + ~9s AV
            # re-read; flip fixtures 29.35/30.44s inc vs 17.68/18.11s cand,
            # attempts 11=11, flags identical). Fix: after a SUCCESSFUL
            # extractall the step writes a per-carrier marker
            # <out>/.__x_done__<md5(abs path)[:12]> holding "size,mtime_ns";
            # on re-encounter with an unchanged fingerprint the extraction
            # is SKIPPED with an honest
            # 'ZIP/TAR/RAR SKIP (extracted before, unchanged)' line and the
            # out-dir is still registered in _pks, so the deferred _peek
            # census is byte-identical (dotfile markers are invisible to
            # every glob face -- census, recon, carrier walk -- while
            # extracted members keep their round-1 mtimes, letting the wrap
            # guard halt honestly once extraction saturates: round-3 step-0
            # sees an unchanged signature instead of cycling to max_steps).
            # Honesty rails: markers are written only after extractall
            # returns (an interrupted or password-failed extraction writes
            # none, so a later round with a bigger token set retries
            # honestly); a mutated carrier (size/mtime_ns changed) fails
            # the fingerprint and re-extracts, and a re-extracted parent
            # legitimately cascades (it rewrites nested child carriers,
            # whose stale markers then fail the same guard); the skip is
            # never a filter -- re-writing byte-identical members loses
            # zero coverage.
            "def _mk(out,p):\n"
            "    return os.path.join(out,'.__x_done__'+hashlib.md5(os.path.abspath(p).encode('utf-8','replace')).hexdigest()[:12])\n"
            "def _done(out,p):\n"
            "    try:\n"
            "        st=os.stat(p); m=_mk(out,p)\n"
            "        return os.path.isfile(m) and open(m,encoding='utf-8',errors='replace').read()=='%d,%d'%(st.st_size,st.st_mtime_ns)\n"
            "    except Exception: return False\n"
            "def _mark(out,p):\n"
            "    try:\n"
            "        st=os.stat(p)\n"
            "        open(_mk(out,p),'w',encoding='utf-8').write('%d,%d'%(st.st_size,st.st_mtime_ns))\n"
            "    except Exception: pass\n"
         # [evolve-20261118-05 / rsi-124] harvest window priority.
         # RED (isolated inc root, this round): a 25-member tar.gz/zip
         # whose ONLY flag member sorts LAST (z25.txt) stays NOT SOLVED
         # on the incumbent -- _peek interleaves 'EXTRACTED <path> <len>'
         # with 4000B of content per member in glob order, and the
         # sandbox truncates each step's stdout at 20000 chars, so
         # ~9 members of 2KB filler push the flag member's content past
         # the window on the unpack face; the wrap-round read+strings
         # face burns its own 20000 chars on the first ~10 members the
         # same way (1500B head + strings per member). A many-member
         # archive therefore hides a late flag from EVERY face.
         # Fix: two-pass _peek -- cheap census lines first (every member
         # name + size enters the observation stream), then content in
         # 'flag-pattern hit members first' order. Priority is ordering
         # only, never a filter: no-hit ordering preserves the glob
         # order exactly (zero regression face), and the config-driven
         # patterns come from core.config (CLI --flag-regex extras stay
         # outside -- see _peek_patterns).
           # [evolve-20261118-08 / rsi-128] XXL-archive census windowing.
           # RED (live, feed round-95 n1ctf-2025-n1cat): 6.4MB WAR -> 3512
           # members, first unpack step out=20001 chars -- the pass-1
           # census alone is ~140KB, so on XXL trees the census burns the
           # whole shared 20000-char step window before the pass-2
           # flag-hit content gets one line out; the isolated XXL fixture
           # w1800-xxl (1801 members, flag member sorts last) reproduces
           # the same shape: incumbent unpack out=20001 chars, 41.9s/16
           # attempts NOT SOLVED. Two-pass _peek (entry-76) fixes mid-
           # size archives but its census is unbounded.
           # Fix: census lines leave the read loop, order flag-hit
           # members first, cap at 80 lines + one summary line (80 x
           # ~150-char absolute member paths + 4000B hit content head =
           # ~16KB worst case, safely under the 20000-char step window;
           # cap=120 measurably overflowed on the evolve53 selftest
           # scratch path).
           # Ordering only, never a filter: every member still gets its
           # content printed in turn; <=80-member trees emit exactly the
           # same lines (hit lines may only move UP the census), so the
           # entry-76 faces (w50 targz/zip, benign) keep their flips.
           #
           # [evolve-20261118-09 / rsi-129] deferred _peek: one peek per
           # out-dir, at the END of the loop, over the FULL tree.
           # RED (live, same n1cat war, measured in .temp/rsi-129-xxl-io):
           # the round-2 unpack walk matches 9 nested zip carriers
           # (8 WEB-INF/lib jars + the war again) that ALL extractall into
           # TWO shared out-dirs (challenge/_extracted and
           # _extracted/WEB-INF/lib/_extracted), and the incumbent peeks
           # out-dir state IMMEDIATELY after each carrier -- so the same
           # growing tree is globbed+read+decoded+regexed once per carrier:
           # 31512 file-visits (46.6s, Windows Defender first-open scan on
           # freshly extracted members included) vs ~7013 for one visit
           # per out-dir; total round-2 unpack = 59.5s against the 60s
           # sandbox budget -> rc=-1, the whole step's observation is lost
           # on every wrap round (and the jar sources stay in the tree, so
           # every subsequent round re-pays it). The candidate registers
           # each out-dir once in _pks (carrier order) and peeks each
           # exactly once AFTER the loop, i.e. at the tree's FULL state.
           # Coverage: every incumbent peek(out) saw a strict PREFIX of
           # the final tree, and the deferred peek emits exactly the
           # final cumulative census+pass2 -- so every line the incumbent
           # ever printed is still printed (once, deduped), no member
           # loses its census line or content turn; the 20000-char step
           # window now spends its budget on unique hit-first information
           # instead of 9 repeats of the same prefix. Extraction itself is
           # unchanged (extractall still runs per carrier, same members
           # on disk, same passwords tried, same tar-slip guard).
           "FLRX=" + repr(_peek_patterns()) + "\n"
           "_rx=[re.compile(x) for x in FLRX]\n"
           "def _peek(outdir):\n"
           "    fs=[]\n"
           "    for rf in glob.glob(outdir+'/**/*', recursive=True):\n"
           "        if os.path.isfile(rf): fs.append(rf)\n"
           "    hit=[]; rest=[]\n"
           "    for rf in fs:\n"
           "        dd=open(rf,'rb').read()\n"
           "        txt=dd[:4000].decode('utf-8','replace')\n"
           "        det=dd[:65536].decode('utf-8','replace')\n"
           "        (hit if any(r.search(det) for r in _rx) else rest).append((rf,len(dd),txt))\n"
           "    cen=hit+rest\n"
           "    for rf,ln,tx in cen[:80]: print('EXTRACTED', rf, ln)\n"
           "    if len(cen)>80: print('... CENSUS-TRUNCATED', len(cen)-80, 'more members (flag-hit listed first)')\n"
           "    for rf,ln,tx in cen: print(tx[:4000])\n"
         "for p in glob.glob('" + d + "/**/*', recursive=True):\n"
         "    if not os.path.isfile(p): continue\n"
         "    out=os.path.dirname(p)+'/_extracted'\n"
          "    try:\n"
          "        if zipfile.is_zipfile(p) and _done(out,p):\n"
          "            print('ZIP SKIP',p,'(extracted before, unchanged)')\n"
          "            if out not in _pks: _pks.append(out)\n"
          "        elif zipfile.is_zipfile(p):\n"
          "            z=zipfile.ZipFile(p)\n"
         "            toks=set()\n"
         "            for q in glob.glob(os.path.dirname(p)+'/*.txt'):\n"
         "                toks|={w.strip() for w in open(q,encoding='utf-8',errors='replace').read().split()}\n"
         "            ok=False\n"
         "            for w in itertools.chain(words,sorted(toks)):\n"
         "                try:\n"
         "                    z.extractall(out,pwd=w.encode()); ok=True; print('ZIP OK',p,'pwd=',w); break\n"
         "                except RuntimeError: continue\n"
         "                except Exception: break\n"
             "            if ok: _mark(out,p)\n"
             "            if ok and out not in _pks: _pks.append(out)\n"
          "        elif open(p,'rb').read(4)==b'Rar!' or p.lower().endswith('.rar'):\n"
        "            import shutil,subprocess\n"
        "            _tar=shutil.which('tar')\n"
         "            if not _tar: print('RAR SKIP (no bsdtar)',p)\n"
         "            elif _done(out,p):\n"
         "                print('RAR SKIP',p,'(extracted before, unchanged)')\n"
         "                if out not in _pks: _pks.append(out)\n"
         "            else:\n"
         "                os.makedirs(out,exist_ok=True)\n"
        "                try:\n"
        "                    _r=subprocess.run([_tar,'-xf',p,'-C',out],capture_output=True,timeout=30)\n"
          "                    if _r.returncode==0:\n"
          "                        print('RAR OK',p); _mark(out,p)\n"
          "                        if out not in _pks: _pks.append(out)\n"
         "                    else: print('RAR FAIL',p,_r.stderr.decode('utf-8','replace')[:200])\n"
        "                except Exception as _e: print('RAR ERR',p,_e)\n"
        # [evolve-20261118-03 / rsi-122] tar-family recognition + tar-slip
        # guard. RED (live, this feed round): n1ctf-2025-bridge 214KB
        # bridge.tar.gz (21 members: Anchor Rust source, server flag
        # service, challenge.so ELF) was never opened -- this arm tested
        # p.endswith('.tar') only, so .tar.gz/.tgz/.tar.bz2/.tar.xz
        # carriers were silently skipped (unpack step out=19 chars, the
        # wrap guard then halted the ladder on the unchanged file set)
        # and members needing real extraction to expose their payload
        # (nested zips, ELF strings) stayed invisible to every face.
        # tarfile.is_tarfile recognizes the whole compressed-tarball
        # family transparently ('r:*' autodetect); a plain non-tar .gz
        # (e.g. a gzipped text handout) stays False and on the fastpath
        # decompress+chain face where it belongs. filter='data' (Py3.12)
        # closes the tar-slip path-traversal hole that the old bare
        # extractall already had on the plain .tar arm: ../ members and
        # absolute paths now raise, the per-file except prints an
        # honest 'unpack err' and nothing escapes the outdir.
          "        elif tarfile.is_tarfile(p):\n"
           "            if _done(out,p):\n"
           "                print('TAR SKIP',p,'(extracted before, unchanged)')\n"
           "                if out not in _pks: _pks.append(out)\n"
           "            else:\n"
           "                tarfile.open(p).extractall(out, filter='data'); print('TAR OK',p)\n"
           "                _mark(out,p)\n"
           "                if out not in _pks: _pks.append(out)\n"
          "    except Exception as e: print('unpack err',p,e)\n"
          "for _o in _pks: _peek(_o)\n"
         "print('unpack pass done')\n"),
        ("byte-frequency / xor-report for crypto blobs",
         "import glob,os,collections\n"
         "for p in glob.glob('" + d + "/**/*', recursive=True):\n"
         "    if os.path.isfile(p):\n"
         "        d=open(p,'rb').read()\n"
         "        if len(d)>32:\n"
         "            c=collections.Counter(d).most_common(5)\n"
         "            print(p,'top bytes',[(hex(b),n) for b,n in c])\n"),
    ]
    if step_no >= len(strategies):
        # Wrap around: after an extraction-type strategy, re-run recon
        # strategies so newly produced files get scanned (bounded by max_steps).
        step_no = step_no % len(strategies)
    name, code = strategies[step_no]
    return {"action": "python", "code": code, "_strategy": name}, ""


def next_action(brain, category, routing, step_no, sandbox, files, chall_dir,
                context, llm, llm_budget):
    """Get the next action from the active brain, with schema validation."""
    if brain == "llm" and llm is not None and llm_budget[0] > 0:
        res = llm.chat(SYSTEM_PROMPT, context, max_tokens=2048, temperature=0.2)
        if res.ok:
            llm_budget[0] -= 1
            obj, err = parse_action(res.text)
            if obj is not None:
                return obj, ""
            return None, "LLM response rejected by schema: %s" % err
        # fall through to mock on backend failure
        llm_budget[0] -= 1
    obj, err = _mock_strategy(category, routing, step_no, sandbox, files, chall_dir)
    if obj is None:
        return None, err
    obj["_brain"] = "mock"
    return obj, ""
