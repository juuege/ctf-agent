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
- LLL/lattice：格基规约，HNP（hidden number problem）。
- AES：ECB 的块重复检测；CBC 的字节翻转与 padding oracle。
- 哈希题：md5 长度拓展、哈希碰撞脚本、彩虹表本地小字典。
- 椭圆曲线：smart's attack（异常曲线 p=n）、pohlig-hellman（光滑阶）。

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
