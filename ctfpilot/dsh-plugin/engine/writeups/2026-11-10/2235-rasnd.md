# rasnd 解题报告（2024 CCB×CISCN Quals · Crypto · 50 分 · 451 队解出）

- **FLAG**: `flag{6ab144ac-5f97-4253-a762-c8675ae5efd1}`（真实数据实测复现，与公开题解一致）
- **耗时**: 约 13 分钟（快通道 0.1 + 攻击推导与本地模拟验证 ~8 + 真实数据复现 1.5 + 题解学习 3.5），解题盒 15 分钟内
- **通道**: 快通道 NOT SOLVED（3.7s，源码题无数据）→ 模型探索通道（纯标准库自研 exp）→ 成功

## 解题链路
1. **crypto2**（秒级）: 指数 n-p-q = φ(n)-1 伪装 → 欧拉定理 a^(φ-1)≡a⁻¹ → X=hint⁻¹ mod n 即 514p-114q（~2^1034<n 无需约减）→ 判别式 isqrt 解 514p²-Xp-114n=0 得 p → RSA 解密 flag2
2. **crypto1**（90s）: H1=x1p+y1q、H2=x2p+y2q → 交叉消元 H1·x2-H2·x1=(x1y2-x2y1)·q 必为 q 倍数 → 枚举 x1,x2<2^11 共 2²² 次 gcd 命中（预计算内层模乘数组提速）→ 分解 n 解密 flag1

## 七维标签
- 题型: crypto/RSA 泄漏 | 载体: py 源码+远程输出 | 识别信号: 指数 n-p-q、hint 小系数线性组合、114514 梗
- 核心技术链: 模逆恢复线性组合 → isqrt 二次方程；交叉消元 → 枚举 gcd 分解
- 陷阱类型: 题解博客多会话数据混贴（exp Given values 与 docstring remote 输出不一致，致 to_bytes 溢出）
- 可迁移模式: 「hint=x·p+y·q 且 x 可枚举 → 交叉乘消 y 得 q 倍数 → gcd(组合,n) 分解」
- 变体预测: x 上界>2^22 枚举失效转 LLL/CVP；y 缩小时可改枚举 y 用 H1y2-H2y1 的 p 倍数关系

## 一得
指数把 φ(n)-1 写成 n-p-q 是"模逆恢复线性组合"的伪装手法；交叉消元产 gcd 是 hint-RSA 通用武器（原理：y 项精确相消，与博客题解只写"爆破 x1,x2"相比补齐了数学机制）。

## 题解学习要点（CSDN chenxing.null 2024CSICN&长城杯 Crypto WP）
- part1 独立推导与题解 exp 完全同路径（枚举 i,j 做 gcd），验证推导正确性
- part2 题解用 sympy 符号解方程组，我改用 isqrt 判别式法——纯标准库零依赖更快，属更优路径
- 题解文字分析（"hint1-hint2=Yq"）表述有误/过度简化，真正机制是交叉消元；exp 代码才是对的
- 教训：博客贴的 exp Given values 与 docstring 输出可能来自不同 remote 会话，数据不自洽时先怀疑混贴

## 失败原因
无实质失败路径；唯一波折是初用博客 Given values 组数据解出溢出明文，识别为多会话混贴后换 docstring 同会话四行数据即成功。无远程靶机（赛事已结束），真实数据取自公开题解，攻击链本地模拟数据亦全程验证通过。

## 产物
- `.temp/solve_rasnd_real.py`（纯标准库 exp，含真实数据与本地模拟两用）
- `.temp/gen_rasnd.py`（无 pycryptodrome 环境的纯标准库 getPrime/模拟数据生成器）

> AI生成
