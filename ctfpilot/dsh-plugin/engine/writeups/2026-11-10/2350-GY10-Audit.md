# GY10-Audit 解题报告（2026-ICSC 工控安全锦标赛 Crypto）

- 赛题：2026 第十届工业信息安全技能大赛工控安全锦标赛 · Crypto · GY10-Audit（来源 CTF-Archives/2026-ICSC-ICS-Champ）
- 附件：aes_ct.txt（AES-CBC iv+ct 48B）、rsa_pub.txt（n=2047bit, e=65537）、session_ids.txt（5×64bit）、shares.enc（3×~2047bit）
- flag：**NOT SOLVED**（时限盒内未闭合 LCG→key 最后一步）
- 耗时：约 35 分钟（超 15 分钟盒约 20 分钟；超在两个错误假设各耗约 8 分钟——60 排列插值穷举与隐式 CRT/Coppersmith）
- 通道：快通道 solve auto NOT SOLVED 3.2s/70 尝试（无 RSA/LCG/Shamir 检测器）→ 模型探索通道补位 → 3 轮检索无公开题解（2026 新赛事，仅 CSDN 有 Misc 赛道复盘可佐证赛事实名）→ README 题面学习收尾

## 实证发现（核心价值）
1. **session_ids 是 LCG 流**（题面"对随机数理解偏差"实证）：x_{n+1}=2985016781437487305·x_n+7999203871274490253 (mod 2^64)，5 项全部成立。
2. **AES IV 也在 LCG 链上**：= ids 之后第 3/4 个输出，中间恰空 2 个未用输出（0xe18e0b036273ff52、0x1ae1729d164360ef）= 密钥生成时序窗口。
3. shares 三数 < n、两两差 gcd=3；n 无小因子、非完全平方、fermat 2 万轮失败。

## 尝试与失败原因
- 60 排列（5 选 3×3!）整数域/模 n 二次拉格朗日插值：无小值 secret；插值派生 key 解密无命中 → 不是朴素 Shamir。
- 隐式 CRT 假设（n=p1p2p3，S≡share_i mod p_i）：flint LLL Coppersmith 三次小根（m=2/3/4）无根 → 假设不成立（此路为大耗时段）。
- LCG 池 ~26 输出穷举两两拼接 key（BE/LE/hex 串/十进制串/md5/sha256/quad-256/CBC/ECB）：全无命中 → key 生成存在未识别变换（大概率经 RSA/shares 环节，需先分解 n）。

## 题解学习要点（README+检索，无公开 wp）
- 题面三层结构：AES 加密日志 + RSA"备份"关键密钥 + 秘密分享保护 FLAG，弱随机数贯穿始终。
- 最优正解方向（变体预测）：n 的素数由 LCG 流拼接（Java-Random 式 16×64bit 块 nextPrime）生成——由已恢复 LCG 时间线正向重建候选素数即可分解 n，解 3 个 share 得份额材料，还原 AES key 解出 flag。
- 3 轮 online_search + CTF-Archives README 均无该题公开题解；鱼影安全团队博客仅覆盖 Misc 赛道。

## 七维标签
crypto / weak-randomness / LCG-mod2^64 / RSA-key-gen-from-weak-prng / secret-sharing / AES-CBC / audit-forensics

## 一得
附件出现「会话 ID 序列 + 双 64 位 IV」指纹 → 先做 LCG 差分检验（a=(x3-x2)·(x2-x1)⁻¹ mod 2^64，5 项一致性即坐实），成立即双向扩展建时间线、用 IV 落点定位密钥生成窗口——这套动作应在 2 分钟内完成（本题实际 12 分钟后才做，顺序错误是超时主因）。固化候选：LCG-参数恢复+时间线重建器（纯标准库）入快通道。

## 登记与产物
- writeup 已 learn-writeup 落库（GY10-Audit_2026ICSC，crypto，1 checkpoint）
- done.json 已登记 CTF-Archives/2026-ICSC-ICS-Champ/GY10-Audit
- 下轮候选：同仓库 GY10-GhostPlatoon / GY10-TwinModuli（均为 Crypto）

> AI生成
