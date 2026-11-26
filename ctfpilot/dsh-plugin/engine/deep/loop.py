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
import os as _os


def _dir_signature(chall_dir):
    """File-set signature for the mock wrap-around guard: (relpath, size,
    mtime) of every real challenge file, sorted. Engine-owned bookkeeping
    (_ctfpilot* scratch files, __pycache__, .temp) is excluded so solve
    bookkeeping writes cannot defeat the guard; unpack products
    (_extracted/...) ARE included -- newly produced inputs legitimately
    justify a re-sweep round.

    Evidence for the guard (2026-11-16 feed round, 2022-xhlj-web-real_ez_node
    127.1s / 12 deep steps, steps 5-11 repeated steps 0-6 verbatim):
    trajectory store shows 0/187,906 successes for repeated strategy runs
    ("same strategy failed earlier then succeeded" count: 0). Wrap-around
    only pays when the unpack strategy produced new files; the planner
    comment says so but never checked. The guard encodes that missing
    check.
    """
    sig = []
    for root, dirs, fs in _os.walk(chall_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".temp")]
        for fn in fs:
            if fn.startswith("_ctfpilot"):
                continue
            # [evolve-20261117-11] wrapper redirection (.solve.log/.solve.err)
            # grows DURING solve; keeping it in the signature makes the
            # signature never repeat, silently defeating this wrap-around
            # guard (live: sizes/mtimes change every stat on every round).
            if fn in (".solve.log", ".solve.err"):
                continue
            p = _os.path.join(root, fn)
            try:
                st = _os.stat(p)
            except OSError:
                continue
            sig.append((_os.path.relpath(p, chall_dir).replace("\\", "/"),
                        st.st_size, int(st.st_mtime)))
    sig.sort()
    return tuple(sig)


def run_deep_loop(chall_name, chall_dir, routing, files, checker, sandbox,
                  llm, store, cid, cfg, log, brain="auto", benchmark=False,
                  fast_done=False, reject_flag=None):
    """Returns (flag or None, attempts).

    fast_done: True when this solve already ran run_fast_path on the very
    same chall_dir + files list (do_solve mode=auto). The deep Strategy 0
    quick pass then SKIPS its rerun: deterministic detectors on identical
    inputs return identical results, so the rerun only duplicated work and
    db rows (measured 2026-11-16 feed round on 2022-xhlj-web-real_ez_node:
    quick pass cost 42.3s exec + 18.3s row inserts per unsolved challenge,
    with 28/28 historical deep-pre successes all explained by same-solve
    fast-phase successes). mode=deep keeps the quick pass as its only
    fastpath carrier.
    """
    attempts = []
    # Placeholder/raw-echo candidates already refused by the provenance
    # callback. Kept per solve so a repeat hit in a later step (same string
    # printed again by a different recon strategy) does NOT end the loop.
    rejected = set()
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
    # Recall-effectiveness accounting (口径修正 2026-11-16): keep the ids of
    # every lesson that entered the context, so a SOLVE credits them with
    # success_count. Without this, case cards ('case:<name>' technique) never
    # matched bump_success(category, detector) and success_count stayed 0.
    recalled_ids = set()

    def _track(ls):
        recalled_ids.update(l.get("id") for l in ls if l.get("id"))

    _track(lessons)

    # Strategy 0: rerun fast path -- but only when this solve has not run
    # the fastpath yet (mode=deep). In mode=auto do_solve already ran the
    # identical detector chain on identical inputs; a rerun is pure
    # duplicate work and duplicate db rows (single-variable change,
    # 2026-11-16 feed round).
    if not fast_done:
        flag, fast_attempts = fp.run_quick_pass(chall_dir, files, checker)
        for a in fast_attempts:
            attempts.append(a)
            store.log_attempt(cid, "deep-pre", a.detector, a.detail, a.success, a.flag)
        if flag:
            if reject_flag is not None and reject_flag(flag):
                # mode=deep has no do_solve fast phase ahead of this loop,
                # so the quick pass is the first raw-echo face. Refuse here
                # and keep going instead of returning a placeholder flag.
                rejected.add(flag)
                for a in fast_attempts:
                    if a.flag == flag:
                        a.success = False
                        a.flag = None
                log("[deep] quick-pass candidate %s refused (raw-echo); "
                    "continuing the hypothesis loop" % flag)
            else:
                store.bump_success_by_ids(recalled_ids)
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
    # Mock wrap-around guard state: action_key -> dir signature at its last
    # run. Mock brain cycles a fixed strategy ladder regardless of progress;
    # when the ladder wraps onto an already-run strategy AND the challenge
    # dir is unchanged, every remaining step of this round would repeat
    # identical inputs on deterministic detectors. LLM brains are exempt
    # (their actions are not a fixed ladder and the tried-list feeds back).
    strategy_sig = {} if brain == "mock" else None
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
                _track(lessons)
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

        # Mock wrap-around guard (single-variable change, 2026-11-16):
        # skip-and-stop when the mock ladder wraps onto a strategy already
        # run against an unchanged file set. This is the missing check the
        # planner comment promised ("re-run recon so newly produced files
        # get scanned") -- it fires only when NO new files were produced.
        if strategy_sig is not None:
            guard_key = _action_key(action)
            if guard_key:
                cur_sig = _dir_signature(chall_dir)
                if guard_key in strategy_sig and strategy_sig[guard_key] == cur_sig:
                    note = ("%s already ran on an unchanged file set; "
                            "mock wrap-around would only repeat deterministic "
                            "detectors on identical inputs" % strategy)
                    log("[deep] step %d: wrap guard: halting mock wrap-around" % step)
                    attempts.append(Attempt("deep-guard", note, False))
                    store.log_attempt(cid, "exec", "deep-guard", note, False,
                                      error_class="semantic:no-new-files")
                    break
                strategy_sig[guard_key] = cur_sig

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
            if found in rejected:
                # The same refused string resurfaced in a later step's output
                # (recon strategies overlap). Treat as not-found, never as a
                # reason to stop: the loop still owns its remaining budget.
                # The attempt row was logged as success just above, so undo
                # both the in-memory credit and the stored row -- otherwise a
                # placeholder would leave success=1 rows behind while the
                # challenge itself stays NOT SOLVED.
                attempts[-1].success = False
                attempts[-1].flag = None
                store.retract_attempts_by_flag(cid, found)
                tried.append("%s -> refused candidate repeated (%s); "
                             "continuing" % (strategy, found))
                fails_in_row += 1
                continue
            if reject_flag is not None and reject_flag(found):
                # Provenance spoke: this candidate is a raw echo (e.g. an
                # env placeholder such as FLAG in docker-compose). The
                # callback already logged + retracted it in the store, so
                # only the in-memory attempt and the loop state remain.
                attempts[-1].success = False
                attempts[-1].flag = None
                rejected.add(found)
                tried.append("%s -> refused candidate %s (raw-echo); "
                             "continuing" % (strategy, found))
                fails_in_row += 1
                continue
            log("[deep] FLAG FOUND at step %d via %s" % (step, strategy))
            store.bump_success_by_ids(recalled_ids)
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
