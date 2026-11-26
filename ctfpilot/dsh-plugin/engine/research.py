"""Research: the human-guided study queue (研究新模块).

Design (honest offline boundary):
  - internet is NOT available in this engine's sandbox, so retrieval is a
    QUEUE, not a crawler: each topic gets a draft file + evidence-pointer
    checklist, and an explicit routing decision for when material arrives.
  - drafts follow the writeup template (learn.writeup_learner), so anything
    the human finishes can go straight into learn-writeup -> rebuild-knowledge.
  - every card cites its evidence pointer (WikiTrace style): without a local
    source (writeup / writeup file / CTF wiki link recorded by hand) the
    topic stays 'queued' forever -- the engine never invents knowledge.

Storage: skills/research/{queue.jsonl, topics/}. stdlib only, idempotent.
"""
import json
import os
import sys
import time


RESEARCH_DIR = "skills/research"
TOPIC_STATUSES = ("queued", "drafting", "ready-ingest", "ingested")


def _project_root():
    # these modules sit AT the engine root (like ctfpilot.py): one dirname up.
    return os.path.dirname(os.path.abspath(__file__))


def paths(root=None):
    root = root or _project_root()
    d = os.path.join(root, RESEARCH_DIR)
    return {"dir": d, "queue": os.path.join(d, "queue.jsonl"),
            "topics": os.path.join(d, "topics")}


def load_queue(path):
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("id"):
                    out.append(o)
    except OSError:
        pass
    return out


def add_topic(topic, category="misc", source="manual", root=None,
              log=print):
    """Register one research topic + its draft file (idempotent on title)."""
    p = paths(root)
    q = load_queue(p["queue"])
    hit = next((t for t in q if t["topic"] == topic), None)
    if hit is not None:
        log("[research] already queued: %s" % topic)
        return hit
    rec = {"id": "rsch-%03d" % (len(q) + 1), "topic": topic,
           "category": category, "source": source,
           "status": TOPIC_STATUSES[0],
           "created": time.strftime("%Y-%m-%d %H:%M:%S"),
           "evidence": [], "note": ""}
    q.append(rec)
    draft = draft_template(topic, category, rec, root=root)
    _write_queue(q, p["queue"])
    log("[research] queued: %s (%s)" % (topic, category))
    log("[research] draft written: %s" % draft)
    return rec


def draft_template(topic, category, queue_rec, root=None):
    """Write a topic draft file aligned to learn.writeup_learner's parser:
    colon-label lines parse via FIELD_RE; narrative sections use exactly the
    headers SECTION_RE recognizes (失败原因 / 尝试路径 / 一得 / 样本特征).
    An evidence-pointer checklist gates ingest (<2 pointers -> blocked)."""
    p = paths(root)
    os.makedirs(p["topics"], exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_"
                   for ch in topic)[:60].strip("_") or "topic"
    path = os.path.join(p["topics"], "%s-%s.md" % (
        time.strftime("%Y%m%d"), safe))
    body = [
        "# %s" % topic,
        "",
        "> 研究草稿（research 研究队列产物）。带 ▢ 的行是证据指针清单：",
        "> 每收齐一项就登记到 queue 记录的 evidence 里；",
        "> **≥2 个证据指针 + 本草稿填完，才放行入库路由**。",
        "> 字段写法与 learn-writeup 对齐（冒号单行或下列小标题节）。",
        "> 每行只写字段值，不要跟注释（# 后的内容会被学习器当成字段值）。",
        "",
        "分类: %s" % category,
        "来源: %s" % (queue_rec.get("source") or "manual"),
        "题目: ",
        "",
        "## 样本特征",
        "（什么样的东西一看就该想到这个模块？）",
        "",
        "## 核心技术链",
        "核心技术链: （攻击步骤，可分号串联）",
        "",
        "有效技术: ",
        "陷阱类型: ",
        "可迁移模式: ",
        "变体预测: ",
        "载体: ",
        "",
        "## 失败原因",
        "（哪里卡过、为什么）",
        "",
        "## 尝试路径",
        "（走过的顺序）",
        "",
        "## 一得",
        "（这条研究最值得记的一点）",
        "",
        "▢ [证据指针 1]: writeup 文件 / CTF wiki URL / 比赛 writeup 集合（本地路径或 URL）",
        "▢ [证据指针 2]: 同上，至少 2 个不同来源",
        "",
        "未测试: ",
        "可固化性: ",
        "结果: 未验证",
        "Flag: ",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(body) + "\n")
    return path


