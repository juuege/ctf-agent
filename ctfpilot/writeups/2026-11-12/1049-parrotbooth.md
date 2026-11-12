# CTF 单题循环报告 - parrotbooth

- 时间: 2026-11-12 10:49 (Asia/Shanghai)
- 赛事/题目: CTF-Archives/2026-NCO-Quals · PWN-入门夺旗-parrotbooth (5pts)
- 类型: pwn（源码级越界读泄露）
- 耗时: 约 18 分钟（含引擎 112s、题解学习与落库）

## 结论

**利用链完全闭合，flag 需在实例上取得，本轮未取到真实 flag（UNSOLVED-VERIFIED-EXPLOIT）。**

- 二进制保护: ELF EXEC(2) 非 PIE、NX 开、无 stack canary（`__stack_chk_fail` 不存在）。
- 漏洞: `struct { char line[10]; char flag[64]; }`，`read_line()` 循环条件 `while (index < size)` 读满即返回且不写 `\0`；`printf("\nParrot: %s\n", memory.line)` 依赖 NUL 结尾 → 越过 line 直接打印紧随其后的 flag[]。
- 触发条件: 发**恰好 10 字节**（不得少，少则 memset 残留 0 提前截断）。
- 回显形如 `Parrot: AAAAAAAAAANCO26{...}`，正则 `NCO26\{[^}\r\n]*\}` 提取。

## 验证证据

1. 编译产物布局核对（纯标准库字节扫描，无 objdump）：
   `48 8d 45 b0`(=lea rax,[rbp-0x50] &memory) → `48 83 c0 0a`(=add rax,0xa &memory.flag) → `be 40 00 00 00`(size=64)，与源码 load_flag 调用一致；printf 前 `48 8d 45 b0`(line 首地址) 再次印证相邻偏移 +10。
2. 逻辑仿真: `exp.py --sim` 用 bytearray 复现 memset/load_flag/read_line/`%s`，输出 `AAAAAAAAAANCO26{simulated_flag_not_real_...}`，断言通过。
3. 引擎: `solve --mode auto --write-trace` 112.3s / 124 attempts → NOT SOLVED（无 pwn 源码级检测器，属预期）。

## Flag

未取得（无远程 DynamicContainer 实例；边界禁止执行附件 ELF）。交付 `exp.py` 可对任意实例一键复放。

## 一得（本题唯一卡点）

payload 长度必须**精确等于** `sizeof(line)`：memset 已把整个结构体清零，若只发 9 字节，line[9] 仍是 0，`%s` 在 line 内就截断，泄露失败。判断"读函数是否补 \0"只需看循环边界是 `size` 还是 `size-1`。

## 题解学习要点

- 赛事 README 仅列题名/分值，无题解链接；按题名+赛事在线检索无该题公开 writeup（2026 新赛、低热度）→ 判定闭门赛，自证路径即最优，learn-writeup 落库 5 检查点 + 3 技能卡。
- 通用对照：pwn 入门"未终止字符串泄露"家族常见三形态——①本题（struct 相邻字段）②栈上相邻局部变量 ③`printf(buf)` 格式化串。本题为最简形态，无需 libc/ROP，故 `--write-trace` 之外的偏移计算量为零。
- 静态布局反推技巧（无 objdump 环境）：在 .text 内找 `lea rax,[rbp-X]` 后紧跟 `add rax,imm` 的组合，即可在不装 capstone 的前提下确认结构体成员地址，比全量反汇编快一个量级。

## 失败原因

- 环境侧：本题为 DynamicContainer 动态靶机题，归档仓库只给附件，无固定 remote；且技能边界禁止执行附件可执行文件，无法本地起实例读 flag.txt。
- 引擎侧：CTFPilot 无"源码级未终止缓冲区"检测器，deep 的 mock 策略梯只能做魔数/字符串/熵统计，对本题无效。
- 固化候选（下轮评估）：给 fastpath 加一条 C 源码启发式——`struct` 内相邻 char 数组 + 读函数循环含 `index < size` 且无 `buf[i]='\0'` + 后续 `printf("%s", buf)` → 标记"未终止越界读泄露"，直接产出 payload 长度。

## 产物

- `ctf-20min/CTF-Archives__2026-NCO-Quals__PWN-入门夺旗-parrotbooth/exp.py`（纯标准库 socket，含 `--sim` 自检）
- `ctf-20min/CTF-Archives__2026-NCO-Quals__PWN-入门夺旗-parrotbooth/writeup.md`（已 learn-writeup 入库）
- 引擎轨迹：本次 `--write-trace` 未落 `_ctfpilot_trace.json`（NOT SOLVED 时仅写 SQLite trajstore），尝试记录保存在引擎侧数据库。

> AI生成
