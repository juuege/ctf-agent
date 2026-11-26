"""Standalone taxonomy tests -- run with the project python:

    python -X utf8 tests/test_error_tax.py

Covers: classify_exec values (v0.1 compat), sleep heuristic, fingerprint
normalization, giveup budget split, final_class precedence, action_key
stability. Exits nonzero on the first failure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deep.error_tax import (classify_exec, sleeps_in_code, fingerprint,
                            action_key, giveup_class, final_class)

FAILURES = []


def check(name, cond):
    if cond:
        print("  ok  %s" % name)
    else:
        print("  FAIL %s" % name)
        FAILURES.append(name)


# ---- classify_exec: same values as v0.1 classify_error (compat) ----
check("timeout detected",
      classify_exec({"rc": -1, "stderr": "TIMEOUT after 60s", "stdout": ""}) == "timeout")
check("nonzero-exit detected",
      classify_exec({"rc": 1, "stderr": "boom", "stdout": "x"}) == "nonzero-exit")
check("empty-output detected",
      classify_exec({"rc": 0, "stderr": "", "stdout": "   \n "}) == "empty-output")
check("ok detected", classify_exec({"rc": 0, "stdout": "data"}) == "ok")
check("no-result on None", classify_exec(None) == "no-result")

# ---- sleep heuristic ----
check("time.sleep caught", sleeps_in_code("import time\ntime.sleep(30)"))
check("bare sleep(5) caught", sleeps_in_code("for i in range(3):\n    sleep(5)"))
check("no false positive on prose", not sleeps_in_code("print('asleep(zzz) of bees')"))
check("empty code safe", not sleeps_in_code(None))

# ---- fingerprint ----
check("whitespace normalized",
      fingerprint("a  b\n\t c") == fingerprint("a b c"))
check("different text differs", fingerprint("abc") != fingerprint("abd"))
check("long text truncated head", fingerprint("x" * 900) == fingerprint("x" * 2000))

# ---- giveup budget split ----
check("early giveup is premature", giveup_class(2, 12) == "semantic:premature-giveup")
check("late giveup is honest", giveup_class(11, 12) == "semantic:giveup")
check("boundary 70% is honest", giveup_class(9, 12) == "semantic:giveup")
check("zero budget safe", giveup_class(0, 0) == "semantic:giveup")

# ---- final_class precedence ----
S = set(["x"])
check("identical output -> no-progress (beats everything)",
      final_class("ok", fp="x", seen_fps=S) == "semantic:no-progress")
check("repeated crash output -> no-progress",
      final_class("timeout", fp="t", seen_fps=set(["t"])) == "semantic:no-progress")
check("sleep timeout first time -> exec:timeout-sleep",
      final_class("timeout", code="time.sleep(30)", fp="a", seen_fps=S)
      == "exec:timeout-sleep")
check("plain timeout -> exec:timeout",
      final_class("timeout", code="print(1)", fp="b", seen_fps=S) == "exec:timeout")
check("nonzero -> exec:nonzero-exit",
      final_class("nonzero-exit") == "exec:nonzero-exit")
check("empty-output -> exec:empty-output",
      final_class("empty-output") == "exec:empty-output")
check("ok fresh run -> not an error", final_class("ok") == "")
check("same strategy rerun with new output -> repeat-strategy",
      final_class("ok", action_key="k", seen_action_keys=set(["k"]))
      == "semantic:repeat-strategy")
check("fresh strategy fresh output -> empty",
      final_class("ok", action_key="k", seen_action_keys=set()) == "")
check("no-result -> exec:no-result", final_class("no-result") == "exec:no-result")

# ---- action_key ----
check("mock named strategy stable",
      action_key({"_strategy": "recon"}) == "strategy:recon")
k1 = action_key({"action": "python", "code": "print(1)"})
k2 = action_key({"action": "python", "code": "print(1)"})
check("llm code fingerprint stable", k1 == k2 and k1.startswith("code:"))
check("different code differs",
      action_key({"action": "python", "code": "print(2)"}) != k1)
check("shell keyed by cmd",
      action_key({"action": "shell", "cmd": "python x.py"}).startswith("cmd:"))
check("giveup/unknown -> None", action_key({"action": "giveup"}) is None)

print("")
if FAILURES:
    print("test_error_tax: %d FAILURE(S)" % len(FAILURES))
    sys.exit(1)
print("test_error_tax: all checks passed")
