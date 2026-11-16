# 2024-chb Mypow — SOLVED

- 时间：2026-11-13 15:47（解题约 13 分钟，单题时间盒 15 分钟内）
- 来源：CTF-Archives/2024-chb attachments/crypto/Mypow的附件.zip（1019B）
- 题目：Genp 令 p-1、q-1 均含因子 672=2^5·3·7；hint=2p+7q 未取模；自定义 Mypow 先右移再平方判位，实际指数=e-(e&1)=672
- Flag：`DASCTF{FastP0w3r_4nd_AMM_0f_R5A}`
- 标签：crypto/RSA/buggy-fastpow/exponent-shift/hint-linear-combination/quadratic-factor-recovery/smooth-p-1/nth-root/AMM/CRT/纯标准库

## 解题路径
1. hint 直给 2p+7q：由 7n=p(hint-2p) 得 2p²-hint·p+7n=0，判别式恰为 (2p-7q)²，isqrt 整除即得 511-bit p、q。
2. 审 Mypow：`e>>=1` 位于平方与判位之前 → 累乘指数 Σ_{i≥1} bit_i·2^i = e-(e&1)；e=673 为奇素数，故 ct=m^672。
3. gcd(672,p-1)=672，开根不唯一：纯标准库实现——p-1 拆成 S(2,3,7 部分, p 侧 2016)+s'，s' 分量用模逆直开，S 分量在 2016 阶子群爆破出全部 672 个 672 次根；每个 p 根验 `pow(r,672,q)==ct%q`。
4. 唯一命中即 m（62B=30B 随机+32B flag），`pow(m,672,n)==ct` 复核通过。

## 一得
buggy fastpow 的"先移位后判位"等价于指数减 1（奇数 e）；Genp 的光滑因子恰等于该指数时必然退化为开根题——纯标准库用"Sylow 拆分 + 小群爆破"即可替代 Sage 的 f.roots()。

## 题解学习要点
- 公开题解：CSDN《2024楚慧杯WP》（blog.csdn.net/CLAY0011/article/details/144651513）含 Mypow 完整解法。
- 对照：题解用 Sage `f=x^e-ct; f.roots()` 一次取全部根，比手写 AMM 少 ~30 行；因子恢复写 2x²-hint·x+7n=0 求根，与本文判别式等价但更直观。
- 可省步骤：过滤只需 `b'DASCTF' in flag`；先用 p 根验 mod q 可免 45 万次 CRT 组合。

## 失败原因
无失败；耗时主要在无 sympy/Sage 环境下自写 Sylow 枚举与开根。

> AI生成