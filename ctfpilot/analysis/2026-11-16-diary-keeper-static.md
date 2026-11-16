# 2026-NCO-Final PWN「diary-keeper」静态精读分析

> 分析对象：`D:\teleclaw\.temp\dk_extract\diary-keeper`（16376 B）
> 题型修正：引擎 fast+auto 误判 **misc**；实际为 **PWN · 格式化字符串**（远程 DynamicContainer，无本地靶机可行）
> 方法：pyelftools 结构解析 + capstone 反汇编 + pwntools 符号查找，全程静态，未连接任何远程

---

## 1. ELF 安全属性（checksec 结论）

| 属性 | 状态 | 证据 |
|---|---|---|
| **PIE** | **No PIE** | `e_type = ET_EXEC`，入口 `0x4010d0`，代码段固定于 `0x400000–0x4014f0` |
| **RELRO** | **Partial** | 有 `PT_GNU_RELRO`（`0x403e10–0x404000`），但 `.got.plt`（`0x404000–0x404040`）在 RELRO 区**之外**；`.dynamic` 无 `DT_BIND_NOW`/`DT_FLAGS` → 惰性绑定，**GOT 可写** |
| **Canary** | **Yes** | 导入 `__stack_chk_fail`（GOT `0x404020`）；write_diary/read_diary 均有 `mov rax, fs:[0x28]` 序言 |
| **NX** | **Yes** | `PT_GNU_STACK` flags=6（RW，非可执行）；数据段 flags 无 X |
| CET/endbr64 | 启用（endbr64 抬头） | `.note.gnu.property`，对各函数头部已见 `endbr64` |

### 导入函数（全部 PLT，无 libc 静态链接符号）
`puts(0x404018) / __stack_chk_fail(0x404020) / setbuf(0x404028) / printf(0x404030) / fgets(0x404038)`（GOT 地址）
`.plt.sec` 槽位：`puts=0x401080, stack_chk=0x401090, setbuf=0x4010a0, printf=0x4010b0, fgets=0x4010c0`

**关键否定证据**：无 `malloc/free/read/write/open/system/execve` 导入 → 与堆题（UAF/double-free）无关；无文件读写 → 本体不读 flag。

### 符号表（未 strip，二进制极小）
| 函数 | 地址 | 大小 | 职责 |
|---|---|---|---|
| setup | `0x4011b6` | 71 | setbuf(stdin/stdout/stderr,0)，交互无缓冲 |
| show_intro | `0x4011fd` | 101 | 打印横幅与彩蛋文案 |
| write_diary | `0x401262` | 196 | 写当日日记（**触发点 A**） |
| read_diary | `0x401326` | 377 | 菜单（统计/编辑，**触发点 B**） |
| main | `0x40149f` | 78 | `for(i=0;i<=6;i++){write_diary();read_diary();}` **7 轮循环** |

---

## 2. 字符串证据（.rodata 0x402000–0x40228b）

```
0x402038 "    Welcome to Magic Diary System!"
0x402060 "This is a magical diary that can record your secrets."
0x402098 "Cultivate a habit by writing diary for a week!"      ← 谜面即题解：一周=7轮循环
0x4020c8 "But be careful, the diary sometimes leaks things it shouldn't..."  ← 明示"泄漏"→格式化字符串泄露
0x402110 "Please write your diary entry for today:"
0x40213c "Read failed!"
0x40214a "Your diary has been saved!"
0x402165 "Diary preview:"
0x402176 "What would you like to do:"
0x402191 "1. View diary statistics"      （固定假统计，无信息）
0x4021aa "2. Edit diary content"         （触发点 B 入口）
0x402218 "Please enter new diary content:"
0x402239 "Diary updated!"
0x402248 "Updated content:"
0x402261 "Thank you for using Magic Diary! Goodbye~"
```

判定：**无任何 flag 路径、无 "/bin/sh"、无 shell 相关字符串** → flag 只存在于远程容器文件系统，确认 DynamicContainer 语义。

---

## 3. 漏洞原语（两处完全同型的格式化字符串漏洞）

### 3.1 读写全部越界安全（排除溢出/UAF）
- write_diary：`buf=[rbp-0x90]`（距 canary `rbp-8` 尚有 0x88），`fgets(buf, 0x80, stdin)`@`0x4012b4` → 最多写 0x80 字节。**不溢出**。
- read_diary：菜单 `fgets([rbp-0x120], 0x10)`@`0x40139b`（16B 区间，安全）；编辑 `buf2=[rbp-0x110]`，`fgets(buf2, 0x100)`@`0x401438` → 0x100 < 0x110-8。**不溢出**。
- 无堆操作 → 漏洞面唯一。

