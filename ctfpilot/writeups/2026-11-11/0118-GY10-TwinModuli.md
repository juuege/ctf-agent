# GY10-TwinModuli 解题报告（2026-11-11 01:18）

## 结果
- **Flag**: `flag{a163f947a3efa4c4}` —— **SOLVED**（本地确定性求解，RSA 明文双重验证一致）
- **来源**: CTF-Archives/2026-ICSC-ICS-Champ · Crypto/GY10-TwinModuli（第十届工业信息安全技能大赛工控安全锦标赛）
- **耗时**: 约 11 分钟（快通道 6.4s NOT SOLVED → 模型补位约 10 分钟：其中 Fraction LLL 超时浪费 ~7 分钟，正确路径 stage1+stage2 合计 <1 分钟）

## 技术链
1. **keystream 恢复**: 已知明文 "IVI-TBOX-SYNC-OK" XOR 挑战密文得 16 字节组合器输出流
2. **GF(2) 线性攻击免暴力**: OR 组合器 0 输出位强制 b1=0∧b2=0；Galois LFSR 状态对 seed 是 GF(2) 线性 → 30 个齐次方程 rank 29 → 零空间仅 8 候选 → 验证得 seed=0x38a95dbe
3. **解掩码**: 同 seed 新实例 keystream(35) XOR p_high_masked → p1 高 276 位
4. **Coppersmith**: f(x)=x+P，unknown=236<N^0.25=256，m=8/t=10/d=19 格，python-flint LLL 秒级 → p1 → q1=n1/p1
5. **出 flag**: flag=md5(p1||q1)[:16]；RSA 解密 c_rsa 得 `OTA|LHGAV3850A2012345|flag{a163f947a3efa4c4}` 交叉验证 ✓

## 七维标签
题型 crypto / RSA known-MSBs；技术 GF(2)-线性化 LFSR 组合器、Coppersmith Howgrave-Graham、已知明文攻击；陷阱 delta_hint 红鲱鱼（q 独立无共因子）、s2 乘法进位非线性、Fraction LLL 性能陷阱；工具 python-flint fmpz_mat.lll；变体 AND 组合器同法更约束、seed 扩位仍可用；固化 flint-LLL Coppersmith 模板 + LFSR 组合器线性化卡（已入 skills/crypto.md）

## 一得
OR/AND 组合 LFSR 的线性化攻击：零（一）输出位约束下凡对 seed 线性的分支都产生 GF(2) 方程，非线性派生分支（乘法进位）丢弃后小解空间枚举即可，免 2^32 暴力；大整数格 LLL 一律 flint，禁 Fraction 手写。

## 失败路径（复盘）
1. 初版把 s2=s·K mod 2^32 当线性 → 60 方程 rank32 唯一零解，真 seed 被排除（乘法进位破坏线性性）
2. GF(2) 消元 pivots 未去重 → rank 58>32 假象
3. Fraction 教科书 LLL 在 d=19、8192 位整数格上 7 分钟未完成 → 换 flint 秒出（固化项）

## 题解学习
- 2026 新赛事无公开题解（搜索 0 命中，与 Audit/GhostPlatoon 一致）；仓库 README 题面自证方向："也许差的不只是一个 δ"（delta 红鲱鱼）+"诊断通道里的噪声"（LFSR 掩码 keystream）——与本解法完全吻合
- 对照出题注释（task.py 内 red herring 自注）确认：共因子攻击、p2-p1=δ 单行分解均不可用，唯一路径 = keystream 恢复 + Coppersmith，本解未漏技术点
- writeup 已 learn-writeup 落库（GY10-TwinModuli-00.json）；done.json 已登记至 21 题

> AI生成
