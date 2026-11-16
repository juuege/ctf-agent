# Crypto1 (ctf_challenges) — 已解出

- **仓库/题名**: CTF-Archives/ctf_challenges / crypto/Crypto1
- **flag**: flag{8ea44e39c914c5ddfbb9808c10033421}
- **耗时**: 盒内约 3 分钟（引擎 2.2s NOT SOLVED + 脚本秒解）；题解检索 1 轮无专属公开题解（练习库 Readme 即官方解法，与自推链一致）
- **标签**: [crypto] [multi-stage-encoding] [hex] [html-numeric-entity] [base64] [ascii-decimal-codes] [practice-bank] [stdlib-only]
- **赛题说明**: 题目描述"多次解码"（难度⭐），附件 decode.txt 单行 hex

## 技术链（全部脚本实证）

1. decode.txt 为 0x 前缀单行 hex（1712B）→ bytes.fromhex 解出 `&#77;&#84;...` HTML 数字实体流。
2. 正则 `&#(\d+);` 取码 chr 拼接得 base64 串（含换行）；Readme 把此步称"unicode解码"，实为 HTML 十进制实体解码。
3. b64decode 得 `102\n108\n97\n103\n{...` 换行分隔的十进制 ASCII 码表。
4. 逐码 int→chr 得 flag{8ea44e39c914c5ddfbb9808c10033421}，与仓库 Readme KEY 一致（从附件推导，非 raw-echo）。
5. 引擎 auto 2.2s/34 attempts NOT SOLVED：chain-decode 8 深度不含 HTML 实体（&#NN;）解码环节 → 模型补位通道，纯标准库 4 步脚本秒解。

## 一得

1. **hex 解出 `&#\d+;` 流 = HTML 数字实体环节**：题面"unicode"命名有误导性；先认 &# 十进制实体再认 \uXXXX。
2. 固化候选：HTML 数字实体链式解码环节（hex→&#NN;→b64→十进制码→chr），可并入 chain-decode 中间层，属可判定确定性特征。

> AI生成