### 3.2 触发点 A —— write_diary
```
0x4012ed  lea rax, [rbp-0x90]     ; rax = buf（用户输入）
0x4012f4  mov rdi, rax
0x4012f7  mov eax, 0
0x4012fc  call printf@plt         ; printf(buf) —— buf 即格式串！
```
### 3.3 触发点 B —— read_diary 选项 2
```
0x401460  lea rax, [rbp-0x110]    ; rax = buf2（用户输入）
0x401467  mov rdi, rax
0x40146a  mov eax, 0
0x40146f  call printf@plt         ; printf(buf2) —— 同样裸打
```
菜单选项仅比较首字节（`cmp al,0x31/0x32`，0x4013b0/0x4013fc），选 `2` 即达触发点 B。

### 3.4 可用预算
每轮 = A+B 两次触发，共 **7 轮 → 14 次格式化字符串**；buf 可输入 0x7F 字符（A）/（B 为 0xFF），`fgets` 原样拷贝嵌入的 `\x00`，payload 内可携带 NUL 结尾的 8 字节地址（GOT 地址 0x4040xx 无 `\x0a` 字节，天然无换行冲突）。

### 3.5 x64 栈槽推算（ABI 推导，S = printf 调用时 rsp）
`%N$p` ↔ `[S+(N-6)*8]`（%1–5 → rsi,rdx,rcx,r8,r9）：
- **触发点 A**（S=buf=rbp_w-0x90；rbp_w＝rbp_main-0x20）：
  - `%6$p` = buf 首八字节（任意指定可定位后续栈上地址）
  - `%23$p` = canary；`%25$p` = write_diary 返回地址 `0x4014c3`（校准用）
  - **`%29$p` = main 的返回地址 = `__libc_start_call_main+0x83`（libc 锚点）**
- **触发点 B**（S2=rbp_rd-0x120，buf2=S2+0x10）：
  - `%8$p` = buf2 首八字节 → **可打印地址数组基底**（%n 写入的指针来源）
  - `%41$p` = canary；`%43$p` = `0x4014c8`；**`%47$p` = main 返回地址（libc 锚点）**
- 注意事项：上述槽号由 ABI 静态推得，远程首发建议 `%6$p…%47$p` 短扫一遍校准 ±1 槽（不同 glibc 寄存器保存策略偶有偏移，但本环境 libc 版本固定见 §4）。

---

## 4. libc 鉴定（zip 自带 = 远程容器同款）

- **版本行**：`GNU C Library (Ubuntu GLIBC 2.35-0ubuntu3.13) stable release version 2.35.`（Ubuntu 22.04 jammy 补丁版）
- build_id：libc `095c7ba148aeca81668091f718047078d57efddb`，ld `8cfa19934886748ff4603da8aa8fdb0c2402b8cf`
- 关键符号偏移（针对**本 build**精确反汇编核实）：
  - `system` = `+0x50d70`
  - `execve` = `+0xeb080`
  - `/bin/sh` 字符串 = `+0x1d8678`
  - `__libc_start_main` = `+0x29dc0`；**`__libc_start_call_main` = `+0x29d10`**，其中 `0x29d8e: call rax`（call main，5 字节）→ **main 返回地址 = libc_base + 0x29d93**
- 教训性发现：网传 jammy 2.35 经典 one_gadget `0x50a37` 在本 0ubuntu3.13 build 中**不存在**（该区域为 do_system 的 posix_spawn 准备代码，已位移）；静态可确认的候选：
  - `0x50a73` 区：do_system 以 `lea rcx,[rip+…]`（→"/bin/sh"）+ `environ` 走 posix_spawn；
  - `0xebc88/0xebc93`：`lea rdi,[rip+…]`（→"/bin/sh"）+ `call execve`（__spawni 内部，寄存器约束多）。
  两者均需 one_gadget 工具/动态验证约束，**不作为主链**。

---

## 5. 推断利用链（全程静态推定，常数齐备）

