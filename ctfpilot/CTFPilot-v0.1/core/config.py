"""Configuration loader: reads config.json next to project root, provides defaults."""
import json
import os
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_FLAG_PATTERNS = [
    r"flag\{[^\s{}]{4,256}\}",
    r"FLAG\{[^\s{}]{4,256}\}",
    r"ctf\{[^\s{}]{4,256}\}",
    r"CTF\{[^\s{}]{4,256}\}",
    r"DASCTF\{[^\s{}]{4,256}\}",
    r"ctfshow\{[^\s{}]{4,256}\}",
]


def load_config(config_path=None):
    if config_path is None:
        config_path = os.path.join(PROJECT_ROOT, "config.json")
    cfg = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    # Safe defaults for anything missing
    cfg.setdefault("llm", {"backends": [], "mock_fallback": True})
    cfg.setdefault("sandbox", {})
    cfg.setdefault("flag_patterns", DEFAULT_FLAG_PATTERNS)
    cfg.setdefault("budget", {"max_deep_steps": 12,
                             "max_llm_calls_per_solve": 24,
                             "fastpath_time_limit_s": 60})
    cfg.setdefault("memory", {"db_path": "memory/trajectories.sqlite",
                              "recall_top_k": 3})
    return cfg


def compiled_flag_patterns(cfg, extra=None):
    pats = list(cfg.get("flag_patterns", DEFAULT_FLAG_PATTERNS))
    if extra:
        pats = [extra] + pats
    return [re.compile(p) for p in pats]


def path_under_project(rel):
    return os.path.join(PROJECT_ROOT, rel)
