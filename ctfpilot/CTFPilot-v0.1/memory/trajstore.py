"""Trajectory store (SQLite): every attempt, error and outcome is recorded so
later challenges can recall similar problems/failures (case-based learning).

Grounding:
- CTF-Dojo (arXiv 2508.18370): training on 486 execution-verified
  trajectories gave up to +11.6% absolute on CTF benchmarks.
- TermiAgent (arXiv 2509.09207): located memory activation beats naive RAG.
We store the *location* (category + error class + technique) with each record
so recall is precise.
"""
import json
import os
import sqlite3
import time


SCHEMA = """
CREATE TABLE IF NOT EXISTS challenges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, category TEXT, dir_path TEXT,
    created_at REAL, solved INTEGER, flag TEXT,
    mode TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    challenge_id INTEGER,
    ts REAL, phase TEXT, detector TEXT, detail TEXT,
    success INTEGER, flag TEXT, error_class TEXT, evidence TEXT
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    challenge_id INTEGER, ts REAL,
    category TEXT, technique TEXT, when_to_use TEXT, snippet TEXT
);
CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    challenge_id INTEGER, source TEXT,
    step_no INTEGER, description TEXT, done INTEGER, ts REAL
);
"""


class TrajStore:
    def __init__(self, db_path):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- challenges ----------
    def new_challenge(self, name, category, dir_path, mode, summary):
        cur = self.conn.execute(
            "INSERT INTO challenges(name, category, dir_path, created_at, solved, mode, summary) "
            "VALUES(?,?,?,?,0,?,?)",
            (name, category, dir_path, time.time(), mode, summary))
        self.conn.commit()
        return cur.lastrowid

    def finish_challenge(self, cid, solved, flag):
        self.conn.execute("UPDATE challenges SET solved=?, flag=? WHERE id=?",
                          (1 if solved else 0, flag or "", cid))
        self.conn.commit()

    # ---------- attempts ----------
    def log_attempt(self, cid, phase, detector, detail, success, flag=None,
                    error_class=None, evidence=None):
        self.conn.execute(
            "INSERT INTO attempts(challenge_id, ts, phase, detector, detail, success, flag, error_class, evidence) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (cid, time.time(), phase, detector, str(detail)[:2000],
             1 if success else 0, flag or "", error_class or "",
             (evidence or "")[:8000]))
        self.conn.commit()

    # ---------- lessons ----------
    @staticmethod
    def _lesson_key(when_to_use):
        """Source fingerprint: quoted name in when_to_use, else prefix."""
        s = when_to_use or ""
        i = s.find("'")
        if i >= 0:
            j = s.find("'", i + 1)
            if j > i:
                return s[i + 1:j][:80]
        return s[:50]

    def add_lesson(self, cid, category, technique, when_to_use, snippet):
        """Upsert by (category, technique, source): repeat solves refresh
        ts/evidence instead of appending duplicate rows."""
        rows = self.conn.execute(
            "SELECT id, when_to_use FROM lessons WHERE category=? AND technique=?",
            (category, technique)).fetchall()
        key = self._lesson_key(when_to_use)
        hit = next((r[0] for r in rows if self._lesson_key(r[1]) == key), None)
        if hit is not None:
            self.conn.execute(
                "UPDATE lessons SET challenge_id=?, ts=?, when_to_use=?, snippet=? WHERE id=?",
                (cid, time.time(), when_to_use, (snippet or "")[:6000], hit))
        else:
            self.conn.execute(
                "INSERT INTO lessons(challenge_id, ts, category, technique, when_to_use, snippet) "
                "VALUES(?,?,?,?,?,?)",
                (cid, time.time(), category, technique, when_to_use, (snippet or "")[:6000]))
        self.conn.commit()

    def recall_lessons(self, category, top_k=3):
        """Newest-first, one entry per technique, so top_k covers k distinct
        techniques even when the table still holds historical duplicates."""
        rows = self.conn.execute(
            "SELECT category, technique, when_to_use, snippet FROM lessons "
            "WHERE category=? ORDER BY ts DESC", (category,)).fetchall()
        seen, out = set(), []
        for r in rows:
            if r[1] in seen:
                continue
            seen.add(r[1])
            out.append({"category": r[0], "technique": r[1],
                        "when_to_use": r[2], "snippet": r[3]})
            if len(out) >= top_k:
                break
        return out

    # ---------- checkpoints ----------
    def add_checkpoints(self, cid, source, descriptions):
        for i, d in enumerate(descriptions):
            self.conn.execute(
                "INSERT INTO checkpoints(challenge_id, source, step_no, description, done, ts) "
                "VALUES(?,?,?,?,0,?)", (cid, source, i + 1, d, time.time()))
        self.conn.commit()

    def get_checkpoints(self, cid):
        cur = self.conn.execute(
            "SELECT step_no, description FROM checkpoints WHERE challenge_id=? ORDER BY step_no", (cid,))
        return cur.fetchall()

    # ---------- recall for planner ----------
    def recall_similar(self, category, keyword_tokens, top_k=3):
        """Challenges in same category, solved ones first; matched name/evidence boosted."""
        rows = self.conn.execute(
            "SELECT id, name, category, solved, flag, summary FROM challenges "
            "WHERE category=? ORDER BY solved DESC, created_at DESC LIMIT 30",
            (category,)).fetchall()
        scored = []
        for r in rows:
            cid, name, cat, solved, flag, summary = r
            hay = (name + " " + (summary or "")).lower()
            score = sum(1 for t in keyword_tokens if t and t.lower() in hay)
            scored.append((score + (2 if solved else 0), r))
        scored.sort(key=lambda x: -x[0])
        out = []
        for _s, r in scored[:top_k]:
            out.append({"id": r[0], "name": r[1], "solved": bool(r[3])})
        return out

    def recent_attempts_digest(self, cid, limit=40):
        rows = self.conn.execute(
            "SELECT phase, detector, detail, success FROM attempts "
            "WHERE challenge_id=? ORDER BY id LIMIT ?", (cid, limit)).fetchall()
        return [{"phase": r[0], "detector": r[1], "detail": r[2], "success": bool(r[3])}
                for r in rows]

    def stats(self):
        ch = self.conn.execute("SELECT COUNT(*), SUM(solved) FROM challenges").fetchone()
        at = self.conn.execute("SELECT COUNT(*) FROM attempts").fetchone()
        ls = self.conn.execute("SELECT COUNT(*) FROM lessons").fetchone()
        return {"challenges": ch[0] or 0, "solved": ch[1] or 0,
                "attempts": at[0] or 0, "lessons": ls[0] or 0}
