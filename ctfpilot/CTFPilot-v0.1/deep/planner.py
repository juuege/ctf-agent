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
    cards = []
    learned_dir = os.path.join(SKILL_DIR, "learned")
    if not os.path.isdir(learned_dir):
        return cards
    for name in sorted(os.listdir(learned_dir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(learned_dir, name), "r", encoding="utf-8") as f:
                card = json.load(f)
            if card.get("category", category) == category:
                cards.append(card)
        except (OSError, ValueError):
            continue
        if len(cards) >= top_k:
            break
    return cards


def build_context(chall_name, routing, tried_summary, lessons, checker_hint):
    skill = load_skill_card(routing["category"])
    learned = load_learned_cards(routing["category"])
    learned_txt = "\n".join(
        "- technique: %s | when: %s" % (c.get("technique", "?"), c.get("when_to_use", "?"))
        for c in learned) or "(none)"
    lessons_txt = "\n".join(
        "- %s: %s" % (l["technique"], l["when_to_use"]) for l in lessons) or "(none)"
    return (
        "CHALLENGE: %s\nCATEGORY: %s (confidence %.2f)\n"
        "FLAG FORMAT HINT: %s\n\nFILES AND PREVIEWS:\n%s\n\n"
        "SKILL CARD (%s):\n%s\n\n"
        "LEARNED FROM WRITEUPS:\n%s\n\n"
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
    "if a previous attempt failed, choose a DIFFERENT technique."
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
         "for p in glob.glob('" + d + "/**/*', recursive=True):\n"
         "    if not os.path.isfile(p): continue\n"
         "    out=os.path.dirname(p)+'/_extracted'\n"
         "    try:\n"
         "        if zipfile.is_zipfile(p):\n"
         "            z=zipfile.ZipFile(p)\n"
         "            toks=set()\n"
         "            for q in glob.glob(os.path.dirname(p)+'/*.txt'):\n"
         "                toks|={w.strip() for w in open(q,encoding='utf-8',errors='replace').read().split()}\n"
         "            for w in itertools.chain(words,sorted(toks)):\n"
         "                try:\n"
         "                    z.extractall(out,pwd=w.encode()); print('ZIP OK',p,'pwd=',w); break\n"
         "                except RuntimeError: continue\n"
         "                except Exception: break\n"
         "        elif p.endswith('.tar'):\n"
         "            tarfile.open(p).extractall(out); print('TAR OK',p)\n"
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
