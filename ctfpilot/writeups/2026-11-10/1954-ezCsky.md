# ezCsky（2024 CCB×CISCN Quals · Reverse）解题报告

- **结果**：✅ SOLVED
- **flag**：`flag{d0f5b330-9a74-11ef-9afd-acde48001122}`
- **耗时**：快通道 3.3s 未解出；模型探索通道约 50 分钟（造 C-SKY v1 反汇编器约 35 分钟 + 解题 15 分钟），超 15 分钟盒。
- **标签**：reverse / csky-v1 / ck610 / rare-arch / rc4 / xor-chain / known-plaintext / literal-pool / elf-eflags / self-built-disassembler

## 题目流程（静态逆向还原）

ELF32，e_machine=39（旧 C-SKY）、e_flags=0x10000002 → CSKY_ARCH_610 + ABI_V1，纯 16 位 LE 半字 ISA：

1. `memcpy` 从 rodata@0x8aa0 拷 42 字节密文
2. `read(0, input, 42)`，`strlen(input)` 必须 = 42，否则 `error1`
3. `memset(work, 0, 42)`
4. `check(input, work)`：`work[i] = input[i] ^ input[i+1]`，i=0..40（41 字节）
5. `rc4_init("testkey", 7, S)`、`rc4_crypt(work, 42, S)`（标准 RC4，全 42 字节）
6. `cmp_result(work, ct)` 逐字节比 41 字节，不等输出 `error2`，全等输出 `You enter a true flag!`

## 解法

W = RC4 解密密文前 41 字节 → `input[i+1] = input[i] ^ W[i]`，用已知前缀 `flag{` 作种子前向异或链展开即得全文；重加密 41 字节与密文完全一致（`}` 也经 W[40] 链验证）。密钥 `testkey`、密文地址等全部从字面池（lrw target = ((pc+2)&~3)+imm*4）确定性读出。

## 每题一得

无工具罕见架构的最短路径 = api.github.com raw 拉 binutils `opcodes/csky-opc.h` 编码表造纯 Python 反汇编器（解析坑：SOPCODE_INFO 前缀、IMM5b 小写 b、宏族 OP16_OP32/DOP16、mask=~位段）。C-SKY V1(ck610) 是纯 16 位定长 ISA，无 v2 的 (hw&0xc000) 32 位判别；识别看 e_flags：0x1F 位段里 0x2=ck610、bit28=ABI_V1。

## 题解学习（对照 CSDN xccwxy《2024年ccbciscn竞赛部分Wp》）

- 官方思路：RC4 解密后见尾字符 `}`，**从后向前**异或得 flag；我从 `flag` 前缀**从前往后**异或，双向互验一致。
- 更优路径评估：官方用 IDA（C-SKY 处理器模块）直接看主函数，几分钟定位；我的纯手造工具链慢但零依赖、可固化复用。若先搜到该 WP 可省 30 分钟——但"必学题解"在收尾环节才检索，本题现场没走到该分支。
- 补齐技术点：cmp 只比 41 字节（第 42 字节不校验），重加密验证时 enc[41]=0xac ≠ ct[41]=0xd1，与 check 只写 41 字节、work[41] 恒 0 的实现自洽。

## 失败原因与改进

- 失败轮次：①快通道无 C-SKY 检测器；②误判为 C-SKY v2（半字拼接方向错 2 版）；③2 轮 RC4 密钥组合盲试（违反速度纪律，及时止损转搜索）。
- 固化成果：`CTFPilot-v0.1/skills/csky_dis.py`（自包含反汇编器）+ `skills/csky-opc-tables.h` + `skills/csky_example_solve.py` 已入库；后续 C-SKY 题直接调用，零思考成本。
- 变体预测：改密钥/密文只需换参重跑；v2(ck810) 版本把表切回 v2 并恢复 32 位半字判别；尾向异或链用 `}` 作种子反向解。

> AI生成
