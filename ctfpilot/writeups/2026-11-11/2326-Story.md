# CTF 20 分钟刷题报告 — 20261111-2326 — Story

## 结果

- **题目**: CTF-Archives/2023-Bluehat-Quals · reverse · Story（52 分，第七届蓝帽杯初赛）
- **flag**: `flag{WhatisYourStory34982733}`
- **状态**: SOLVED（纯静态，未执行任何可执行文件）
- **耗时**: 选题 ~6min + 解题 ~4min + 收尾 ~3min ≈ **13min**（20 分钟盒内；单题解题 15 分钟盒内）

## 选题与弃题记录

1. NCO-Quals 按候选取 pwn/parrotbooth → 下载解压含 src.cpp，读源码推明解法（read_line 读满 10 字节无 null 终止 → printf("%s") 越界连打相邻 flag 字段，1 行 exp），但**DynamicContainer**：附件无 flag.txt、真实 flag 在远程容器，本地不可解——与「超级无敌保险箱」先例同性质，**弃题**。验证 NCO 剩余 pwn×3、web×4 全为 DynamicContainer，NCO 夺旗题已全部做完。
2. 转 2023-Bluehat-Quals（done 中仅 DHRSA）→ 按序 misc/ez_Forensics 为外链内存镜像（跳过）→ **reverse/Story**（带 zip 附件）入选。

## 解法（模型探索通道）

- 引擎 fast 通道 0.0s 假阳性（把源码字面量 `flag{"<<s<<number<<"}` 当 flag，**第 3 次**），脚本复核弃用 → 转模型通道纯源码审计。
- **01-Trie 语义分叉**: `Trie_query(89149889)` 返回最大异或值 x=122002060（用于 srand）；"树上哪个值"= argmax 原值 number=34982733（用于拼 flag）。
- **PRNG 复现**: srand(122002060) + MSVCRT rand（seed*214013+2531011, (seed>>16)&0x7fff）+ libstdc++ random_shuffle 前向迭代 → 打乱 base64 表。
- **闭环确证**: 用打乱表对 "WhatisYourStory" 编码，精确命中程序内置校验串 "fagg4lvhss7qjvBC0FJr" → s、x、number 三环节互证，flag 真实非猜。
- **混淆点（SMC）**: 运行时 VirtualProtect 后将 Trie_build~Trie_query 代码段逐字节 XOR 0x20（磁盘态函数体乱序、反静态反汇编）；本题因附带源码绕过，只有 exe 时需 dump 还原。

## 题解学习要点（收尾 ~5min）

- 检索两轮命中 CSDN《2023蓝帽杯初赛》复盘：作者直读源码注释（`//string flag="WhatisYourStory"`、`// number = 34982733`）秒出 flag，结果一致。
- **对照结论**: 公开题解更快但依赖注释善意；本路径第一性复现可抗注释删改变种，且补齐了题解未覆盖的 rand/random_shuffle 复现细节与 SMC 混淆点。

## 标签与一得

- **标签**: reverse, src-audit, trie-xor-max, prng-replay, msvcrt-rand, random_shuffle-replay, custom-base64-table, smc-selfpatch, engine-false-positive-literal
- **一得**: ①固化候选——Windows PRNG 复现模板（msvcrt rand + random_shuffle 前向/后向两种迭代序 + 打乱表编码验证）；②引擎假阳性第 3 次出现，固化候选升级：solve 前脱敏源码/题面中的 flag 格式字面量；③DynamicContainer 快速判据：附件缺 flag 输入源（flag.txt/Dockerfile/env）即弃。
- **落库**: writeup 已 learn-writeup 入库（1 检查点 + 技能卡 Story-2023Bluehat-00.json）。

## 失败原因

无。唯一注意点：parrotbooth 弃题消耗 ~4 分钟选题时间，后续 NCO DynamicContainer 题应直接按判据跳过。

## 下轮候选

- Bluehat: reverse/瓷砖计算机（TileGX 架构 1000 分，硬骨头）、pwn/heapSpary、pwn/takeway、web/MyBrowser
- 注意：Bluehat pwn/web 题多为交互题，附件静态部分可分析但真实 flag 需远程——选题前先验 DynamicContainer 同类性质。

> AI生成
