# Easy bog (2023-ShenYangCarSecurity-CTF) — 未解出（时间盒）

- **仓库/题名**: CTF-Archives/2023-ShenYangCarSecurity-CTF / Crypto/Easy bog
- **flag**: 未获得（20 分钟编排盒到点强制收尾；技术链完整、归约质量差最后一步）
- **耗时**: 解题盒约 15 分钟（引擎 0.2s NOT SOLVED + sympy LLL 修复后三轮约 80s + Babai）；题解检索 2 轮无公开题解（闭门决赛），赛事 README 无解法链接
- **标签**: [crypto] [subset-sum] [modular-knapsack] [lattice] [LLL] [low-density-0.31] [CVP-Babai] [sympy-floor-bug] [exact-arithmetic]
- **赛题说明**: 附件 task.py+output.txt；flag=flag{rr.hex()}，rr=10 随机字节，m=其 80 位比特串；80 个 [2,p-2) 随机权重 pubkey，c=Σmᵢ·kᵢ mod p（256bit 素数），附 flag_md5 可精确校验候选

## 技术链（全部脚本实证）

1. 判型：模素数背包，密度 d=80/256≈0.3125<0.9408 → Lagarias-Odlyzko 低密度子集和可解；解唯一性由 flag_md5 保证。
2. 构格：嵌入法 82 维（80 个 key 行 (e_i,0,Ka_i) + target 行 (0,1,Kc) + p 行 (0,0,Kp)），K=2^32 缩放约束列强制 LLL 在 s=0 子格内归约——修复后短向量全部 s=0 验证 ✓。
3. **sympy DomainMatrix.lll 大坑（本轮独立发现，检索无公开记录）**：PythonMPQ 未实现 __floor__，math.floor 走 __float__ 浮点回退，256 位大分数四舍五入错位 → 静默输出未归约垃圾基（首向量 norm 2^121）且收尾 AssertionError；小数场景完全正常，极隐蔽。
4. 修复：closest_integer 改用分子分母整数算术 `(2*num+den)//(2*den)` 精确取整 → 36s 出正常归约基（δ=0.75，Norm 从 2^121 降至 ~10²）。
5. 差最后一步：δ=0.75 经典 LLL 实践 Hermite 因子不足，目标向量（norm²≈41 vs GH²≈400）未以基向量浮出；Babai 最近平面对 u=0 子格求 CVP，残差 s=0 ✓ 但 x 分量偏差 max|Δ|≈48，δ=0.99/BKZ/fpylll 时间盒内不可行。
6. 下轮正解路径：fpylll（Windows 无官方 wheel，需 .temp 方案）或 δ=0.99 重规约 + 嵌入行扫描/Babai，预期秒级解出；flag_md5 即答案校验器。

## 一得

1. sympy LLL 大数 floor 浮点回退 bug：任何 DomainMatrix.lll + 大整数输入 = 静默垃圾，规避一行整数取整；候选上报 sympy issue。
2. 固化候选：「模素数低密度背包检测器」——信号=题面给 01 明文/大量随机大权重/单 c mod p → 密度公式 n/log2p<0.9408 → 嵌入格模板 + K 缩放 + Babai 兜底，配套规避版 LLL 脚本（.temp\easybog-solve5.py 即雏形）。

> AI生成
