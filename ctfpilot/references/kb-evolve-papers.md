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

## 6. HPTSA — arXiv 2406.01637 [全文精读]（2026-11-17 多 agent 解题批次①）

Teams of LLM Agents can Exploit Zero-Day Vulnerabilities. UIUC（Zhu/Fang/Bindu/Kang），v2 2025-03-29。

**机制要点**
- 三层结构：hierarchical planner（探环境、定攻击面与指令）→ team manager（选专家 agent、回收前次 run 的信息、可带更细指令重跑）→ 6 个任务专精 agent（XSS/SQLi/CSRF/SSTI/ZAP/generic），各带工具（Playwright/终端/文件；SQLi 加 sqlmap、ZAP 加 ZAP）+ 每类 5–6 份人工筛选文档 + 定制提示词。
- 核心分工：**回溯（backtracking）从执行体剥离给编排层**。单 agent 掉进死胡同后难以换假设（Fang et al. 2024 观察到的回溯混乱）；团队把"换路"变成 manager 的调度决策，专家 agent 只专注单类漏洞。
- HTML 减枝省 token（进 agent 前剥掉 img/svg/style 等无关标签）。

**关键数字（[全文精读]）**
- 14 个真实 0-day（全部晚于 GPT-4 知识截止、沙箱复现）：HPTSA(GPT-4) pass@5 42% / pass@1 18%；比无描述单体 pass@1 **×4.3**、pass@5 ×2.0；距"带漏洞描述的 1DV agent"仅 1.8×；ZAP/Metasploit 均为 0%。
- 消融（最强证据）：**去层级结构（随机派发）pass@1 掉 13×、pass@5 掉 6×——编排是最大因子**；去任务专精 pass@1 掉 2.1×、pass@5 −50%；去文档 pass@1 掉 2.1×、pass@5 −20%。
- 开源底座（Llama-3.1-405B / Qwen2.5-72B）0% 成功：拒答率高（llama 31%）+ 反复重复同一错路——**团队放大基座能力，不能替代基座能力**。
- 成本：$4.39/run、$24.4/成功（GPT-4，pass@1 18%）；人工红队估 $75。
- 跨轨迹综合案例：supervisor 从 SQL agent 三次失败轨迹推断出 CSRF agent 应聚焦 /admin.php，"与人类红队复盘同构"。

**保真度判定：缺失（无 subagent 层）**
- CTFPilot 为单进程双通道（fastpath+模型探索），无层级派发结构；假设切换目前由调度循环内联完成。
- 可映射热点：模型探索通道对 token 候选的并行 fan-out——rsi-100 实测 token 阶段占活体题墙钟 76–86%（baby_re/WAF绕过），正是"多假设并行"最值钱的地方。

**本域复现结果：未复现，候选机制**
- 候选：探索通道 k 路并行采样（k=2 起步）+ 编排层跨路综合。门控照旧：对抗时钟显著优于 incumbent + poison 探针不降 + selftest 6/6。
- 反证风险：HPTSA 增益场景是"多漏洞×多假设"宽搜索；我库多数题为单假设链式推理，fan-out 收益预计集中在诱饵密集的 token 阶段，其余阶段只放大成本。

*复验日期：2026-11-17（首录）。下次复验：2027-02-17。*

---

## 7. D-CIPHER — arXiv 2502.10931 [摘要级]（2026-11-17 多 agent 解题批次②）

D-CIPHER: Dynamic Collaborative Intelligent Multi-Agent System with Planner and Heterogeneous Executors for Offensive Security. NYU（Udeshi/Shao/Dolan-Gavitt/Karri 等），2025-02-15（v2 2025-05-10）。

**机制要点**
- Planner-Executor：Planner 全局解题 + 多个**异构 Executor** 各领子任务，动态反馈环。
- **Auto-prompter agent**：观察到失败模式时自动重写其他 agent 的指令（不是简单重试）。
- 立意即"真实 CTF 是专家团队协作"，把人类队伍的角色拓扑搬进 agent 系统。

**关键数字（[摘要级]）**
- 三基准 SOTA（截至发表）：NYU CTF Bench 22.0% / Cybench 22.5% / HackTheBox 44.0%，比先前工作高 2.5–8.5pp。
- 攻克 MITRE ATT&CK 技术点比单 agent 基线**多 65%**。

**保真度判定：部分可行**
- auto-prompter ≈ 我方 case-memory 定向 lesson 注入的强化版；我方 lesson 只进检索，不改写系统提示；无 Executor 池。

**本域复现结果：未复现，候选机制**
- 低配版候选：对"同类题连续 N 次失败"做失败模式聚类，触发提示词定向增强（复用 learn-writeup 产线），不需要多进程即可先测 auto-prompter 思想。

*复验日期：2026-11-17（首录）。下次复验：2027-02-17。*

---

## 8. STRIATUM-CTF — arXiv 2603.22577 [全文精读]（2026-11-17 多 agent 解题批次③，含关键反证）

STRIATUM-CTF: A Protocol-Driven Agentic Framework for Autonomous CTF Solving. FSU，v2 2026-10-29。

**机制要点**
- **不是多 agent**：单 agent（Claude Sonnet 4.5）+ MCP 协议层（schema 硬校验，越界调用在执行前拦截）+ 执行层（Angr/Ghidra/GDB/Nmap/Nuclei 全 MCP server 化）。
- 把"格式幻觉"（编造不存在的工具/参数/slopsquatting）与"功能错误"（合法但语义错，如返回地址偏移错）分开：前者被协议层转成结构化拒绝、agent 自改重发；后者靠环境反馈纠正。
- 控制循环 plan→execute→parse（丢无关字段省 context）→iterate。

