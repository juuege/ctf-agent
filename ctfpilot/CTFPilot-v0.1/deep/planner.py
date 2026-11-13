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
         "    if os.path.isfile(p):\n"
         "        for s in pat.findall(open(p,'rb').read())[:80]: print(p, s[:200])\n"),
        ("file-stat + entropy heuristic",
         "import glob,os,math,collections\n"
         "for p in glob.glob('" + d + "/**/*', recursive=True):\n"
         "    if os.path.isfile(p):\n"
         "        d=open(p,'rb').read()\n"
         "        if not d: continue\n"
         "        c=collections.Counter(d); H=-sum(v/len(d)*math.log2(v/len(d)) for v in c.values())\n"
         "        print(p,'bytes',len(d),'entropy','%.2f'%H)\n"),
        ("unpack archives (zip/tar, incl. token passwords)",
         "import zipfile,tarfile,glob,os,itertools\n"
         "words=['flag','ctf','password','admin','123456','0000']\n"
         "def _peek(outdir):\n"
         "    for rf in glob.glob(outdir+'/**/*', recursive=True):\n"
         "        if os.path.isfile(rf):\n"
         "            dd=open(rf,'rb').read()\n"
         "            print('EXTRACTED', rf, len(dd))\n"
         "            print(dd[:4000].decode('utf-8','replace'))\n"
         "for p in glob.glob('" + d + "/**/*', recursive=True):\n"
         "    if not os.path.isfile(p): continue\n"
         "    out=os.path.dirname(p)+'/_extracted'\n"
         "    try:\n"
         "        if zipfile.is_zipfile(p):\n"
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
         "            if ok: _peek(out)\n"
         "        elif p.endswith('.tar'):\n"
         "            tarfile.open(p).extractall(out); print('TAR OK',p); _peek(out)\n"
         "    except Exception as e: print('unpack err',p,e)\n"
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
