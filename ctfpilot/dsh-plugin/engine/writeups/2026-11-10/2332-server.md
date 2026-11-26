# 2026-11-10 23:32 · 2024-CCB-CISCN-Quals / server（pwn）

## 结果
- **NOT SOLVED**（无远程靶机 + 本轮禁执行题目二进制），静态分析闭合，exp 骨架已产出：`ctf-20min\2024-CCB-CISCN-Quals__server\exp_server.py`
- flag：未取得（0 解出题，500 分）

## 耗时
- 总计 ~17 分钟：下载/解压 2 分钟，快通道 solve 72s（NOT SOLVED，引擎无 pwn 检测器，预期内），静态逆向 ~12 分钟，题解检索 3 分钟
- 超时点：无实质卡壳；按盒止损于"利用链规划完成、运行期偏移未标定"

## 题目结构（自研静态分析，未逐字节核对 293e4 尾段）
- HTTP todo 服务：cpp-httplib + 端口 9999，libstdc++ 静态链入（888KB，stripped）
- 路由→handler：/register→0xac9b、/login→0xb200、/logout→0xb8f7、/todos→0xc2c8(create)/0xcbc7(list)/0xd15d、PUT /todos/(\d+)(?:/(\d+))?→0xd590(update)、/todos/(\d+)→0xdd5e
- Todo 对象（0x30B）：+0x00 char* content_buf、+0x08 int size、+0x0c int len；启动时 main 循环 new 0x30×7 存 .bss 0xd8b60，每槽预置 new[0x201] "default"
- 保护：PIE + **FULL RELRO（DT_FLAGS=0x8 BIND_NOW）** + NX + Canary + glibc 2.39（Ubuntu 2.39-0ubuntu8.3，无 hooks）

## 漏洞（核心新知）
- `Todo::update`(0x29550) 与 `content_at`(0x293e4) 的偏移边界检查为**有符号 `jl offset<size` → 负 offset 直通**
- 写窗口检查 `size-offset ≥ len`：负偏移时窗口反而变大 → **PUT /todos/\<id\>/\<负offset\> = 堆上向后任意相对写**（长度 ≤ size+|offset|）
- 利用链规划：覆写邻接 Todo 的 content_buf → GET/PUT 变任意读/写 → 读 .got 泄 libc/PIE → 读 environ 泄栈 → 写线程栈返回地址 ROP execve；备选 FSOP（house-of-apple2，glibc2.39 有 vtable 校验）

## 七维标签
pwn / heap-OOB-negative-index / cpp-httplib-HTTP服务 / 有符号边界检查 / FULL-RELRO-glibc2.39 / ELF静态逆向(objdump-WSL) / 0解500分超盒

## 一得
**有符号边界检查=负偏移直通**：应用层出现 "Invalid offset" 类报错字符串时，先找检查指令的符号性（cmp+jl/jg），负索引即任意相对读写原语；2.34+ libc 的 RCE 终点提前按 environ 栈 ROP / FSOP 规划，勿找 hooks。

## 题解学习
- 公开题解检索：README 指向的 A1natas 战队 WP 站 TLS 异常不可达；imxbt 分区 WP（17 题覆盖）无 server；0 解出题 → 无公开解法可学，本题 writeup 为**自推导**（含失败路径与变体预测），已 learn-writeup 落库为 `server_2024CCB`
- 未核对项：0x29550 写入段尾部（memcpy 落点）、create 负 size 行为、堆上缓冲区与 Todo 对象实际间距（需本地调试）

## 失败原因
- 无远程靶机（比赛服务已关），且本轮规则禁止执行题目二进制 → 无法标定运行期偏移与验证利用链
- 技术失败：无；流程失败：raw 附件下载断流 1 次（重试成功）；首次 ELF phdr 解析按 64B 步长错乱（ELF64 phdr 实为 56B，已记入 writeup 避坑）

## 收尾
- 产物：本报告、`exp_server.py`、`server_writeup.md`（已落库）
- done.json 已更新（17 题）；下轮候选：本仓库附件已用尽（仅剩 sxmisc 29MB 超限跳过）→ 按规则探测 CTF-Archives 组织其他赛事仓库

> AI生成
