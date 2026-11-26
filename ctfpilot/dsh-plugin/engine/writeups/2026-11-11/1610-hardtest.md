# 2025-WQB-Quals / reverse / hardtest — 解题报告

- 时间：2026-11-11 16:03 起，单题用时约 3 分钟（20 分钟盒内）
- 仓库：CTF-Archives/2025-WQB-Quals
- 题目：attachments/reverse/hardtest/8486c393ef8744a2989e60540644c788.zip（3.7KB，解压得 14KB ELF64）
- 通道：模型探索通道（静态逆向；未执行附件）
- Flag：`flag{Bl@st1ng_1s_a_g00d_Way!!}`

## 结果
SOLVED。目标数组 24 字节完整还原为 ASCII，且按原变换重加密后与 `byte_2120` 逐字节一致（强验证）。

## 技术链
1. `srand(time(NULL))` → `rand()%255+1` 作为数字门禁；可用 255 次暴力或按 time 预测，与 flag 无关。
2. 第一轮 sub_1492：`buf[i] = rol8(input[i], i%7+1)`。
3. 第二轮 sub_13E1：`xor 0x5A → rol8 3 → 半字节 hi*3/lo*5 (mod 16) → x^255 mod 257（模逆）→ ror8 2 → AES S-box`。
4. 逆向顺序：S-box 值查索引 → rol8 2 → 模 257 求逆 → 半字节 *11/*13 → ror8 3 → xor 0x5A → ror8(i%7+1)。

## 标签
识别信号=AES S-box 256B 连续表 + 单字节数字门禁 + 静态目标数组；陷阱类型=模数混淆（257 而非 0x11b）、逆变换顺序、S-box 字节被误当密钥；可迁移模式=“63 7c 77 7b 开头即 S-box，xor+rol+查表链逆序反演”；变体预测=换 S-box/改旋转位数/嵌 TEA/目标运行时解密。

## 一得
`imul 0x7f807f81` + `q*257` 是 mod 257 的标志，不是 GF(2^8)（0x11b）；`x^255 mod 257` 就是求逆，反变换只需再算一次。

## 题解学习
CSDN Xun_M《2025年“湾区杯”网络安全大赛部分WriteUp》hardtest 节确认同一技术链：IDA 反编译 → 识别两轮变换 → S-box 逆查 → 还原 24 字节。其半字节逆用 16×16 枚举，本解用乘 11/13 直接求逆更短；数字门禁可暴力/预测的结论一致。无更优路径遗漏。

## 失败原因
无（一次解出）。唯一弯路：初读 0x1313 曾误判为 GF 乘法，靠魔数常量纠正。

## 附件与脚本
- 题目：`ctf-20min\2025-WQB-Quals__hardtest\unzip\hardtest`
- 解题脚本：`ctf-20min\2025-WQB-Quals__hardtest\solve.py`
- 反汇编：`ctf-20min\2025-WQB-Quals__hardtest\disasm.txt`

> AI生成
