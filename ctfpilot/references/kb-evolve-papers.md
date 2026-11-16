# kb-evolve-papers.md — 论文证据链（ctf-research 精读库）

> 用途：ctf-research 流水线第 2 步（精读）的归档。每篇必带：机制要点 / 关键数字 /
> 保真度判定（一致|简化|缺失|证伪）/ 本域（CTFPilot）复现结果。
> 引用纪律：对外报数字必须携带 [全文精读] / [摘要级] / [工程改造] 等级标注。
> 未经本域复现的不叫 SOTA，叫候选机制。
> 维护规则：三个月前的记录重跑一次消融确认结论仍成立（见每节末行）。

域内基线快照（复现对照用）：2026-11-16 11:32:47，top1 43.75% / top3 81.25% /
MRR 0.5947 / evaluable 16 / cases 135（solved 81）/ 已知评测陷阱：载体自证已修
（carrier 中位覆盖 0.09）、poison 探针 v0 有假阴性设计缺陷（毒行与评测查询零耦合）。

---

## 1. Zep Agent Skills — arXiv 2611.01386 [全文精读]

Learning by Doing: Autonomously Compiling and Curating Agent Skills from Verified Traces. Zep AI, 2026-11-01.

**机制要点**
- 程序性记忆服务：Trace（带验证强度）→ Reflector/Curator 双 LLM 编译 → 准入门控 → 版本化 Skill。
- 只用**强验证**（工具/授权人/外部系统）的 attempt 出证；自报成功不作数；两条验证记录冲突则该 Trace 不自动出卡。
- **使用效果加权排序**（本库关注的核心机制）：search weight w = (k·n/(n+1) + s) / (k + t)；
  n=支持的 attempt family 数，s/t=成功/总使用 trial 数。检索排名 = 词汇分 × w；
  trust value = 95% Wilson 下界，达标升 established；验证过的失败会拉低 w 至先验均值以下。
- citation-or-retain 消费规则：被引用的强验证源消费，未被引用的成功源保留待后续编译轮。
- 检索用词面（不用嵌入）：descriptor 短文本 + 渐进披露；全文明确未做向量对照实验。

**关键数字（[全文精读]）**
- 26 cell 全正增益 +4.5～+18.8 points（teacher-executor 协议，弱模型执行）；τ3 汇总 +8.6 [95% CI +5.5,+11.9]。
- ALFWorld 对比连续微调 RSD：Zep Skills Qwen3-8B +32.6（42.8→75.4）vs RSD 同底座 +13.7；免训练。
- 增益集中来源：teacher 4/4 全过的任务 +17.0 [11.6,22.3]，teacher 从未过的仅 +0.6 [−2.2,+4.0]——**主基准不考迁移**，论文自己承认。
- token 开销：单 episode 0.2–2.3M；AppWorld 7/9 cell 总 token 上升（索引常驻 +2300 tokens/调用）。
- k 值与 trust 阈值论文未披露（引用时不得臆造）。

**保真度判定：缺失（核心回环）**
- 我们没有"使用 trial"记录：CTFPilot 的召回无 usage-outcome 回写，solved 只在 ingest 时写死一次。
- SKILL 判定依据：以 solved 当使用效果代理已在本地被证伪（121 卡消融持平）——与论文 7.5 节
  "增益集中在 teacher 解过的题"互证：无真实"用后结果"信号，加权项退化为零信息。

**本域复现结果：暂缓，门槛=真实 recall→solve 回环**
- 触发条件：engine recall 侧新增 usage-outcome 落轨（类似 Zep 的 Skill use 记录），
  且至少累积 3 轮 feed 之后。在 .temp 复现，不动产线。

*复验日期：2026-11-16（首录）。下次复验：2027-02-16。*

---

## 2. FGSE — arXiv 2611.14326 [全文精读]

When Should Agents Remember? Falsification-Gated Self-Evolution for LLM Agents. NTU/GT/UW/WUSL/Sydney/NUS, 2026-11-10.

