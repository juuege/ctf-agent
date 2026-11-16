# 0x100 (2024-LanqiaoCup-Finals) — 已解出

- **仓库/题名**: CTF-Archives/2024-LanqiaoCup-Finals / 0x100
- **flag**: flag{2b0fbc1c-ec8a-4d9b-bea4-b8a83cff52a3}
- **耗时**: 盒内约 3 分钟（下载解压 1 分钟 + 引擎 2.3s NOT SOLVED + 脚本秒解），题解检索 2 轮无公开题解（闭门决赛）→ 自证落库
- **标签**: [crypto] [rsa] [e=3] [cube-root] [ljust-zero-pad] [strip-known-factor] [brute-force-length] [stdlib-only]
- **赛题说明**: 小蓝学 RSA，把原文给移位后再补 0，能解密出原文吗

## 技术链（全部脚本实证）

1. 题面 `plain = bytes_to_long(flag.ljust(114514, b"\x00"))`、e=3、n=1023bit → c=(m·256^k)³ mod n，k=114514−len(flag)，m 为去补零后的 flag 整数。
2. 补零在 big-endian 右侧 = 整数左移 8k 位，移位因子 2^(24k) 已知：c_k = c·(256^(−3k) mod n) mod n = m³ mod n。
3. 逐 L∈[4,80) 爆破 k 剥因子后取整数立方根（pow 负指数模逆 + Newton）；L=42 命中精确立方根，验证 m³=1008bit < n=1023bit 无模约减，解码 42 字节得 UUID flag。
4. 边界验证：立方根法成立条件 L ≤ ⌊n.bit_length()/3/8⌋ = 42，恰好等于真实 flag 长度；L≥43 时 m³>n 需转 Coppersmith。
5. 引擎 `--mode auto` 2.3s/60 attempts NOT SOLVED（无 RSA 检测器，预期）；recall 无同型案例 → 模型补位通道，纯标准库 15 行脚本。

## 一得

1. **"移位补零"RSA 通式**：LSB 端补零即 m·2^t，已知因子先剥离再做整数立方根，比直接上 Coppersmith 更快且零失败率；判定式 `m³<n ⇔ L ≤ n.bit_length()//24`。
2. Python 3.8+ `pow(base, -exp, mod)` 原生支持负指数模逆，免手写扩展欧几里得。
3. 固化候选：RSA 低指数 LSB 补零剥离立方根检测器（识别信号：ljust/zfill + e≤5 + c 与 n 同位数）。

> AI生成
