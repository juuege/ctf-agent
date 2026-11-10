# 例题writeup：小素数差的RSA

## 题目描述
给出 n, e, c 三个大整数，n 为 1024 bit。提示：出题人偷懒，p 和 q 很接近。

## step 1: 判断素数接近
当 p、q 接近时，n = p*q 可用费马分解：从 ceil(sqrt(n)) 开始向上枚举 a，直到 a^2 - n 是完全平方数。

## step 2: 费马分解脚本
```python
import gmpy2
from math import isqrt
def fermat(n):
    a = isqrt(n)
    if a*a < n: a += 1
    while True:
        b2 = a*a - n
        if gmpy2.is_square(b2):
            b = gmpy2.isqrt(b2)
            return a-b, a+b
        a += 1
p, q = fermat(n)
```

## step 3: 求私钥并解密
```python
d = pow(e, -1, (p-1)*(q-1))
m = pow(c, d, n)
print(bytes.fromhex(hex(m)[2:]))
```

## step 4: 拿到flag
解出明文即 flag{f3rm4t_f4ct0r1z4t10n}。

## 备选方法
- 如果素数差不小但 n 多个共享因子，考虑批 GCD。
- 已知部分密钥位用 Coppersmith 小根方法。
- RSA 低指数广播攻击用 CRT 合并。
- 更复杂的约束关系可以直接丢给 z3 求解器。

## 总结
RSA 小素数差 -> 费马分解；先看 n 的 bit 长度和素数间距再选路线。
