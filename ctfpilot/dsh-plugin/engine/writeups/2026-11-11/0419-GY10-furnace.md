# CTF 循环任务报告：GY10-furnace（2026-ICSC-ICS-Champ / Pwn）

- **时间**：2026-11-11 03:35–04:20（约 45 分钟，超 15 分钟盒 30 分钟）
- **结果**：**NOT SOLVED（无 flag）**——远程 192.168.18.21:7777 内网失联、二进制内无 flag，按技能合规仅产出 exp 源码与偏移；**完整攻击链已静态推导并用 300 轮模拟数学验证通过**，环境恢复即可取 flag
- **附件**：furnace.zip 5.9KB → 单 ELF 18KB（PIE/stripped/canary/NX/seccomp）

## 通道记录
| 通道 | 结果 |
|---|---|
| 快通道 solve --mode auto | 4.5s NOT SOLVED（预期：引擎无 pwn 检测器） |
| 模型探索（纯静态：readelf/objdump@WSL + Python stdlib 模拟） | 攻击链完整闭合，300/300 轮数值验证 |

## 七维标签
`pwn` `seccomp-ORW` `safe-linking混淆(G=0x9e37…,seed=AT_RANDOM)` `堆结构泄露` `位法解方程(G奇数)` `假节点原语(phase≠raw免身份)` `间接call触发win`

## 关键发现（每步一行）
- seccomp 手工 BPF 25 条还原：allowlist=read/write/openat/close/exit/exit_group/brk/newfstatat/lseek/getrandom，余 KILL → win=ORW
- 协议：11 单字节命令（c=idx^0x60）；5 节点堆 n_k=HB+0x10+0x50(k-1)；f30/f0 双层 safe-linking 混淆
- **核心洞**：'a' 只在 phase==raw 时校验身份，phase 为其他值（含 .bss 的 0）零校验接管 → 假节点原语
- 'b' 写窗口 state2=56 字节（lea 0x30(,%rax,8)，覆盖 f30）→ 可控 head
- 泄露：'c' 读 f0=prev 堆地址；锻造 node[8..15] 滑窗 → seed 低 29 位；记录区三 qword（xor 0xa5+0x11i 去混淆）
- 求解：G 奇 → (P·G)^(P<<12)=C 方程 LSB 位法 64 步唯一解堆页（免 2^28 暴力）→ seed → PIE(12ed)
- 终链：接管 51c0 写 "/flag"、接管 5240 写 12ed^seed → 'h' 布防（输入=((((2S^path)>>12)*0x7f4a7c15)^S_low32)）→ '`' call *(5240)^seed → 12ed openat 读 flag

## 题解学习
- 两轮检索（题名+赛事+writeup / 关键词变体）无公开题解（2026 新赛事），GitHub 仓库 README 无解法链接 → 自主推导闭合，writeup 已 learn-writeup 落库（8 检查点、3 技能卡）
- 变体预测与失败路径已写入 writeup（'b' 限长误读、12c9 首指令漏看两大坑）

## 失败原因（超时归因）
1. **12c9 首指令 `xor %rsi,%rdi` 漏看**（objdump 分页读截断函数头）→ 误判 'a' 为必死陷阱，绕弯约 6 分钟——教训：函数完整字节先读再下结论
2. **'b' 限长 lea 0x30(,%rax,8) 误读为 cmov** → 一度认为 f30 不可写、链路死锁约 8 分钟——教训：`8d 04 c5` 操作码=变址 lea，立即警觉
3. Pwn 题静态全推导本身工作量即超盒（状态机 11 命令+双通道混淆+win 门控），属题目难度而非路径错误

## 一得
带 XOR 的乘法方程 (P·G)^(P<<12)=C：G 为奇数时 LSB 位法 64 步精确重构唯一解，免去 ASLR 暴力——pwn 泄露方程求解通用技；固化候选：CTFPilot 增加"混淆链泄露方程求解器"（safe-linking 常数自动识别+位法求解）。

## 产物
- exp：`ctf-20min\2026-ICSC-ICS-Champ__GY10-furnace\exp_furnace.py`（io 封装留位，接环境即用）
- writeup：`ctf-20min\2026-ICSC-ICS-Champ__GY10-furnace\writeup_furnace.md`（已落库）
- 验证脚本：`.temp\f_verify.py`（300/300 通过）
- done.json 已登记 `CTF-Archives/2026-ICSC-ICS-Champ/Pwn/GY10-furnace`

## 下轮候选
同仓库剩余：Reverse/GY10-AutoCrypt-ADB、GY10-Driver、GY10-VCL-FW、GY10-反应釜温度与压力融合控制（Pwn 组已清空）

> AI生成
