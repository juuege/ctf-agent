"""Sandboxed command execution.

Design principle (from STRIATUM-CTF, IEEE CARS 2026): every tool call is
schema-validated BEFORE execution; out-of-schema or network-leaking commands
are rejected and the rejection itself is fed back to the planner as a
structured observation, which is what enables self-correction.

Default posture: OFFLINE. Network commands are blocked so that a "solve"
is always a clean solve (cf. "Every Model Cheats", 2026: 37.1% of Cybench
passes involved cheating, mostly via web search).
"""
import subprocess
import os


class SandboxViolation(Exception):
    def __init__(self, reason, cmd):
        super().__init__(reason)
        self.reason = reason
        self.cmd = cmd


class Sandbox:
    def __init__(self, cfg, workdir):
        sc = cfg.get("sandbox", {})
        self.offline = bool(sc.get("offline", True))
        self.blocked = [p.lower() for p in sc.get("blocked_command_patterns", [])]
        self.allowed = [a.lower() for a in sc.get("allowed_executables", ["python"])]
        self.timeout = int(sc.get("shell_timeout_s", 60))
        self.max_out = int(sc.get("max_output_chars", 20000))
        self.workdir = workdir

    def validate(self, cmd):
        """Return (ok, reason). Static validation, no execution."""
        low = cmd.lower().lstrip()
        if not low.strip():
            return False, "empty command"
        exe = os.path.basename(low.split()[0].strip('"')) if low.split() else ""
        # allow direct python script invocations of our own project
        exe_base = exe.replace(".exe", "")
        if exe_base not in self.allowed:
            return False, "executable not in whitelist: %s (allowed: %s)" % (exe, self.allowed)
        if self.offline:
            for pat in self.blocked:
                if pat in low:
                    return False, "offline mode: command matched blocked pattern '%s'" % pat
        return True, ""

    def run(self, cmd, timeout=None, extra_env=None):
        """Validate then run. Returns dict(stdout, stderr, rc, truncated)."""
        ok, reason = self.validate(cmd)
        if not ok:
            raise SandboxViolation(reason, cmd)
        timeout = timeout or self.timeout
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        if extra_env:
            env.update(extra_env)
        try:
            p = subprocess.run(cmd, shell=True, cwd=self.workdir,
                                capture_output=True, timeout=timeout, env=env)
            out = p.stdout.decode("utf-8", errors="replace")
            err = p.stderr.decode("utf-8", errors="replace")
            rc = p.returncode
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"").decode("utf-8", errors="replace")
            err = "TIMEOUT after %ss" % timeout
            rc = -1
        truncated = False
        if len(out) > self.max_out:
            out = out[: self.max_out]
            truncated = True
        return {"stdout": out, "stderr": err, "rc": rc, "truncated": truncated}

    def run_python_code(self, code, workfile="_ctfpilot_step.py"):
        """Write code to a temp file under workdir and run it with the
        project's interpreter. Keeps LLM-generated code isolated and auditable."""
        path = os.path.join(self.workdir, workfile)
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        py = os.environ.get("CTFPILOT_PYTHON", "python")
        return self.run('"%s" -X utf8 "%s"' % (py, workfile))
