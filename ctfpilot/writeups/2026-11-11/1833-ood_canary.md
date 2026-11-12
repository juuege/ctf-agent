# CTF 20min 循环 · 20261111-1833 · ood_canary

- **题源**: CTF-Archives/2025-WQB-Quals `pwn/ood_canary`（候选探测：主仓 2024-CCB-CISCN-Quals 18/18 已尽；qwbs10 仅 README；WQB 剩余题中 web 3 道无附件、forensics 附件外链过期、imi_mqtt 网盘不可用，ood_canary 为第一道有本地 zip 的未做题，950KB）
- **flag**: 未实测获取——比赛环境已归档且本任务禁止执行附件可执行文件；技术链完整闭合并经官方题解交叉验证（getshell 后读 /flag）
- **耗时**: 约 16 分钟（选题下载 2min；引擎 NOT SOLVED 1min 预期；模型补位静态分析约 11min；题解学习与收尾约 4min，20 分钟盒内）
- **通道**: 引擎 auto/deep NOT SOLVED（pwn 超快通道边界）→ 模型补位静态分析（Python 解析 ELF + WSL objdump 反汇编）解出
- **标签**: pwn / canary-bypass-init_array / fs:0x28置零 / 信息泄露-相邻全局变量 / ret2libc / one_gadget / 栈迁移leave_ret / strncmp前缀限制 / Ubuntu22.04-glibc2.35

## 解法链（自主推导，官方题解三点吻合）

1. `before_main`（.init_array 第 2 条目）在 main 前把 `fs:0x28` 写 0 → **全局 canary 固定为 0**——题眼 "Don't always trust the canary"；vuln 溢出时偏移 0x28 处填 `\x00`*8 即过 `__stack_chk_fail` 检查（canary_backup@rbp-0x38 在 read 范围外，恢复值恒为 0）
2. 泄 libc：`good_news` 中 `bss[0]=puts@GOT` 与 8 字节 `name` 相邻；read 0x20 非零字节 + `strncpy(name,buf,0x20)` 填满 name → 下轮 `printf("...%s",name)` 越界连读 `bss[0]` → libc base = leak − 0x80e50
3. `vuln`: `read(0, buf, 0x40)` 溢出 64 字节但 `strncmp(buf,"exec",4)` 强制前缀；payload = `"exec"+pad(0x24)+\x00*8+fake_rbp+ret`；ret = **one_gadget 0xebc81**（约束 rbp−0x78 可写 → saved_rbp 填数据段地址 0x404a00，页对齐 RW 段内）→ getshell

## 题解学习要点（X1cT34m 官方 writeup：ctf.njupt.edu.cn/archives/1154）

- 官方与本题解在 canary 置 0、name/bss 相邻泄 libc、单跳 one_gadget 三点完全一致；官方路径最短（2×good + 1×vuln）
- 官方 one_gadget 用 0xebc81，saved_rbp 填数据段地址正是为满足 "rbp-0x78 writable" 约束——学到：**one_gadget 的 rbp 约束可用 saved_rbp 定向喂可写地址**
- 官方 exp 的 canary 区填 'a' 应为笔误：检查逻辑要求 [rbp-0x8] == fs:0x28 == 0
- 本题解额外验证稳态变体：程序自带 `leave;ret`@0x4014b6 栈迁移 + ROP 链铺 buf 偏移 0x08-0x27（pop_rdi@+0x2a3e5 / binsh@+0x1d8678 / system@+0x50d70），规避 one_gadget 的 r10/rdx 残留寄存器赌运；栈地址可经 `exit_a` 的 `bss=&c` 原语泄露（F = &c+9）

## 一得

- 遇 `__stack_chk_fail` 类题**先反查 .init_array 全部条目**——init_array 篡改 fs:0x28 是 "canary=0" 题的固定套路（RSI 固化候选：.init_array canary 篡改检测器）
- 无格式化字符串时，**相邻全局变量 + strncpy 恰好越界填满** = 合法 %s 越界读原语，泄 GOT 指针
- 产物：`ctf-20min\2025-WQB-Quals__ood_canary\{exp.py, writeup-ood_canary.md}`；learn-writeup 已入库（1 检查点 + 2 技能卡）

## 下轮候选

- WQB 剩余未做题：pwn/signal（745KB zip 有附件）、pwn/imi_mqtt（附件网盘不可用跳过）、web 3 道（纯题面在线环境题，需检索题解路径：官方 writeup 已含 ez_python/ssti/easy_readfile 三题完整解法可直接学习）

> AI生成
