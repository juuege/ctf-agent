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

- 每次合入在 `arena/state/` 记 `{"winner": <variant>, "adversarial_time": x, "prev": <sha>, "date": ...}`
- 回滚：恢复 prev 版本的引擎文件 + cases.jsonl（有 v1 归档）+ config.json
- **败者不删**：backlog 保留所有 reject 的候选与原因，防止重复踩坑

## 防退化

- 对抗时钟不是唯一门控：`selftest` 与 `poison 探针` 是并行守卫
- 进化使某类题变快但另一类变慢 → 用 arena 分类别对比，**不许跨类补偿**（快 misc 慢 web 不算赢）
- 每次进化后跑 `eval-retrieval`（检索不能退化），跑 `probes.py leak`（背题风险不能升）

## 触发条件

- ctf-feed 累计落库 5 道新题 → 触发一轮进化候选分析
- ctf-research 验证了一个新模块 → 触发一轮合入消融
- 连续 2 道 NOT SOLVED 且同类 → 触发针对该类题的专项进化

## 相关 skill

- `ctf-arena`：跑消融
- `ctf-research`：提供已验证的新模块
- `ctf-lab`：提供对抗时钟定义与探针
- `ctf-solve`：被进化的 harness
- `ctf-feed`：提供进化输入（轨迹）
