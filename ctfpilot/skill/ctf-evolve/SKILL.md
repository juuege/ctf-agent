---
name: ctf-evolve
description: "RSI (Recursive Self-Improvement) for the CTF solver harness/runtime: iteratively evolve the solving method itself by running controlled ablation tests (via ctf-arena), incorporating verified new modules from ctf-research, and keeping the variant with the best adversarial clock. Use when the user asks to RSI/进化harness/进化runtime/迭代优化解题框架/留存最优harness. This is Layer 4 (top) — depends on ctf-arena, ctf-research, and ctf-lab."
name_cn: CTF RSI进化
description_cn: "解题框架自身的递归自我改进：消融→取论文新模块→合入→留存最优。对抗时钟门控，败者可回滚。"
create_source: super-agent-skill-creator
---

# CTF RSI 进化（ctf-evolve）

## 依赖图

```
ctf-lab (L0) ← 本 skill 用对抗时钟做门控，用探针验证进化不退化评测
ctf-solve (L1) ← 本 skill 进化其 harness/runtime
ctf-arena (L2) ← 本 skill 用其跑 A/B 对比
ctf-research (L3) ← 本 skill 从其取已消融验证的新模块
ctf-evolve (L4, 本skill) ← 顶层，无人依赖
```

## 进化循环

```
初始化 incumbent = 当前 harness 配置
循环：
  1. 候选生成（来源）：
     a. ctf-research 已验证的新模块（合入后跑消融）
     b. 自身 trace 分析（哪步最耗时→优化该步）
     c. ctf-feed 的轨迹库统计（哪类题反复卡→加检测器/改提示）
  2. 构造 candidate = incumbent + 候选变更（每次只改一个变量）
  3. ctf-arena 消融：incumbent vs candidate，同一留出集
  4. 判定（对抗时钟门控）：
     candidate.adversarial_time < incumbent.adversarial_time × (1 − threshold)
     且 selftest 6/6 不降
     且 ctf-lab poison 探针不退化（进化不能损害评测真实性）
  5. 赢 → incumbent = candidate，记入版本链
     输 → 回滚，候选进 backlog
```

## 变更类型（改什么）

| 层 | 变更举例 | 度量方式 |
|---|---|---|
| 引擎代码 | 新检测器 / 改 planner prompt / 改 JIT 频率 | arena 对抗时钟 |
| 知识库 | 精化卡片 / 修分类 / 归档已掌握 | eval-retrieval top1/top3 → 再跑 arena |
| 配置 | flag 正则 / 沙箱白名单 / 预算步数 | arena 对抗时钟 |
| 工作流 | 改 SKILL.md 触发器 / 改下钻条件 | 留出集人工验收 + arena |

## 每次只改一个变量

多个变量同时改 = 无法归因。如果必须同时改（如精化+分类审计），先拆成两轮。

## 门控公式

```
if candidate.adversarial_time ≤ incumbent.adversarial_time × (1 − 0.03)
   and selftest == 6/6
   and poison_probe_not_degraded:
    promote(candidate)
else:
    reject(candidate)  # 回滚，backlog 记录原因
```

threshold = 3%（可调，过小则噪声放行，过大则进化太慢）。

## 版本链与回滚

- **唯一采纳写路径（evolve-20261117-10 起）**：合入一律走 `python -X utf8 ctfpilot.py rsi adopt <PID> --note "<一句话>" --chain-json '<JSON 对象>'`。该命令把 ledger 行与 canonical 链 `CTFPilot-v0.1/skills/arena/state/version-chain.jsonl` 链行**一起提交**：写前双文件字节快照、写后重读验证，任一失败即双双回滚并抛错（evolve-20261117-15 起验证还会重读磁盘 ledger 行，要求其携带 chain_entry 注记，缺失即双双回滚）；重复 adopt 同一 PID 只追加 ledger history，绝不重复链行。禁止手改链文件（rsi-067..073 七轮手工追加全部丢失，靠 rsi-074 轮逐条重建 reconstructed=true 才找回）。
- **单一版本链（evolve-20261117-15 起）**：链只此一本；ledger 行自 rsi-083 起随行携带 chain_entry 注记。工作空间 `arena/state/chain.jsonl` 手维护镜像已**封账停用**（止于 entry 35，2026-11-17；rsi-080 曾整行丢失、rsi-081/082 靠手工补录两次、两次猜错时间戳）——留作只读历史存档（含 13 条 canonical 未载的早期采纳），**永远不要再手写它**；审计与回滚只看 canonical 链 + rsi ledger。
- `--chain-json` 字段：`winner`（变体名）、`change`（改动清单）、`rollback`（回滚配方）、`evidence`（门禁数据）、`next_candidates`（下轮候选）；`pid/date/prev/chain_entry` 由代码计算，不可覆盖。链上 1-19 编号为历史缺口（编号约定前的 11 条无号行 + 7 条重建前丢失），保留缺口不补号，新条目恒为 max+1。
- 回滚：恢复 prev 版本的引擎文件 + cases.jsonl（有 v1 归档）+ config.json。回滚件快照与配方写在链行的 `rollback` 字段与 `.temp/` 对应轮次目录。
- **败者不删**：backlog 保留所有 reject 的候选与原因，防止重复踩坑

## 防退化

- 对抗时钟不是唯一门控：`selftest` 与 `poison 探针` 是并行守卫
- 进化使某类题变快但另一类变慢 → 用 arena 分类别对比，**不许跨类补偿**（快 misc 慢 web 不算赢）
- 每次进化后跑 `eval-retrieval`（检索不能退化），跑 `probes.py leak`（背题风险不能升）

## 触发条件

- ctf-feed 累计落库 5 道新题 → 触发一轮进化候选分析
- ctf-research 验证了一个新模块 → 触发一轮合入消融
- 连续 2 道 NOT SOLVED 且同类 → 触发针对该类题的专项进化

## 汇报规范（用户要求：说人话，2026-11-16 起）

每轮结束后，给用户的正文**只用四行模板**，每行一句话以内，不堆术语：

```
改了什么：一句话（例：给解题知识库加了两张技术卡片——SM4 算法和截断 LCG）
为什么改：改之前出的什么实际问题（例：那道题用到这两个技术，但库里搜不到，卡了 392 秒）
原先的问题：这个缺口为什么一直存在（例：经验提取规则表里没有这两个关键词）
效果如何：门控一句话 + 未验证项如实说（例：6 道测试全过；真实提速要等下一道同类题才能算出）
```

铁律：
- 效果没验证就写"要等什么时候才能知道"，不许写"应该会快"
- 技术细节（提案 ID、sha、门控数据、回滚配方）只进台账文件（rsi ledger / backlog / canonical 链），**不进正文**
- 用户说"太长/看不懂"时，正文压回四行模板；细节改为"要不要看数据？"一句话征询

## 相关 skill

- `ctf-arena`：跑消融
- `ctf-research`：提供已验证的新模块
- `ctf-lab`：提供对抗时钟定义与探针
- `ctf-solve`：被进化的 harness
- `ctf-feed`：提供进化输入（轨迹）