**关键数字（[全文精读]）**
- 180 runs / 15 题 / 4 档挂载文档：仅 55 行 schema 77.8% → 4147 行 86.7%，χ²(3,N=180)=1.36 **p=0.71 无显著差异**（对 ~60× 文档量缩减 robust）；剥离 6 道非天花板题后 Baseline−Minimal 差 22.2pp 但仍欠功效（p=0.58）。9/15 题全条件 3/3 全过（题集偏易）。
- FSU 现场 CTF（2025-11-15，30+ 注册队、全人类）**第一名** 215 vs 205/200；单场事件，作者自认"可行性证明而非优于人类专家的统计证明"。优势来源＝机器速度 + 不知疲倦的系统化重试。
- 未防住的失败模式：**hangs——schema 合法的调用 server 不返回，烧光预算；schema 校验管形式不管活性**，需每调用超时 + server 健康检查。
- 反面记录：富文档条件下 operator 观察到 agent "forget things and ignores instructions"——挂载指导越多越挤占对自身状态的注意力（context drift 文献同引）。

**保真度判定：思想同构，规模反证**
- 协议层"执行前确定性拦截"与 fastpath 确定性思想同构；我方 token 尝试缺 schema 级输入硬约束。
- **反证价值**：单 agent + 强工具层即可 77.8–86.7% + 现场冠军；文档量 null result 与 SKALOS 在 121 卡"小规模无收益"同型——**复杂度堆量（团队/技能/文档）在我方规模下增益存疑，机制收益必须消融说话**。

**本域复现结果：未复现**
- 候选（近零成本）：模型通道 token 尝试前加 schema 校验（key 长度/字符集/编码格式约束），预期砍掉一类格式性无效 attempt/假旗；可进下一轮单变量实验。

*复验日期：2026-11-17（首录）。下次复验：2027-02-17。*

---

## 9. D-CIPHER 系模型横评 — arXiv 2604.17159 [摘要级]（2026-11-17 多 agent 解题批次④，边界条件）

Systematic Capability Benchmarking of Frontier LLMs for Offensive Cyber Tasks. 2026-04-18（SIEDS 投稿）。

**机制要点**
- 在 D-CIPHER 多 agent 框架上扩 10 模型 × 7 供应商 × NYU CTF Bench 全 200 题；Kali 环境（100+ 预装工具）+ 运行时工具发现 agent。

**关键数字（[摘要级]）**
- Kali 环境比 Ubuntu **+9.5pp**；auto-prompting 与类别提示在装备良好环境中**常为负收益**。
- Claude 4.5 Opus 59% 最高、Gemini 3 Pro 52%、Gemini 3 Flash $0.05/解最省。
- **非对称 planner/executor 模型组合无显著收益；同模型一致配置稳定优于混合档位配对**。
- 驱动力排序：环境工具层 + 模型选型 >> prompt 工程。

**保真度判定：直接适用（设计约束）**
- 若上多 agent：各角色共享同一基座即可，"强模型当 planner、弱模型当 executor"是无实证的浪漫。
- 环境/工具 >> prompt 的排序与我方 fastpath 投入方向一致。

**本域复现结果：未复现；作为多 agent 方案的约束条件直接采纳。**

*复验日期：2026-11-17（首录）。下次复验：2027-02-17。*

---

## 10. 行业侧二手证据（2026-11-17 多 agent 解题批次⑤；未经一手核验，引用必须带"二手"标注）

- AppSec Santa《The Rise of AI Pentesting Agents》2026-06-10（weibo 转载全文已读）：39+ 开源攻防 agent 归纳 6 种架构模式（单 agent / planner-executor / 角色专精 / 动态蜂群 / MCP 型 / Claude Code 原生）；"多 agent 持续优于单 agent"结论引用即 HPTSA ×4.3 与 D-CIPHER +65% ATT&CK。附带教训：单 agent 上下文窗口被 nmap 类输出挤爆；PentestEval（2025-12）端到端流水线仅 31%、单 agent 框架"几乎彻底失败"；CHECKMATE 用 LLM 写 PDDL 交给经典规划器（+20%、快 50%，"别让 LLM 干它不擅长的长程规划"）；VulnBot 渗透任务图并行独立分支；EnIGMA（ICML 2025）soliloquizing——agent 开始想象输出而不再真执行；角色专精型的级联失败（recon 漏一个服务下游全盲）与并行调用成本放大。
- DARPA AIxCC 2025 决赛（DEF CON 33）：冠军 Team Atlanta（佐治亚理工+三星+KAIST+POSTECH，$4M）＝多智能体 RL + LLM + 符号分析混合 CRS，得分约等于第 2+3 名之和；亚军 Trail of Bits Buttercup＝漏洞发现/上下文分析/补丁生成（7 个 AI agent）/验证四组件；7 套决赛 CRS 全开源；决赛合成漏洞检出率 86%（半决赛 37%）。
- 腾讯黑盲松竞赛实战系列（2026-04，腾讯云开发者社区）：5-agent 并行解题"agent 竞赛"抵消单 agent 方差；四级步数阈值队列（30/50/70/100 步）动态调度；"主攻手-顾问"双 agent（顾问只在开局/连续 3 败等量化时机介入）；长亭 PlanAgent（DSPy 编程式编排替代自然语言 prompting）。
- CAI（arXiv 2512.02654，2025-12）：专用架构 agent 在混合 AI-人类 CTF 赛事夺冠（转引自 STRIATUM-CTF 参考文献 [27]）。

*以上均为二手/摘要级，仅作背景与选题参考，不得单独作为本域采纳依据。*

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
