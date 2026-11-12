# CTF 循环任务报告 20261112-0414 SSSMMM5 续攻（2023-fjgys Crypto）

## 结论
- **flag：未解出（NOT SOLVED，二攻）**。格引擎已到手（python-flint），消元 150 维格 6 组缩放参数全部 LLL 实测失败——**实测推翻上轮"LLL 理论可解"的判断**，本题两攻定性，下轮不再自动续攻。
- **耗时**：任务总约 20 分钟（选题+引擎搭建 5 分钟、构型推导与 LLL 实测 10 分钟、检索+落库+报告 5 分钟）。
- **选题**：CCB 18 题穷尽 → fjgys 第一道未做题 = SSSMMM5（上轮解构 90% 未标 done，缺 LLL 引擎）→ 本轮带引擎续攻。

## 引擎与环境（固化资产）
- **python-flint 0.9.0**（pip 装 `.temp` 零编译，`fmpz_mat.lll()` 为 FLINT 工业级 LLL，150 维 256-bit 格实测 7~28 秒）+ pycryptodome。本机格攻击能力从此配齐，无需 fpylll/sage。

## 攻击链与实测数据
- 复用上轮解构：`k_i=α(S_i+R_i)−R_i mod n`（α=sk+1），`k_i=X_i·2^137+u_i·2^130+Y_i` 双未知块。
- 消元构型 150 维（100 变量列+49 方程列+常数列）：消 α 用 B_i=A_i/A_1，中心化 X/Y 块（±2^118/±2^129），权重 w1=2^11/w2=1/w4=2^128~130，**per-equation 缩放 s_j=2^129..132** 平衡骨架行。
- 6 组参数 LLL 实测：最短输出行范数 **2^118.5~2^119.4** vs 目标向量 **2^131** → **gap ≈ −12 bits，目标远非最短向量，不可解**。
- MT19937 路线复核：k_i=narrow 直接取原始 256 位（k<n 概率 1−2^-32），泄漏仅 350 bits ≪ 19968 bits 状态需求，死。

## 失败原因（定性与上轮差异）
- 上轮判据错误：`信息富余 94 bits > 需求 2^38` 混淆了**解唯一性计数**与**格 unique-SVP gap**。双块 HNP 每条方程占 2 个坐标（X 119b+Y 130b），坐标成本超过 mod-n 约束收益；同参数单块 MSB-HNP gap +1.4 bits 边缘可行，双块消元构型 gap 为负。本题 50 组在此构型下 LLL/BKZ 边缘不可解（需 BKZ-60+ 或 ≥2 倍签名组）。

## 题解学习要点（无公开题解，第三轮检索仍无命中）
- 按"SS2 中间位泄漏 HNP 格攻击 writeup"再检索，仅命中泛化文章（强网杯 RSA 高位泄露/MT19937 恢复类），确认 2023-fjgys 为闭门赛无公开 writeup，自证路径即最优。
- 通用文献对照确认：中位泄漏双块需 m 显著大于单块（约 2 倍+），本题 50 组对 7 bits 中位泄漏不充分。

## 一得（每题必有）
1. **信息富余 ≠ 格可解**：可解性判据应为 `gap = log2(GH(det,dim)/‖目标‖)`，双块构型坐标成本超约束收益即死。已实测固化。
2. **格构型工程三坑**：①消元方程必须 per-equation 缩放平衡骨架行（否则 Y 变量行 sqrt(2) 污染基）；②mod-n 行值写 `(s*n)%n` 恒为 0，格约束全丢（本轮实际踩中，范数 1 向量秒暴露）；③fmpz_mat 索引 `M[i,j]` 非 `M[i][j]`。
3. **格健康度诊断纪律**：LLL 前后打印最小行范数，正常应接近理论 GH；范数 0/1 即构造 bug，先修格再疑算法。
4. **进度漂移检测**：CTFPilot 库 [345] 显示 `2023-fjgys__Misc-ezudp solved=True` 而 done.json 漏标 → 已补标（done.json 91 条）。多源进度需定期交叉。

## 固化候选
- **python-flint LLL 快速建格脚本模板**（消元+中心化+缩放+健康度诊断+目标扫描）已成型于 `.temp/sss_lattice2.py`，可薄封装为格攻击技能卡。
- learn-writeup 对"续攻/复盘型 writeup"的解析失真（category 误判 misc、techniques 抽出无关 QR 内容）——writeup 模板需明确元数据头，列为引擎改进候选。

## 落库与进度
- learn-writeup：SSSMMM5-fjgys2023 第 2 版已入库（[347]，含格工程三坑与 gap 判据；解析器失真部分以本报告为准）。
- done.json：91 条 = 90 + 补标 ezudp；SSSMMM5 二攻未解出不标，**下轮跳过**。
- 下轮候选：fjgys 剩余（Reverse-double/Reverse-mips2/签到2）、zjsdxs 剩余 9 题（Crypto-R_r/Misc-Ez_Signin/Misc-NewGrating/Misc-Xcode_v5.8/Reverse-Ez8or/apkre/ezRE/ida_pro/Web-ezWEB）、瓷砖计算机 TileGX 解码器。

> AI生成