def register_evidence(topic, pointer, kind, root=None, log=print):
    """Record one evidence pointer (or plain fact line) on a queued topic."""
    p = paths(root)
    q = load_queue(p["queue"])
    hit = next((t for t in q if t["topic"] == topic), None)
    if hit is None:
        raise KeyError("topic not queued: %s" % topic)
    ev = hit.setdefault("evidence", [])
    rec = {"pointer": pointer, "kind": kind,
           "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    if rec not in ev:
        ev.append(rec)
    hit["status"] = "ready-ingest" if len(ev) >= 2 else \
        (TOPIC_STATUSES[1] if ev else TOPIC_STATUSES[0])
    _write_queue(q, p["queue"])
    log("[research] evidence+1 (%d/2) on '%s'" % (len(ev), topic))
    return hit


def ingest_route(topic, root=None, log=print):
    """Explicit down-streaming path. Double gate:
      1) >=2 evidence pointers (no invented knowledge)
      2) the draft parses against learn.writeup_learner's grammar
         (finished drafts only -- not blank templates)
    Otherwise the route refuses and explains which gate blocked."""
    p = paths(root)
    q = load_queue(p["queue"])
    hit = next((t for t in q if t["topic"] == topic), None)
    if hit is None:
        raise KeyError("topic not queued: %s" % topic)
    ev = hit.get("evidence") or []
    if len(ev) < 2:
        log("[research] INGEST BLOCKED: '%s' has %d evidence pointers "
            "(need >=2). Finish the checklist first." % (topic, len(ev)))
        return None
    path = draft_path_for(topic, root)
    if path is None or not os.path.exists(path):
        log("[research] INGEST BLOCKED: no draft file for '%s' -- write the "
            "draft first" % topic)
        return None
    if not draft_parser_ok(path):
        log("[research] INGEST BLOCKED: draft for '%s' does not parse against "
            "the writeup-learner grammar (missing 题型/载体/核心技术链 labels). "
            "Fix the draft structure before ingesting." % topic)
        return None
    next_cmd = ("ctfpilot learn-writeup \"%s\" --name \"%s\" && "
                "ctfpilot rebuild-knowledge <writeups_dir>" % (path, topic))
    hit["status"] = TOPIC_STATUSES[2]
    _write_queue(q, p["queue"])
    log("[research] '%s' ready to ingest (evidence %d/2, draft parses). Run:" %
        (topic, len(ev)))
    log("      %s" % next_cmd)
    return hit


def draft_path_for(topic, root=None):
    p = paths(root)
    pref = "".join(ch if ch.isalnum() or ch in "-_" else "_"
                   for ch in topic)[:60].strip("_")
    if not pref:
        return None
    try:
        entries = sorted(os.listdir(p["topics"]), reverse=True)
    except OSError:
        return None
    cand = [e for e in entries if e.startswith(time.strftime("%Y%m%d")) and
            pref in e]
    return os.path.join(p["topics"], cand[0]) if cand else None


def draft_parser_ok(draft_path):
    """Structural alignment check: does this draft parse against
    learn.writeup_learner's FIELD_RE / SECTION_RE grammar?
    Note: checks structure only (label lines + recognized section headers);
    whether the content is finished is the human's call + evidence gate."""
    try:
        sys.path.insert(0, _project_root())
        from learn.writeup_learner import parse_writeup
    except Exception:
        return False
    try:
        with open(draft_path, "r", encoding="utf-8") as f:
            parsed = parse_writeup(f.read())
    except Exception:
        return False
    fields = (parsed or {}).get("fields") or {}
    # label-based fields the learner indexes case cards by
    for k in ("题型", "载体", "核心技术链"):
        if k not in fields:
            return False
    return True


def list_topics(status=None, root=None, limit=50):
    rows = load_queue(paths(root)["queue"])
    if status:
        rows = [t for t in rows if t.get("status") == status]
    rows.sort(key=lambda t: str(t.get("created") or ""), reverse=True)
    return rows[:limit]


def _write_queue(q, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for t in q:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")