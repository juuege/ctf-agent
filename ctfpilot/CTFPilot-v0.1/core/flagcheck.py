"""Flag detection and validation.

Strict-pattern matching only: a flag counts only when it matches a known
CTF flag format. This avoids false positives from random base64 strings.
Anti-cheat note: flags must come from local execution output; anything that
would require fetching a public writeup online is out of scope by default.

Provenance (fix for the 06_zip_nested lesson): a flag that appears VERBATIM
in the original challenge files is a raw echo (challenge-provided flag), not
a derived solve. `flag_in_raw` implements that check; do_solve gates on it via
config `flagcheck.require_derived` (default true).
"""
import os
import re


class FlagChecker:
    def __init__(self, patterns, extra_regex=None):
        self.patterns = list(patterns)
        if extra_regex:
            try:
                self.patterns = [re.compile(extra_regex)] + self.patterns
            except re.error:
                pass

    def find(self, text):
        """Return the first flag found in text, else None."""
        if not text:
            return None
        for pat in self.patterns:
            m = pat.search(text)
            if m:
                return m.group(0)
        return None

    def find_all(self, text):
        found = []
        if not text:
            return found
        for pat in self.patterns:
            for m in pat.finditer(text):
                cand = m.group(0)
                if cand not in found:
                    found.append(cand)
        return found


def flag_in_raw(chall_dir, flag, max_file=8 * 1024 * 1024,
               max_total=64 * 1024 * 1024):
    """Provenance check: does `flag` appear byte-for-byte in the ORIGINAL
    challenge files? If yes, the "solve" only echoed a challenge-provided
    flag (raw-echo) instead of deriving it. Engine-generated side effects
    (_extracted/, .temp/, _ctfpilot* files) are excluded so a legitimate
    extract-then-read solve is not penalized.

    Returns (hit_bool, rel_path_of_first_hit_or_None).
    """
    if not flag:
        return False, None
    needle = flag.encode("utf-8", "replace")
    skip_dirs = {"_extracted", ".temp", "__pycache__"}
    total = 0
    for root, dirs, names in os.walk(chall_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in names:
            if name.startswith("_ctfpilot"):
                continue
            p = os.path.join(root, name)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if size > max_file or total >= max_total:
                continue
            try:
                with open(p, "rb") as fh:
                    data = fh.read(max_file)
            except OSError:
                continue
            total += len(data)
            if needle in data:
                return True, os.path.relpath(p, chall_dir)
    return False, None
