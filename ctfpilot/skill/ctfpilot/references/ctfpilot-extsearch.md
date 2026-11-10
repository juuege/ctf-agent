# CTFPilot 外部检索源参考（论文 · GitHub · 社区）

本文件在元认知复盘发现"临场才学"、固化前对标、修改 SKILL.md 前对标外部基准时读取。北极星：**用最快的实测路径拿到可靠外部思路，供自我进化**。

## 1. 实测基准（2026-11-10 本机国内网络实测，每项 2 次取最优）

| 检索目标 | 最快路径 | 实测耗时 | 备注 |
|---|---|---|---|
| 论文（结构化：标题/摘要/日期/被引） | paper-search 技能脚本（arXiv 源） | 全流程 **1.33s**（API 本体 ~0.5s） | OpenAlex ~1.7s、Crossref ~2.5s 仅作备用降级源；`--check` 约 5s 内自检 |
| 论文（已知 arXiv ID / 单篇详情） | webfetch 抓 arxiv.org 页面 | 网络部分 **0.28s** | 论文页直连快，适合"补读一篇"场景 |
| GitHub 项目 | api.github.com 搜索 API 直连（纯标准库 urllib，见 §3） | **0.42s**（含解析 1.03s） | 匿名 search API 限额 10 次/分钟，正常使用够；返回 stars/描述/更新时间 |
| GitHub 项目（浏览式/网页） | github.com 网页搜索 | 0.72s | 可用；注意 raw.githubusercontent.com 直连**超时不通**，下载仓库文件才需代理，检索本身不需要 |
| 中文社区/媒体报道/方法论文章 | online_search | 无计时回传（平台工具） | 无法本地计时，按可用性与结果质量选用；适合找 writeup 文章、行业观点 |

## 2. 检索决策规则

1. 查"某技术/领域有没有论文、进展如何" → paper-search 脚本，英文关键词，`-n 3~5` 足够决策；找高被引经典加 `-s openalex --sort citations`。
2. 查"有没有现成工具/仓库/最佳实践" → GitHub API 直连（§3），按 stars 排序初筛；中文补充或找不到时再用 online_search。
3. 查"别人怎么做/方法论/观点" → online_search 优先（中文内容多）；学术论文场景用 paper-search。
4. **单路径 >5s 或失败即换下一优先级**，不在同一路径反复重试；检索耗时计入元认知复盘的耗时剖析。
5. **解题卡壳专用**（补位 15 分钟无进展或同策略连败 2 次触发）：先搜"题名+赛事+writeup"（online_search）→ 再搜"核心技术词+writeup/参数"→ 再 GitHub 搜工具；找到后抄参数、本地脚本复现验证，禁止直接信任未验证的 writeup 结论。

## 3. GitHub API 调用法（纯标准库，2026-11-10 实测可用）

```python
import json, urllib.request
url = "https://api.github.com/search/repositories?q=<关键词>&sort=stars&order=desc&per_page=5"
req = urllib.request.Request(url, headers={"User-Agent": "ctfpilot/0.1", "Accept": "application/vnd.github+json"})
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read())
for it in data["items"]:
    print(it["full_name"], it["stargazers_count"], str(it.get("description"))[:80])
```

paper-search 脚本路径：`<paper-search 技能目录>/scripts/search_papers.py`（用法见该技能 SKILL.md；纯标准库，三源自动重试降级）。

## 4. 自我进化对标关键词组（按需取用，英文检索质量最佳）

| 主题 | 检索词 |
|---|---|
| RSI / 递归自我改进 | recursive self-improvement / self-evolving agent / agent self-improvement loop |
| skill 与 prompt 工程 | progressive disclosure skill design / LLM agent skill engineering |
| CTF 方法论 | CTF tools checklist / CTF writeup methodology / awesome-ctf |
| 工作流与 agent 循环 | agentic loop design / hypothesis-execute-observe / agent verification reward hacking |
| 记忆与经验沉淀 | agent structured memory / experience transfer across deployments |

## 5. runtime 对标与基准测试（自主执行，不等提醒）

- **检索层**：用 §4 关键词组查 agent runtime 提速实践（另加：agent runtime latency / LLM serving speedup / agent execution cache），对照本引擎可借鉴点，注明来源与检索日期。
- **实测层**：同题 × 三种 runtime 配置计时矩阵（记录耗时 + 是否解出）：

```powershell
$env:CTFPILOT_PYTHON = $py
$t1 = Measure-Command { & $py -X utf8 .\ctfpilot.py solve <题> --mode fast 2>&1 | Select-String 'result' }
$t2 = Measure-Command { & $py -X utf8 .\ctfpilot.py solve <题> --mode deep --brain mock 2>&1 | Select-String 'result' }
$t3 = Measure-Command { & $py -X utf8 .\ctfpilot.py solve <题> --mode deep --brain llm 2>&1 | Select-String 'result' }
"fast=$($t1.TotalSeconds)s mock=$($t2.TotalSeconds)s llm=$($t3.TotalSeconds)s"
```

- **结论落地**：最快且成功率不降的配置写入 `config.json` 作为默认；耗时矩阵与决策依据落库一条 writeup。
- **时效**：`[llm] probe` 显示后端可用性变化时重跑矩阵，勿沿用过期结论。

## 6. 纪律

- 引用外部内容注明来源与检索日期；GitHub stars 只作初筛参考，不作质量结论。
- 检索是"对标与补位"，不替代自主分析：先查自有 trace 与 recall，再走外部检索。
- 实测基准随网络漂移，超过 1 个月使用前先复测（重跑 `.temp/net_bench.py` 同类探测）并更新 §1 日期。

> AI生成