**机制要点**
- 三探针：把候选 lesson 表示为结构化假设 h=(前置条件 c_h, 行为变更 b_h, 期望效果 e_h, 验证器 v_h)；
  合格需过 commitment gate：Ĝ(h)≥η（目标迁移增益）∧ P̂_fail(h)≤δ（证伪率）∧ D̂_reg(h)≤β（回归损伤）。
- 证伪探针 F(h) 专为暴露**过度泛化**构造（边界案例/前置违反/诱饵）；回归探针 R(h) 是已解决的存量任务，不许变更劣化它们。
- 失败集中在可分离子域时走一次前置条件收窄（refinement pass）再评；仍败则拒绝。
- 理论：经验 Bernstein 下界 + Bernoulli-KL 上界 + union bound ⇒ 有限探针门的泛化保证（Theorem 1）。

**关键数字（[全文精读]）**
- 主表：39.8 WebArena / 78.8 AppWorld Avg / 0.724 τ-Retail Pass1 / 0.487 τ-Airline（DeepSeek-V3.1）。
- 安全性指标：证伪失败 5.0%、回归损伤 1.3%、有害已提交更新 3.6%——最低基线 ACEonline 7.9%（≈2.2×）。
- 消融链：去结构化假设 -4.78（LoCoMo）最大；去证伪探针 ⇒ P̂_fail 5.0%→18.6%、harmful 3.6%→10.6%；
  去回归档案 ⇒ D̂_reg 5.2%→1.3%（回归探针把损伤从 5.2% 压到 1.3%）。
- 超参（C.5）：|T|=8、|F|=16、|R|=16、η=0.03、δ=0.05、β=0.01、γ_h=0.5、λ_h=0.8。
- D.2：Pareto 上 20–40 次 verifier 调用/候选 即可把 harmful 压 2.5–4.5%；targeted 探针 @K=8 检出坏更新约为纯随机的 2×。

**保真度判定：部分可行，映射明确**
- 目标迁移探针 T(h) ≈ 我们的 eval-retrieval 留出集；回归探针 R(h) ≈ 已解题回归集
  （solved=81 ≥ 74 门槛已满足）；证伪探针 F(h) ≈ 对抗样本卡（需构造）。
- Weakest-link 思想可在 token scorer 上模拟；凸组合策略混合不可复现（我们无策略分布接口）。

**本域复现结果：P1 待做，方案已定**
- 现成切入点①：lesson 准入三探针——对 learn-writeup 产出的新卡先过 (Ĝ, P̂_fail, D̂_reg) 三门再入库，
  门槛取论文同量级（η=0.03/δ=0.05/β=0.01 起步，按本地分辨率 6.25pp/题 校准）。
- 现成切入点②（与 2026-11-16 体检结论同构）：poison v2 的毒行改为**从评测查询集取词**——
  这正是 FGSE F(h) 探针的构造原则（探针必须与查询分布耦合），可修 lab 探针 v0 的假阴性缺陷。
- 回归集：74 道已解题（现 solved=81，取子集固定 seed）。

*复验日期：2026-11-16（首录）。下次复验：2027-02-16。*

---

## 3. SKALOS — arXiv 2610.34728 [全文精读]

SKALOS: Learning to Distinguish Similar Skills with Capability Graphs. 北航/广西师大/南开/清华, 2026-10-26.

**机制要点**
- 多层级能力图：共享层（跨技能规范节点）+ 局部层（技能特有证据）；节点四类（capability/method/artifact/constraint）+ 类型化关系（implements/produces/constrains/…）。
- **合取否决**（本库关注的核心机制）：Max–Min 半环 witness——各分支按最弱证据计分（⊗=min），断言以最强实现路径计（⊕=max）；缺一个需求即整个 realization 归零；**同一实现的分支不得跨实现拼装**。
- 排名反馈校准：mask 单元素测其对正/负例排序边际的贡献，抑制不可靠抽取边。

**关键数字（[全文精读]）**
- SkillRet：NDCG@10 86.76 vs matched text-only reranker 84.07（**+2.68pp**）；Recall@10 94.57。
- ALFWorld：任务成功率 22%→29%（witness 引导选技能）。
- 参数量 2.0M + 冻结 0.6B 编码器；top-20 候选池重排。
- Graph-as-Text 序列化几乎无增益（84.09 vs 84.07）——**结构必须进图模型而非转文本**。

