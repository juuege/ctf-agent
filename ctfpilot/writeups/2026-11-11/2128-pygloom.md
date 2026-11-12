# CTF 20min 循环 · 20261111-2128 · pygloom

- **题目**：CTF-Archives/2026-NCO-Quals `reverse/Rev-简单夺旗-pygloom`（20pts）
- **flag**：`NCO26{py7h0n_0bfu5c4710n_1sn7_m461c}`
- **状态**：SOLVED（模型补位通道，纯静态剥层，全程未执行任何附件载荷）
- **耗时**：21:21 开题 → 约 5 分钟解出 flag → 21:28 落库，总约 8 分钟（15 分钟盒内）

## 过程

1. 选题：CCB 18 题已尽 → NCO-Quals reverse 下未做题剩 pygloom/bitdance/traceweaver，按上轮既定候选顺序取 **pygloom**（attachment.py 3.7KB + description.txt，非 zip；api.github.com 直连配额剩 47，下载成功）
2. recall reverse 无 py 混淆同款卡片 → 引擎 `solve --mode auto` 0.1s 返回 SOLVED `NCO26{.*}`，判定为 **description.txt 字面量占位符假阳性**（第 2 次出现，与 unzip 轮同形态），弃用
3. 补位（纯静态，不 exec）：
   - 识别 `_0(t,k)=chr(v^k)` 模式 → XOR 解出隐藏字符串 base64/b85decode/zlib/decompress/utf-8
   - 复算 `_9(_7)`：29 行矩阵扁平化，逐字节异或密钥 `"pygloom"` + 计数器 `(i*13+37)&255` → 647B base85 文本
   - `b85decode` → `zlib.decompress` → 1620B 明文校验器源码（PyGloom ASCII art banner + 输入比对）
   - 校验器 `_W(t)=((ord^0x33)+i*7)&0xFF` vs `TARGET`(36B)，逐字节逆运算 `chr(((t-i*7)&0xFF)^0x33)` 恢复 flag
4. 验证：前向回验 `_W(flag)==TARGET` 通过 + 格式 `NCO26{...}` 校验通过（脚本复核，非引擎结论）

## 标签

reverse / py混淆 / XOR隐藏字符串 / base85 / zlib / 静态剥层 / 校验器逆运算 / 前向回验

## 题解学习要点

两轮在线检索（题名+赛事+writeup；NCO2026+bitdance/traceweaver 关联词）均无公开题解；仓库 README 仅题目清单、无解法链接；NCO2026 为闭门赛，按既定方案自证 `learn-writeup` 落库（**5 检查点 + 1 技能卡** `2026-NCO-Quals__pygloom-00.json`）。解法为唯一路径：逆运算是双射，无更优解法。

## 一得

1. 引擎假阳性第 2 次（description.txt 中与 `--flag-regex` 模板一致的字面量被当 flag）→ **固化候选**：solve 前将题面 flag 格式占位符脱敏，或引擎端拒绝含 `.*` 的候选 flag
2. py 混淆 exec 剥层（XOR 矩阵+计数器 → base85 → zlib）是可判定的确定性特征 → 列为快通道检测器固化候选

## 失败原因

无

## 下轮候选

- NCO-Quals `reverse/Rev-简单夺旗-bitdance`（20pts，按候选顺序优先）
- NCO-Quals `reverse/Rev-中等夺旗-traceweaver`（40pts）
- 2023-Bluehat-Quals 剩余 7 题（web/reverse/pwn/forensics）

done.json 更新至 80 条。

> AI生成
