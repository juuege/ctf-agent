"""Unified error taxonomy v2 for CTFPilot: exec.* + semantic.* layers.

v0.1 had four execution-level codes (timeout / nonzero-exit / empty-output /
reject), which answer "what did the process do" but never "why did the solve
fail". Grounded in the CyberArena failure audit (arXiv:2610.19005: 2,397
audited mistakes over 466 attack-defense matches -- the dominant failures
were semantic, e.g. fake-repair claims 23%, incomplete patches 21%, sleep
idling 14%, attacking stale targets 8%), the offline-solver mapping is:

  exec:timeout              process exceeded the time budget
  exec:timeout-sleep        timeout caused by a deliberate sleep (挂机)
  exec:nonzero-exit         process exited nonzero / crashed
  exec:empty-output         ran fine but printed nothing
  sandbox-reject            policy violation (kept from v0.1)
  planner-empty             planner had no action (kept from v0.1)
  semantic:raw-echo         flag echoed challenge files instead of being
                            derived (anti-cheat provenance gate)
  semantic:no-progress      identical observation already seen (repeat work,
                            the offline twin of CyberArena's stale targets)
  semantic:repeat-strategy  same action source rerun (output changed; the
                            strategy ladder is cycling)
  semantic:premature-giveup planner surrendered with >30% of the step budget
                            untouched
  semantic:giveup           planner surrendered, most budget spent
                            (informational, not premature)

Only the semantic classes make `stats` produce an actionable failure profile
instead of a list of process exit codes.
"""
import hashlib
import re

# Deliberate idling: `time.sleep(...)`, bare `sleep(digit...)`. Imported
# under an alias (e.g. `from time import sleep as wait`) escapes this -- the
# heuristic is best-effort, matching CyberArena's "long sleep" audit class.
_SLEEP_RE = re.compile(r"time\.sleep\s*\(|\bsleep\s*\(\s*[0-9]")


def classify_exec(result):
    """Execution-level class. Same values as v0.1 classify_error so the
    tried-list diagnosis hints in the deep loop keep working."""
    if result is None:
        return "no-result"
    if result.get("rc") == -1 and "TIMEOUT" in result.get("stderr", ""):
        return "timeout"
    if result.get("rc") != 0:
        return "nonzero-exit"
    if not result.get("stdout", "").strip():
        return "empty-output"
    return "ok"


def sleeps_in_code(code):
    """挂机 heuristic: does this action deliberately sleep?"""
    return bool(_SLEEP_RE.search(code or ""))


def fingerprint(text, n=512):
    """Stable fingerprint of an observation: whitespace-normalized head."""
    norm = re.sub(r"\s+", " ", (text or "").strip())[:n]
    return hashlib.md5(norm.encode("utf-8", "ignore")).hexdigest()


def action_key(action):
    """Stable identity of an action SOURCE: mock strategies carry a name;
    LLM actions are identified by their code/cmd fingerprint. Two steps run
    the same action source iff this key matches."""
    if not isinstance(action, dict):
        return None
    strat = action.get("_strategy")
    if strat:
        return "strategy:" + strat
    if action.get("action") == "python" and action.get("code"):
        return "code:" + fingerprint(action["code"], 4096)
    if action.get("action") == "shell" and action.get("cmd"):
        return "cmd:" + fingerprint(action["cmd"], 1024)
    return None


def giveup_class(step, max_steps):
    """Premature surrender = >30% of the step budget untouched."""
    if max_steps and step < max_steps * 0.7:
        return "semantic:premature-giveup"
    return "semantic:giveup"


def final_class(err_class, code=None, fp=None, seen_fps=None,
                action_key=None, seen_action_keys=None):
    """Pick the most informative single error class for the attempt record.

    Precedence (first match wins):
      1. semantic:no-progress    -- identical observation seen before: any
                                    rerun whose output fingerprint repeats,
                                    including repeated sleeps/crashes
      2. exec:timeout-sleep      -- deliberate idling that timed out
      3. exec:<hard failure>     -- timeout / nonzero-exit / empty-output /
                                    no-result
      4. semantic:repeat-strategy -- otherwise-ok rerun of the same action
                                    source with fresh output (ladder cycling)
      5. ""                      -- clean run with new output: not an error
    """
    if fp is not None and seen_fps is not None and fp in seen_fps:
        return "semantic:no-progress"
    if err_class != "ok":
        if err_class == "timeout" and sleeps_in_code(code):
            return "exec:timeout-sleep"
        return ("exec:" + err_class) if err_class else ""
    if (action_key is not None and seen_action_keys is not None
            and action_key in seen_action_keys):
        return "semantic:repeat-strategy"
    return ""