```
Round 1 / 触发A：输入 "%29$p.%25$p"
  → leak = main返回地址 = libc_base + 0x29d93
  → libc_base = leak - 0x29d93
Round 1 / 触发B：选项 2，buf2 内构造成 %hhn/%hn 链（地址放在 buf2 尾部对齐槽位，自 %8$p 起可寻址）
  → 把 printf@GOT(0x404030) 覆写为 libc_base + 0x50d70 (system)
  （Partial RELRO + 惰性绑定，printf 在 Round1 触发A 已完成解析，覆写安全；GOT 地址无 0x0a，fgets 可原样读入）
Round 2 / 触发A：write_diary 输入 "sh"（fgets 存为 "sh\n\0"）
  → 0x4012fc call printf@plt ⇒ system("sh\n")（/bin/sh -c 对行尾换行不敏感）⇒ 拿到远程容器 shell
  → 容器内 cat flag / cat /flag（DynamicContainer 标准姿势）
```

**为什么这条链成立（静态证据链）**：
1. No PIE → 无需泄漏二进制基址，GOT/代码地址直接用 `0x404030` 等常量；
2. Partial RELRO → `.got.plt` 可写（§1 证据）；
3. 14 次触发满足「1 次 leak + 1~3 次写 + 1 次触发」的预算，7 轮循环即冗余设计；
4. 本地无线性溢出（§3.1），canary 全程不被触碰（%n 直写 GOT 与栈保护无关）。

备选链（同样静态成立）：
- `puts@GOT→system` + 在 read_diary 提示串处先触发（副作用：所有后续 puts 变 system，时序更乱，不如 printf@got 干净）+ 输入含 `sh` 的行；
- `%n` 覆写 main 返回地址（栈上 `[rbp_main+8]`，槽 29/47 可定位）→ ROP（需 3-4 次 %hn 拼 8 字节，可用触发预算内完成，但比 GOT 单点写费事）。

---

## 6. 离线可达边界（结论）

**离线拿不到 flag——确定**：
- 本体零文件读取导入、零 shell 字符串，flag 在远程 DynamicContainer 容器内，静态二进制不存在 flag 内容（全量 strings 已审计）；本题无任何「本地可解」路径（不是 flag 手算/隐写型）。另外本机 WSL 未装发行版，连本地动态复现也不可行——本轮纯静态。

**离线已做到/可验证的程度（交付物级）**：
1. 题型定性：pwn · 格式化字符串双触发（修正引擎 misc 误判，给出反汇编证据）；
2. 全部 checksec 属性 + GOT 布局常量（0x404018–0x404038）；
3. 两触发点的精确偏移与 %N$p 槽号推算（ABI 推导 + 帧重建，可按 §3.5 一步校准）；
4. libc 精确指纹（2.35-0ubuntu3.13，build_id）+ leak→base 锚点 `+0x29d93` + `system +0x50d70`；
5. 完整利用剧本（§5），拿到远程靶机后仅剩「写脚本发 payload」的机械工作，无需再逆题。

**残余不确定性（对远程一步校准即可消除）**：
- %N$p 实际槽号 ±1（不同 libc 构建寄存器保存差异）；
- one_gadget 约束未验证（主链不依赖）；
- 容器 flag 具体路径未知（shell 后 `ls /` 即得）。

---

## 7. 可迁移知识点

1. **DynamicContainer 题的离线打法**：题目 zip 必附远程同款 ld+libc → 所有 libc 偏移（system/锚点）离线精算，远程仅做交互；指纹用 build_id 而非记忆常量。
2. **one_gadget 网传偏移不可信**：同一 2.35 大版本的 0ubuntu3.x 补丁间代码会位移（本次实测 0x50a37 失效），必须对 build 重算或用 one_gadget 工具重扫。
3. **x64 格式化字符串槽号公式**：`%N$p ↔ [S+(N-6)*8]`（S=调用时 rsp）；帧重建推 canary/返回地址槽，再以固定地址（非 PIE 返回 0x4014c3）自校准。
4. **printf@got→system 是这类「读一行、立即 printf 回显」菜单的终局手**：printf(用户缓冲) 意味着 GOT 劫持后下次回显即 `system(用户行)`；`fgets` 保留 `\n` 且拷贝 NUL，构造地址数组无换行陷阱。
5. **防溢出≠防住**：本题两处 fgets 全部尺寸安全，canary/NX 全开，但参数级缺陷（fmt 串未定式化）足以打穿——审题先看 printf 第一个参数是否用户可控。
6. **谜面即设计**：`write a week → 7 轮循环`、`leaks things → fmt leak`，文本题面与攻击面预算（14 次触发）严格对应，出题意图可从字符串预读。

---
*分析工具：pyelftools 0.3x / capstone / pwntools-ELF（D:\miniconda3）；产物：dk_extract/（解包）、dk_analysis.md（本报告）。无远程连接，题目目录只读未改。*