**保真度判定：证伪（在 121 卡规模）**
- 本地消融：token scorer 上模拟合取否决（所有需求 token 都命中才给分），121 卡本地持平、无收益。
- 判定原因：案例库太小，同 category 池内相邻卡本就稀疏，合取约束没有可剪枝的对象；
  论文场景是 63K 查询 + 硬负例密集的重排，与我们 16 条可评测查询不在同一规模。
- 复现状态：500+ 卡再评估；触发检查点：cases ≥ 500 或单池硬负例 ≥ 30 时重跑。

*复验日期：2026-11-16（首录）。下次复验：2027-02-16 或触发检查点先到。*

---

## 4. ArchOR — arXiv 2610.41909 [全文精读]

Structure-Aware Skill Retrieval and Arbitration for Optimization Modeling. 2026-10-30.

**机制要点**
- 原型残差 CARL：z' = Norm(z + λ·P_r P_rᵀ(a_g(z) − z))，把缓存向量拉近最近原型；λ=0.35、r=16、免 GPU 免梯度。
- 开放维度签名 S3R：属性-值集（problem_class/var_pattern/constraint_family/…），加权 Jaccard 重叠先硬过滤出 top-K（K=8），再让 LLM 语义重排只看这 K 个。
- **基线保全仲裁**（本库关注的核心机制，防"直接替换型回归"）：
  仅当 V(y_a) ∧ (c_b≤0.62 ∨ c_a>c_b ∨ r_a≥7) 才采用结构路线，否则回退原路线。
  V(y_a) 要求结构路线返回非空有限解——把"会执行"从"该采纳"里剥离。

**关键数字（[全文精读]）——摘要夸大已在首轮精读指出，此处复核**
- 摘要说 "consistent improvements"：实测 **micro 仅 +1.64pp**（697→715，McNemar p=0.0009，23 净修复/5 净损伤）；macro +2.68pp。
- **直接路由（去仲裁）micro −1.91pp**：39 错→对 / 60 对→错（p=0.044）——去掉仲裁会净伤害，摘要不及此。
- 单模块消融（OptMATH）：CARL-only −4.21pp、S3R-only −4.21pp、直接合并 −3.01pp、带仲裁 +4.22pp——**两个模块各自有害、合起来靠仲裁才有益**。
- 检索诊断（leave-one-out）：Hit@1 0.607→0.804、MRR 0.682→0.839——表示层改进 ≠ 端到端改进的直接证据。
- K=8 把选择器输入从 46 技能缩到 8。

**保真度判定：简化可复现（本域复现方案已具备）**
- 我们无嵌入模型：CARL 需 .temp 装轻量嵌入（候选 sentence-transformers 或自建 TF-IDF 原型），
  或者跳过 CARL 只做 S3R 风格的结构签名硬过滤——我们的 category+signals 已就是"开放维度签名"的雏形
  （category=problem_class，signals=var_pattern 的替代）。
- c_b（基线置信度）没有直接对应物：可用 ranking 分差或 flagcheck 的置信输出近似。

**本域复现结果：待做（装依赖优先级低于 FGSE）**
- 计划：先 S3R-lite（category 池内按 signals token 重叠硬过滤），复用 eval-retrieval 实测；
  仲裁用"快照前 top1 卡在过滤后仍 top3 才采纳新排序"的保守规则。
- 预期风险的直接教训（论文给的）：**新排序模块单独上线很可能负收益，必须带保全仲裁一起测**——
  这条已写进 ctf-arena 后续变体设计的默认检查项。

*复验日期：2026-11-16（首录）。下次复验：2027-02-16。*

---

## 5. SEMV — arXiv 2609.27175 [全文精读]

Self-Evolving Multimedia Verification through Memory Consolidation of Contestation Experiences. FPT/UNB/VAST, 2026-09-23.

