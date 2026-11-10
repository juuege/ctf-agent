"""Flag detection and validation.

Strict-pattern matching only: a flag counts only when it matches a known
CTF flag format. This avoids false positives from random base64 strings.
Anti-cheat note: flags must come from local execution output; anything that
would require fetching a public writeup online is out of scope by default.
"""
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
