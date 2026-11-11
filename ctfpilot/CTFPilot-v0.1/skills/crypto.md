# crypto 技能卡

## 决策顺序
1. 识别原语：看文件特征（BEGIN PRIVATE KEY / 大整数 n,e / "enc" 后缀 / 神经网络 pickle）。
2. 约束题优先 z3 硬解（sat 求解器，把条件翻译成约束直接解出），不要暴力枚举。
3. 每步产物都过 flag 正则再进入下一步。

## 常见打法（离线）
- RSA 小指数/共模：`python -c` 用 pow、gcd 求解；e=65537 且 n 可分解时直接用 factor.db 本地素数表（若无网则跳过）。
-古典密码：base 家族 / caesar / 维吉尼亚 / 栅栏：先测 IC（重合指数）判断是否替换密码。
- RSA 小素数差：费马分解 `while a*a - n not square`。
- 已知高位/部分密钥：Coppersmith（小根）思路，先手推模多项式再上 z3/sympy。
- LLL/lattice：格基规约，HNP（hidden number problem）。LWE小秘密小误差（题面给 A/b/q/error_bound）同走此路：嵌入基 [I|A^T;0|qI;0|-b|C]，C≈秘密量级，样本≥n+8 即可、先少量LLL再全量复核，LLL后取末位非零行按符号翻转得 s；d≥70 一律 python-flint `fmpz_mat(d,d,flat).lll()`（sympy 同格 d73 实测 198s，flint 秒级）。
- AES：ECB 的块重复检测；CBC 的字节翻转与 padding oracle。
- 哈希题：md5 长度拓展、哈希碰撞脚本、彩虹表本地小字典。
- 椭圆曲线：smart's attack（异常曲线 p=n）、pohlig-hellman（光滑阶）。
- **ECDLP 多子群拼钥**：同一 secret 在多条小阶子群点 Q_i=d·G_i 上公布 → 各子群 BSGS（m=⌊√n⌋~8k~16k）得 d_i → 广义 CRT（阶可能不互素，用 g=gcd 版）还原 d，区间必为 [0,∏n) 内唯一；d 直接当 AES 口令 sha256(str(d))[:16] 用时无需再验范围。

## z3 模板（约束题）
```python
from z3 import *
s = Solver()
x, y = Ints('x y')
s.add(...)  # 题面条件逐条翻译
print(s.check()); print(s.model())
```

## 陷阱
- "看起来是 base64" 但字符集异常 → 先 print len、charset。
- 伪加密 zip：直接改 flag 位（PK 头后 CRC/加密标志位），无需密码。
- 密码学题先看题名：名字常常直接给算法提示。

## RSA known-MSBs → Coppersmith (Howgrave-Graham, flint LLL)
- 适用: 已知素因子高 276/512 位（unknown < N^0.25），f(x)=x+P 单变量。
- 格: g_i=N^{m-i}(xX+P)^i (i=0..m), h_j=(xX)^j(xX+P)^m (j=1..t); d=m+1+t。
- 参数: unknown=236/N=1024 时 m=8,t=10 一发命中; 更紧时升 m=10~12。判界: [m(m+1)/2·log2N + S_X·log2X]/d+(d-1)/4 < beta·m·log2N。
- 求根: LLL 后短向量除 X^k 得整系数多项式, 粗扫 2^13 段符号变化 + 二分找整数根, 用 N%(P+r)==0 收根。
- **陷阱: 禁用 Fraction 手写 LLL（d=19、8192 位格 >7min 跑不完）; pip install python-flint --target .temp 后 fmpz_mat(d,d,flat).lll()，索引 L[i,j]，秒级。**

## LFSR OR/AND 组合器 keystream 恢复 (GF(2) 线性化, 免暴力)
- 信号: 双 LFSR + OR/AND 组合 + 已知明文段（挑战串），seed ≤ 40bit 时禁 2^seed 暴力。
- 原理: OR 下 out=0 强制 b1=0∧b2=0（AND 下 out=1 同理）; Galois LFSR 状态转移是 GF(2) 线性, 输出位 b_t(s)=parity(c_t & s)。
- 步骤: 基向量 e_j 跑 T^t 轨迹收集系数 c_t → 齐次方程组高斯消元 → 零空间枚举 → keystream 全长验证定 seed。
- **陷阱: 派生分支 s2=s·K mod 2^32 有进位、非 GF(2) 线性, 方程只能取线性分支（必要条件）, 解空间小直接枚举; GF(2) 消元主元必须去重。**
- 变体: 掩码与挑战串同流（新实例同 seed）时, 挑战段 ks 可直接复用为掩码前缀。

> AI生成
