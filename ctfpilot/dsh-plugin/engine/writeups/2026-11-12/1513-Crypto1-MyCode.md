# 2024-zjsdxs-Finals Crypto1 MyCode — SOLVED

- 时间：2026-11-12 15:13（约 13 分钟，单题时间盒 15 分钟内）
- 来源：CTF-Archives/2024-zjsdxs-Finals Release（Crypto1-MyCode.zip，939B）
- 题目：自定义 4 字节分组密码，10 轮 nibble S-box + 滚动轮密钥异或，密钥 4 字节，密文 44 字节
- Flag：`DASCTF{6ef4d8e1-845a-4e3c-a4e1-a15e5530a0f4}`
- 密钥：`0xECB4F678`
- 标签：crypto/bruteforce/known-plaintext/custom-block-cipher/32-bit-key/LFSR-key-schedule/C#-Parallel/Add-Type

## 解题路径
1. 源码确认分组 4 字节且密钥 32 bit，已知明文首块 `DASC` 与密钥空间等长。
2. Windows 无 C 编译器，用 PowerShell Add-Type 即时编译 C#，Parallel 4 核全空间爆破；逐 key 仅算首字节 10 轮，命中后验全块，122 秒得 3 个候选。
3. 按源码逆 S-box 解密全密文，`ECB4F678` 得完整 flag，另两候选为不可打印碰撞。

## 一得
自研分组密码先找"分组长度=密钥长度"的已知明文块；源码占位符 `key = 0xECB...` 泄露高 12 bit，本可只爆 2^20，爆破前必须先翻注释/占位符找密钥位泄漏。

## 题解学习要点
- 赛事 README 无解法链接；两轮在线检索未找到 Crypto1 公开 writeup（同场仅有 Misc WP）。
- 自证复盘：全空间爆破可行但非最优；优化顺序应为 ①占位符缩空间（2^20）②首字节早筛 ③全密文可打印性甄别碰撞。
- 引擎 false positive：`solve` 0.0s 报 `CTF{******}`，实为源码中的假 flag 占位符，必须脚本复核（技能"引擎假阳性需脚本复核"再次验证）。

## 失败原因
无失败；主要耗时在 2^32 全空间爆破（122s），未利用占位符缩空间。

> AI生成
