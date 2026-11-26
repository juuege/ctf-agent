# xor (NewStar CTF 2024 Week1) — 已解出

- **仓库/题名**: CTF-Archives/2024-newstar / Week1 Crypto xor
- **flag**: flag{0ops!_you_know_XOR!}
- **耗时**: 盒内约 3 分钟（选题下载 7 分钟后起算；引擎 0.1s NOT SOLVED + 模型补位脚本秒解）
- **标签**: [crypto] [xor] [known-key] [bytes_to_long] [cyclic-xor] [pwntools-source] [length-reconciliation] [stdlib-only] [sign-in]
- **赛题说明**: "作为2024级新生你应该了解XOR"——签到题，源码给出 key=b'New_Star_CTF' 与两段密文 c1/c2。

## 技术链

1. 源码审计：flag 25 字符劈成两段——前 13 字节 `bytes_to_long` 后与 `bytes_to_long(key)` 单次 XOR 得 c1（大整数异或），后 12 字节与 12 字节 key 循环 XOR 得 c2（字节异或）；两种 XOR 语义混合是本题唯一考点。
2. 还原：`m1 = c1 ^ int.from_bytes(key,'big')` → long_to_bytes → `flag{0ops!_yo`；`m2 = bytes(c^key[i%12])` → `u_know_XOR!}`；拼接 25 字符与占位符 `flag{***...***}` 长度对账一致，前缀 `flag{` 后缀 `}` 校验通过。
3. 引擎 auto 0.1s/4 attempts NOT SOLVED：无 "Python 源码已知 key XOR" 检测器；raw-echo 门控正确拦截源码内占位符 `flag{***...***}`（非逐字 flag，门控运作正常）。
4. 题解对照（博客园荔枝白 NewStarCtf 2024第一周writeup）：解法与 flag 完全一致；差异点：公开 wp 用 pwntools `xor()`，本次纯标准库推导实现同等结果（避免依赖）——无更优路径，无漏技术点。

## 一得

1. **混合 XOR 语义判读**：`bytes_to_long(a) ^ bytes_to_long(b)` 是大整数整体异或（不等长也只对齐低字节），`pwntools xor(key,data)` 是按位循环异或——同题混用时按各自语义分别还原，不可统一按字节处理。
2. 占位符 `flag{***}` 长度（25）= 真实 flag 长度，长度对账是签到题最快自检。
3. 固化候选：Python 源码 XOR 签到题检测器（识别 bytes_to_long ^ + xor() 源码模式 → 提取 c1/c2/key 自动还原）；频次待积累。

> AI生成
