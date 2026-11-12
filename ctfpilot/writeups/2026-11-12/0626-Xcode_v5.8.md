# CTF 循环报告：2023-zjsdxs Misc-Xcode_v5.8（SOLVED）

- **时间**：2026-11-12 06:26（耗时约 20 分钟，含收尾）
- **题目**：CTF-Archives/2023-zjsdxs/Misc-Xcode_v5.8（决赛 Misc-1）
- **flag**：`DASCTF{The_new_encode_master!!}`
- **通道**：引擎 NOT SOLVED（2.4s，无提示字典/XXencode 检测器）→ 模型补位全链闭合

## 技术链

1. 选题：CCB 18 题穷尽 → zjsdxs 仓库列目录取第一道未做题 Xcode_v5.8（剩 5 题）
2. 附件：zip 经 git blob API（raw.githubusercontent 不通，api.github.com 可用）下载解压 → flag.zip（flags 0x0009 = ZipCrypto 真加密 + 数据描述符，uid=501 macOS 创建）
3. 密码：提示 "Xcode v5.8"（不存在的版本号）→ 产品词×分隔符×版本变形生成 4729 候选 → 纯标准库 zipfile 爆破命中 `Mac_5.8.0`
4. flag.txt（61 字符）`hAXBCEaBZMLZNJbRAQnNlOZdARqFAB1QpKa7IF4F5R1ElBKErIKpFQZNcNEc+`：
   - **XXencode 解码**：表 `+-0-9A-Za-z`；**行首字符 h=45 是行长前缀，必须剥离**；后 60 字符×6bit=45 字节整
   - 得 `23NBceayYVwLs6qjZLwdL475ZbTDdGt415d7QmQrVhe`（字符集无 0/O/I/l → Base58 特征）
   - **Base58** 纯整数大数解码 → `DASCTF{The_new_encode_master!!}`

## 题解学习（官方：xz.aliyun.com/t/13043 AabyssZG）

- 官方路径与本解一致：XXencode（CaptfEncoder）→ Base 系解码 → flag
- 差异：官方用现成工具手点；本解 XXencode/Base58 均纯标准库脚本化可复放；压缩包密码官方未述推导，本解以提示派生字典独立复现
- 漏点检查：无（XXencode 长度前缀、Base58 判型均已覆盖）

## 一得

- **XXencode/UUencode 行首字符是"本行数据字节数"前缀**（h 在表中恰为 45），整串直接解码必错位成二进制——这是本次最大坑点，3 轮变体盲试后由 WP 点破
- 次得一：假版本号提示 = 产品词+版本变形字典（Xcode v5.8 → Mac_5.8.0）

## 固化候选

1. XXencode 解码检测器（含行长前缀剥离 + 45 字节整除判定）——进快通道
2. 压缩包提示词派生密码字典生成器（产品词×分隔符×版本变形）

## 标签

misc / encoding-chain / xxencode / base58 / zip-password / ZipCrypto / hint-dictionary / 长度前缀

## 下轮候选

zjsdxs 剩 5 题（Reverse-Ez8or/apkre/ezRE/ida_pro、Web-ezWEB）、fjgys 剩 4 题、2026-jksn-quals 等

> AI生成
