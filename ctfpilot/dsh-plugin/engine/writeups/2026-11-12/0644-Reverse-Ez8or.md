# CTF 20min 循环 — 2023-zjsdxs 决赛 Reverse-Ez8or SOLVED

- **Flag**: `DASCTF{5ucc355_wa1king_Fr0m_2fai1ur3_with_n01055_3nthu5ia5m}`（正向加密重放 60 字节全匹配）
- **耗时**: 引擎 3.4s NOT SOLVED → 模型补位约 9 分钟闭合，收尾约 6 分钟，总 15 分钟盒内完成
- **通道**: 快通道 miss（无 ELF 逆向检测器）→ 模型补位（WSL objdump 静态反汇编 + 纯标准库键流重放）
- **标签**: reverse, anti-debug-ptrace, static-objdump, xor-keystream, movzbl-sar-trap, per-group-increment, replay-verify
- **一得**: `movzbl` 零扩展读局部变量后 `sar` 退化为逻辑移位——手推密钥流按 signed char 算术右移会得差 0x80 的错误流；判据：解密结果 p=0/p=5 正确、其余恒差 0x80 → 定位符号位错误（本题 0x80 恰是移位后的符号位掩码）。花指令只挡 IDA F5，objdump 线性扫描直接穿透。
- **题解学习要点**: CSDN DreamAndSun 同 flag 同本质路径（key1 递增 XOR + key2=(i-57) 移位流）；其流程 = IDA nop ptrace → 修花指令 → F5 → 展开 a1/a2 数组逐字节 XOR。对照：我的静态重放免执行二进制更合规（题解作者备用思路需 gdb 动态调试）；无漏掉技术点，60 字节即 flag 全长（题解称输入 72 数组与 60 密文比较）。writeup 已 learn-writeup 入库（1 检查点+2 技能卡）。
- **失败原因**: 无（一次闭合）。
- **进度**: done.json 99→100；zjsdxs 剩 4 题（Reverse-apkre / ezRE / ida_pro、Web-ezWEB）；CCB 已穷尽，下轮继续 zjsdxs 或探测 CTF-Archives 其他仓库。

> AI生成
