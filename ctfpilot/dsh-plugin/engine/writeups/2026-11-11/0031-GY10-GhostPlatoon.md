# GY10-GhostPlatoon 解题报告（CTF 循环任务）

- 题目：CTF-Archives/2026-ICSC-ICS-Champ/Crypto/GY10-GhostPlatoon（第十届工控安全锦标赛）
- 时间：2026-11-11 00:31 | 结果：**SOLVED** | Flag：`flag{204d453a575f94cd}`
- 耗时：解题约 8 分钟（含 2 次 EC 实现排错；格攻击核心 <1 秒），题解检索与落库约 5 分钟

## 题目与攻击面
- secp256r1 ECDSA，V2X 编队 BSM 签名 99 条；k 的高 148 位由公开字段（gps_time/counter/veh）经 SHA-256 KDF 重构（含 veh 相关 XOR limb 陷阱），低 108 位未知；5% 签名 k 全随机（中毒，需过滤）。
- 私钥族 d_i = d0 + i·α + i²·β (mod n)，i=0,1,2；flag = flag{ md5(d0‖α‖β‖d3)[:16] }，d3 = d0+3α+9β；密文为 SHA-256 流密码（key/nonce 均由 secret 派生）。

## 解题路径（模型探索通道）
1. 快通道 solve --mode auto --write-trace：NOT SOLVED 23.8s（引擎无 ECDSA-HNP 检测器，预期）。
2. HNP 格攻击：e_j = k_j − high_j·2^108 ∈ [0,2^108)；中心化后 e'_j = A'_j + B_j·d (mod n)。嵌入格（m+2 维，m=6）：n·I_m / B 行 / 中心化 A' 行（末列 K=2^107）。**关键：d 不作为格坐标**，LLL 短向量直接给出全部 e'_j，任一方程反解 d。
3. 滑窗取 6 连续签名/车 → flint LLL（复用 .temp\flint-env）→ d·G==Q 验证 → 三车首个窗口即命中。
4. α = (4d1−d2−3d0)/2，β = d1−d0−α，d3 = d0+3α+9β → secret → md5 得 flag，流密码解密自洽验证通过（解密文 == flag 串）。
5. 事后中毒统计：e_j 越界判别 → 4/99（1+1+2），与名义 5% 自洽。

## 失败原因（复盘）
- 失败路径 1：dbl 公式漏 a·Z⁴ 项（secp256r1 a=−3）→ EC 验证全错，修 E = 3A − 3Z⁴。
- 失败路径 2（真正的坑）：affine 投影把 x 写成 X/Z³（3 次 zi），应为 X/Z²——任何标量乘必在曲线上，用"离曲线断言"快速定位。两次排错合计约 4 分钟。
- 无其他弯路：格构造一次即对，窗口一次即命中。

## 七维标签
crypto / ECDSA-HNP known-MSB-nonce / lattice-embedding-LLL / flint / poisoned-filter-sliding-window / secp256r1 / V2X-BSM

## 每题一得
HNP 嵌入格的正确形态：目标仿射行 + B 行 + n·I，私钥 d **绝不入格坐标**（256 位量级会撑爆目标向量），短向量给出全部小量 e_j 后反解 d——这是与"把 d 放进格"错误写法的本质区别；EC 手写实现必须配"结果在曲线"断言当哨兵。

## 题解学习要点（无公开 wp，自推导 + 同类题型印证）
- 两轮检索（题名+赛事+writeup / 同类关键词）无本题专属公开题解；检索印证同类题型存在（"ECDSA 随机数高位窗口恢复私钥"，2026 能源网络安全大赛 crypto），攻击面判断与业界一致。
- 理论最省：每方程泄漏 148 位，2 条好签名 > 256 位即数学可解；m=6 取冗余换中毒鲁棒性，滑窗+点乘验证是 5% 噪声下最简过滤法。
- 变体预测：L 变小 → 格维可降；无 task.sage 时需先猜 KDF（上下文标签+字段拼接+veh limb）；私钥族升三次 → 需 4 车；s 若负规范（s>n/2 翻转）必须先归一化；高位重构必须带上 veh limb，否则 naive 重构全错（题目明设陷阱）。

## 产物
- 解题脚本：`.temp\ghostplatoon_solve.py`
- writeup 已 learn-writeup 落库：GY10-GhostPlatoon_2026ICSC
- done.json 已更新（第 19 题）

> AI生成
