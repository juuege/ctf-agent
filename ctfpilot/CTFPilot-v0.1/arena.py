"""Arena: the competition question bank (arena 题库).

Turns "challenges lying around" into a ranked, difficulty-stratified practice
set so improvement is measurable per category (竞技场练兵).

Design:
  - registry.json records every registered challenge: name, category,
    source (pool / gh / local), difficulty tier, stats {solves, fails}.
  - difficulty tiers are DATA-DRIVEN (never vibes):
      easy   : triage confidence >= 0.8 AND fast-path-solved at least once
      hard   : never solved OR solved only after deep retries
      normal : everything else
    tiers recompute on every `arena stats`; manual override (--tier) wins.
  - scoring is per-category win-rate over arena attempts plus an overall
    accuracy; the arena ledger stays separate from production attempts so
    training data never pollutes the real trajectory store.

Everything offline, stdlib only, idempotent. Ledger lives under skills/arena/.
"""
import json
import os
import time


ARENA_DIR = "skills/arena"
REGISTRY = "registry.json"


def _project_root():
    # these modules sit AT the engine root (like ctfpilot.py): one dirname up.
    return os.path.dirname(os.path.abspath(__file__))


def paths(root=None):
    root = root or _project_root()
    d = os.path.join(root, ARENA_DIR)
    return {"dir": d, "registry": os.path.join(d, REGISTRY),
            "ledger": os.path.join(d, "attempts.jsonl")}


def load_registry(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"items": [], "updated": ""}


def save_registry(data, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log_attempt(entry, path):
    """Append one arena attempt (append-only training ledger)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    rec = dict(entry)
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def register(name, category, source, tier="normal", root=None):
    """Idempotent: same (name,category) refreshes, never duplicates."""
    p = paths(root)
    reg = load_registry(p["registry"])
    items = [i for i in reg["items"]
             if not (i["name"] == name and i["category"] == category)]
    items.append({"name": name, "category": category, "source": source,
                  "tier": tier, "solves": 0, "fails": 0,
                  "registered": time.strftime("%Y-%m-%d %H:%M:%S")})
    reg["items"] = items
    save_registry(reg, p["registry"])
    return reg


def recompute_tiers(root=None):
    """Difficulty from data: solved-once fast vs deep-retried vs never-solved.
    Returns summary; the registry file gets the new tiers."""
    p = paths(root)
    reg = load_registry(p["registry"])
    changed = 0
    for it in reg["items"]:
        prev = it.get("tier", "normal")
        n = (it.get("solves") or 0) + (it.get("fails") or 0)
        if it.get("fast_solved"):
            t = "easy"
        elif n == 0:
            t = "hard"          # never attempted: assume the harder end
        elif (it.get("solves") or 0) == 0:
            t = "hard"
        else:
            t = "normal"
        if t != prev:
            it["tier"] = t
            changed += 1
    save_registry(reg, p["registry"])
    return {"changed": changed, "items": len(reg["items"])}


def record_result(name, category, solved, tier, fast_solved,
                  root=None, log=print, time_s=None, cost_tokens=None):
    """Feed one arena solve outcome back into the registry (+attempts.jsonl).
    The production trajectory store is NOT touched -- arena data is practice
    data; recall/lessons only come from real solves.
    time_s / cost_tokens (evolve-20261116-05): optional practice clock and
    token cost for the cost axis; recorded only when provided, so old
    ledger rows stay valid."""
    p = paths(root)
    reg = load_registry(p["registry"])
    hit = None
    for it in reg["items"]:
        if it["name"] == name and it["category"] == category:
            hit = it
            break
    if hit is None:
        hit = {"name": name, "category": category, "source": "manual",
               "tier": tier or "normal", "solves": 0, "fails": 0}
        reg["items"].append(hit)
    if solved:
        hit["solves"] = hit.get("solves", 0) + 1
    else:
        hit["fails"] = hit.get("fails", 0) + 1
    hit["tier"] = tier or hit.get("tier", "normal")
    if fast_solved and hit["tier"] != "easy":
        hit["fast_solved"] = True
    save_registry(reg, p["registry"])
    entry = {"name": name, "category": category, "tier": hit["tier"],
             "solved": bool(solved), "fast_solved": bool(fast_solved)}
    if time_s is not None:
        entry["time_s"] = float(time_s)
    if cost_tokens is not None:
        entry["cost_tokens"] = int(cost_tokens)
    log_attempt(entry, p["ledger"])
    return hit


def leaderboard(root=None, top=12):
    """Per-category + overall practice metrics from the arena registry.
    Cost/clock means (evolve-20261116-05) come from ledger rows that carry
    them; rows without time/cost are excluded from the mean, never imputed."""
    p = paths(root)
    reg = load_registry(p["registry"])
    cats = {}
    total_s = total_f = 0
    for it in reg["items"]:
        s, f = it.get("solves") or 0, it.get("fails") or 0
        total_s += s
        total_f += f
        c = cats.setdefault(it["category"], {"cat": it["category"],
                                            "total": 0, "solved": 0,
                                            "tiers": {"easy": 0,
                                                      "normal": 0,
                                                      "hard": 0}})
        c["total"] += s + f
        c["solved"] += s
        c["tiers"][it.get("tier", "normal")] = \
            c["tiers"].get(it.get("tier", "normal"), 0) + 1
    # practice clock + cost means from the append-only ledger
    axis = {"time_s": [], "cost_tokens": []}
    cat_axis = {}
    try:
        with open(p["ledger"], "r", encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                for k in ("time_s", "cost_tokens"):
                    v = o.get(k)
                    if isinstance(v, (int, float)) and v >= 0:
                        axis[k].append(float(v))
                        cat_axis.setdefault(o.get("category") or "?", {}) \
                            .setdefault(k, []).append(float(v))
    except OSError:
        pass

    def _mean(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    rows = []
    for cat in sorted(cats, key=lambda k: -(cats[k]["solved"] /
                                            max(cats[k]["total"], 1))):
        c = cats[cat]
        ax = cat_axis.get(c["cat"], {})
        rows.append({"category": c["cat"], "attempted": c["total"],
                     "solved": c["solved"],
                     "win_rate": round(c["solved"] * 100 /
                                       max(c["total"], 1), 1),
                     "by_tier": c["tiers"],
                     "mean_time_s": _mean(ax.get("time_s") or []),
                     "mean_cost_tokens": _mean(ax.get("cost_tokens") or [])})
    return {"overall": {"attempted": total_s + total_f,
                        "solved": total_s,
                        "win_rate": round(total_s * 100 /
                                          max(total_s + total_f, 1), 1),
                        "mean_time_s": _mean(axis["time_s"]),
                        "mean_cost_tokens": _mean(axis["cost_tokens"])},
            "categories": rows, "registered": len(reg["items"]),
            "top": rows[:top]}