**机制要点**
- 记忆整合七步生命周期：捕获→人争议→预测后反思→**验证与暂存**→多经验整合→长期晋升→复用回写。
- 验证门（Eq.26）：候选须（a）源 case 属学习集；（b）证据/论证 ID 可回溯到 trace；（c）置信 c_κ≥0.60；
  （d）Safe(κ)；（e）非同源重复。**验证集来源的候选一律不得通过。**
- 晋升门（Beta 置信 Conf(C)=(α_C+α_0)/(α_C+β_C+α_0+β_0)）：
  episodic (0.85,1,1) / failure (0.70,2,2) / semantic (0.75,3,3)——三类不同门槛。
- **冲突保留**（本库关注的核心机制）：矛盾不下判决、不删除；记录维护 (α_m,β_m)，
  conflict ratio>0.30 → under review；弃用需"观察到矛盾且置信<0.45"；复活需 cr≤0.20 且 c≥0.45。
- 认识论边界（Def 3.1）：论证必须引**当前案例**证据；记忆只能引导规划，不能替代证据进入论证。

**关键数字（[全文精读]）**
- COSMOS：91.88% vs 最强基线 89.10%（p=0.006, n=1700）；ECE 0.049。
- 记忆递进实验（同库对照）：∅86.06 → +episodic 87.29（负迁移 0.4）→ +无验证更新 87.59（**负迁移升至 5.7%**）
  → +验证整合 90.71（0.5）→ +冲突保留 **91.88（负迁移 0.2%）**——验证整合把负迁移从 5.7% 压到 0.5%，冲突保留再压到 0.2%。
- 184 个反思候选去向：61.4% 过 grounding / 42.5% 晋升 / 18.6% 合并 / 7.1% 保留为未决冲突 / 31.8% 拒绝。
- 争议性（CTR）：scoped causal revision 修正 96.7% 初始错误、诱发错误 0、计算节省 52.8%、定位率 97.6%。

**保真度判定：部分可行**
- 无验证门时负迁移 5.7% 的教训直接适用：我们 learn-writeup 目前就是"无验证更新"通道；
  这是最值得先堵的漏洞（与 FGSE 切入点①同一条路）。
- 冲突保留需案例对撞检测（两张卡 signals 互相矛盾与否的判定器），当前无实现；
  弱化版先行：rebuild 时对同 category 同 key-triplet 卡打 conflict flag，不删不覆盖——已有 merge_refined
  布尔字段保护的教训（category_inferred 覆盖事故）说明"保留而非覆盖"在我们域内有真实前科。

**本域复现结果：待做（随下次卡片精化批一起上）**
- 最小改动版：learn-writeup 出的新卡在入库前过 grounding 检查（source 存在 + signals 与题面 token 重叠≥阈值）+ 与存量同池卡做对撞检测，命中即挂 conflict 标记进暂存区。
- 晋升门槛首版参数借用论文语义：failure 类 (0.70, ≥2 独立来源, ≥2 独立题)。

*复验日期：2026-11-16（首录）。下次复验：2027-02-16。*

---

## 附录：本域与论文域的结构差异（引用时需注意）

| 维度 | Zep/FGSE/SEMV 论文域 | CTFPilot 本域 |
|---|---|---|
| 评测密度 | 千级 query / 千级 episode | 16 条可评测查询（单题分辨率 6.25pp） |
| 使用反馈 | 有 usage-outcome 回环 | 无（solved 一次性入库） |
| 验证器 | 外部 verifier（agent 不可自证） | flagcheck + 人工，但 lesson 入库无门 |
| 检索器 | 嵌入/重排模型 | 纯标准库 token 重叠 |
| 研究对象 | memory/skill 准入与治理 | 解题 harness 的召回与快通道 |

> 结论性提醒：五篇中只有 FGSE 的三探针与 SEMV 的验证门**现在就能落地**（数据具备）；
> Zep 加权排序缺回环、SKALOS 缺规模、ArchOR 缺嵌入依赖、SEMV 冲突检测缺对撞器——
> 按 backlog 排期：FGSE 优先级 P1 > SEMV 验证门（P1 同批）> ArchOR S3R-lite（P2）>
> 其余待触发条件。

> AI生成
