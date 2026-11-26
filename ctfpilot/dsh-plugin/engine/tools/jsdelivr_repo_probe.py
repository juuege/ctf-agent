#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jsdelivr 免配额选题探测脚本（API 限流降级通道）。

用法:
  python jsdelivr_repo_probe.py <repo1> [repo2 ...]   # 探测指定仓库（CTF-Archives 下）
  python jsdelivr_repo_probe.py --batch                # 探测内置候选批次

走 data.jsdelivr.com（独立 CDN 配额），列 <repo>@attachments 分支文件树，
输出含 zip/tar.gz 的路径与大小；done.json 里已做的题自动跳过。
排障经验（2026-11-11 本轮）：api.github.com 60/h 配额是刷题循环脆弱单点，
此脚本在限流时提供选题能力，但 jsdelivr 同步有延迟与盲区（新仓库仅 README、
部分仓库 404），结果为空时应回退 API 通道而非盲试。
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

WORKSPACE = Path(os.environ.get('CTFPILOT_WORKSPACE') or Path(__file__).resolve().parents[2])
DONE = WORKSPACE / "ctf-20min" / "done.json"
API = "https://data.jsdelivr.com/v1/packages/gh/CTF-Archives/{repo}"
API_VER = API + "@{ver}"
UA = {"User-Agent": "ctfpilot-jsdelivr-probe"}

BATCH = [
    "2026-NCO-Quals", "2026-NCO-Final", "2026-WQB-Quals", "2026-WQB-Finals",
    "2026-LanqiaoCup-Quals", "2026-LanqiaoCup-Finals", "2026-CCSSSC-Semis",
    "2025-CCB-CISCN-Quals", "2025-CCB-CISCN-Semis", "HKCERTCTF2025Quals",
    "2025-pengchengcup-quals", "2025-qwbs9-quals", "GuJianShanCTF2025",
    "2025-jswa", "2025-SDNISC-haixuan", "2025-gyxxaqjnds-Finals",
    "2024-newstar", "2024-PandaCupCTF", "2024-sanxia", "2024-shangmicup",
    "2024-dfjk", "2024-chb", "2024-qwbs8", "2024-YCB-Undergraduate",
    "2024-OMCTF", "2024-ISCC-Quals", "2024-CISCN-Quals", "2024-VNCTF-OnlyLocalSql",
    "2023-ISCC-Analysis", "2023-Bluehat-Quals",
]

def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def walk(nodes, path, out):
    for n in nodes:
        if n.get("type") == "directory":
            walk(n.get("files", []), path + n["name"] + "/", out)
        else:
            name = n["name"]
            if name.lower().endswith((".zip", ".tar.gz", ".tgz", ".gz")):
                out.append((path + name, n.get("size", 0)))

def load_done():
    try:
        data = json.loads(DONE.read_text(encoding="utf-8"))
        return set(data.get("done", []))
    except Exception:
        return set()

def probe(repo, done):
    try:
        ver = fetch(API.format(repo=repo))
        names = [v["version"] for v in ver.get("versions", [])]
        target = next((n for n in names if n.lower() == "attachments"), None) or (names[0] if names else None)
        if not target:
            print(f"[{repo}] jsdelivr 未同步（无分支信息）")
            return
        tree = fetch(API_VER.format(repo=repo, ver=target))
        found = []
        walk(tree.get("files", []), "", found)
        fresh = []
        for rel, size in found:
            rel_noprefix = rel[len("attachments/"):] if rel.startswith("attachments/") else rel
            stem = rel_noprefix
            low = stem.lower()
            for ext in (".tar.gz", ".tgz", ".zip", ".gz"):
                if low.endswith(ext):
                    stem = stem[: -len(ext)]
                    break
            parts = stem.rsplit("/", 1)
            stem_key = parts[0] if len(parts) > 1 else parts[-1]
            key = f"CTF-Archives/{repo}/{stem_key}"
            if key in done:
                continue
            fresh.append((rel, size))
        state = "无附件(仅README形态,附件可能在Releases)" if not found else \
                ("全部已做" if not fresh else f"{len(fresh)} 道未做")
        print(f"[{repo}@{target}] {state}")
        for rel, size in fresh:
            mb = size / 1048576
            skip = "  (>25MB,超限跳过)" if mb > 25 else ""
            print(f"    {rel}  {size}B{skip}")
    except Exception as e:
        print(f"[{repo}] ERR {e}")

def main():
    done = load_done()
    repos = BATCH if len(sys.argv) < 2 or sys.argv[1] == "--batch" else sys.argv[1:]
    for r in repos:
        probe(r, done)

if __name__ == "__main__":
    main()
