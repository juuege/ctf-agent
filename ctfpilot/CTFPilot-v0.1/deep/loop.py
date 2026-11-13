"""Deep path: hypothesis -> action -> observation -> diagnosis -> retry.

Context segmentation (cf. RAISE 2026 "context segmentation" paper, which
solved 18.52% of tasks that plain agentic execution failed): the LLM never
sees raw full history; it sees a bounded context = challenge summary +
skill card + recalled lessons + a compact "already tried" list. The full raw
trace lives in SQLite, not in the prompt.
"""
from core.sandbox import SandboxViolation
from fastpath.detectors import Attempt
from fastpath import detectors as fp
from deep.error_tax import (classify_exec as classify_error, fingerprint,
                            action_key as _action_key, final_class, giveup_class)


def run_deep_loop(chall_name, chall_dir, routing, files, checker, sandbox,
                  llm, store, cid, cfg, log, brain="auto", benchmark=False):
    """Returns (flag or None, attempts)."""
    attempts = []
    budget = cfg.get("budget", {})
    max_steps = int(budget.get("max_deep_steps", 12))
    llm_budget = [int(budget.get("max_llm_calls_per_solve", 24))]
    qtokens = None
    try:
        from knowledge import tokenize
        qtokens = tokenize(routing.get("summary", "") + " " +
                           " ".join(f.get("path", "") for f in files))
    except ImportError:
        pass
    lessons = store.recall_lessons(routing["category"],
                                   int(cfg.get("memory", {}).get("recall_top_k", 3)),
                                   query_tokens=qtokens)

    # Strategy 0: rerun fast path (cheap, and records what was tried)
    flag, fast_attempts = fp.run_quick_pass(chall_dir, files, checker)
    for a in fast_attempts:
        attempts.append(a)
        store.log_attempt(cid, "deep-pre", a.detector, a.detail, a.success, a.flag)
    if flag:
        return flag, attempts

    from deep.planner import next_action as _na  # noqa: F401  (explicit path check)
    from deep import planner
    llm_be = llm.available_backend() if llm else None
    if brain == "auto":
        brain = "llm" if llm_be is not None else "mock"
    log("[deep] brain=%s (backend=%s)" % (brain, llm_be.get("name") if llm_be else "none"))

    tried = []
    fails_in_row = 0
    # Semantic-layer state (error taxonomy v2): observation fingerprints and
    # action-source keys already seen in THIS challenge, so repeat work /
    # ladder cycling / sleeping become visible in the failure profile.
    seen_fps = set()
    seen_action_keys = set()
    for step in range(max_steps):
        tried_summary = "\n".join("- %s" % t for t in tried[-12:]) or "(nothing)"
        # JIT re-retrieval (JitSkill zero-training approximation): refresh
        # lessons every 4 steps or after 2 consecutive failures, folding the
        # current hypotheses / excluded paths into the query signature.
        if step > 0 and qtokens is not None and (step % 4 == 0 or fails_in_row >= 2):
            try:
                from knowledge import tokenize as _tok
                lessons = store.recall_lessons(routing["category"],
                                               int(cfg.get("memory", {}).get("recall_top_k", 3)),
                                               query_tokens=qtokens | _tok(tried_summary))
                fails_in_row = 0
                log("[deep] step %d: lessons re-recalled with progress tokens" % step)
            except ImportError:
                pass
        context = planner.build_context(chall_name, routing, tried_summary,
                                        lessons, "flag{...} style")
        if benchmark:
            # Unattended loop (solve --benchmark): a human is never on the
            # other side. Planner asks ("user looks at the image") would hang
            # the run, so they degrade to scripted evidence or are skipped.
            context += ("\nUNATTENDED BENCHMARK MODE: no user is present. Do "
                        "not output any action that requires user "
                        "interaction. Instead of asking the user to look at "
                        "an image, produce scripted evidence (e.g. OCR-like "
                        "heuristics, pixel statistics); if that fails, skip "
                        "the step and continue.\n")
        action, err = planner.next_action(brain, routing["category"], routing,
                                          step, sandbox, files, chall_dir,
                                          context, llm, llm_budget)
        if action is None:
            log("[deep] step %d: planner gave no action (%s)" % (step, err))
            attempts.append(Attempt("planner", err, False))
            store.log_attempt(cid, "plan", "planner", err, False, error_class="planner-empty")
            break

        # Explicit giveup handling. v0.1 crash: a giveup action has no "cmd",
        # so the exec branch below did action["cmd"] -> KeyError -> the whole
        # solve crashed with a traceback. Now it exits cleanly with a
        # budget-aware class (premature vs honest surrender).
        if action.get("action") == "giveup":
            reason = (action.get("reason") or "").strip()[:300]
            ec = giveup_class(step, max_steps)
            log("[deep] step %d: giveup (%s): %s" % (step, ec, reason))
            attempts.append(Attempt("planner", "giveup: %s" % reason, False))
            store.log_attempt(cid, "plan", "planner", "giveup: %s" % reason,
                              False, error_class=ec, evidence=reason)
            break

        strategy = action.get("_strategy") or action.get("_brain", "llm") + "-action"
        log("[deep] step %d: %s" % (step, strategy))

        # Execute through the sandbox (schema-validated, offline)
        try:
            if action["action"] == "python":
                result = sandbox.run_python_code(action["code"])
            else:
                result = sandbox.run(action["cmd"])
        except SandboxViolation as sv:
            obs = {"stdout": "", "stderr": "SANDBOX_REJECT: %s" % sv.reason,
                   "rc": -2, "truncated": False}
            result = obs
            log("[deep] sandbox rejected: %s" % sv.reason)
            # Feed the rejection back: LLM must propose something else
            tried.append("REJECTED(%s): %s" % (strategy, sv.reason))
            attempts.append(Attempt("sandbox", sv.reason, False))
            store.log_attempt(cid, "exec", strategy, sv.reason, False,
                              error_class="sandbox-reject")
            fails_in_row += 1
            continue

        err_class = classify_error(result)
        out_text = result.get("stdout", "") + "\n" + result.get("stderr", "")
        found = checker.find(out_text)
        # Taxonomy v2: exec-level err_class drives the diagnosis hints;
        # the stored error_class is the final dual-layer classification
        # (register the fingerprints AFTER classifying so the first
        # occurrence of anything is never a "repeat").
        fp_obs = fingerprint(out_text)
        akey = _action_key(action)
        eclass = final_class(err_class,
                             code=action.get("code") or action.get("cmd"),
                             fp=fp_obs, seen_fps=seen_fps,
                             action_key=akey, seen_action_keys=seen_action_keys)
        if fp_obs:
            seen_fps.add(fp_obs)
        if akey:
            seen_action_keys.add(akey)
        attempts.append(Attempt(strategy,
                                 "rc=%s err=%s out=%d chars" %
                                 (result.get("rc"), err_class, len(out_text)),
                                 bool(found), found))
        store.log_attempt(cid, "exec", strategy,
                          "rc=%s err=%s" % (result.get("rc"), err_class),
                          bool(found), found, eclass,
                          evidence=out_text[:4000])

        if found:
            log("[deep] FLAG FOUND at step %d via %s" % (step, strategy))
            return found, attempts

        # Diagnosis note for next iteration
        head = out_text.strip().splitlines()
        head_txt = head[0][:160] if head else "(no output)"
        tried.append("%s -> %s | %s" % (strategy, err_class, head_txt))
        fails_in_row = fails_in_row + 1 if err_class != "ok" else 0
        if err_class == "timeout":
            brain_hint = " (previous step timed out; use a smaller/faster script)"
            tried[-1] += brain_hint

    return None, attempts
