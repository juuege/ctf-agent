# CTF 20 分钟轮：digtal_bomb（2025 湾区杯初赛 pwn）

- **结果**: NOT SOLVED（本地完整逆向，无远程靶机未获 flag；官方 Writeup 为 getshell 型，未公布 flag 字符串）
- **耗时**: 约 18 分钟（15 分钟解题盒 + 收尾学习，20 分钟硬截止内）
- **选题**: CCB 18 题已尽 → WQB2025 附件未做题中第一道（pwn/digtal_bomb，1.02MB）
- **通道**: 引擎 auto/deep NOT SOLVED（82s，pwn 超边界）→ 模型探索通道（WSL objdump 全量反汇编）

## 已完成分析
- ELF64 全保护，14KB，glibc 2.35（Ubuntu GLIBC 2.35-0ubuntu3.10），符号 stripped
- 数字炸弹掩体：min/max 相差 1 → bomb 恒为低端点 → 猜非炸弹端必使区间收缩至 [bomb,bomb] → 进消息板菜单（choice 666 = edit）
- 菜单四功能：New(idx0-10, size∈(0x10,0x800]) / Delete(置NULL) / Show(puts) / Edit(仅一次, 0x10 字节)
- **漏洞定位**: New 的 read(0,ptr,size) 后补写 ptr[n]=0 → off-by-one null byte
- glibc 2.35：safe-linking 存在、__free_hook/__malloc_hook 已废弃

## 失败原因
- 堆题深水区：heap leak 原语与 overlap 布局 15 分钟盒内未完成
- 无远程靶机，exp 无法验证；本机无 pwntools

## 题解学习要点（X1cT34m 官方 Writeup，已 learn-writeup 入库）
1. edit 唯一一次优先用于 **leak heap**：大块写 p64(0)+p64(0x601) 伪造 chunk 头 → free 后 add 回 → edit 截断 → show puts 堆指针（safe-linking 的前置条件）
2. off-by-null 把 0x601 chunk 缩为 0x510 制 chunk overlap → show leak unsorted fd → libc base = leak - 0x21ace0
3. overlap 内伪造 tcache entry：fd = strlen.got ^ (heap>>12) → tcache poisoning 劫 **libc 自身 strlen.got**（puts 内部调 strlen）→ 写 one_gadget(0xebc85) → 菜单任意 puts 触发 getshell
4. 对照自查：漏洞点、菜单进入技巧、glibc 版本判断与官方一致；缺的是 heap leak 手法与 strlen.got 目标选择

## 一得
- `rand()%(max-min)` 在 max-min=1 时 bomb 恒为 min——"间隔 1"是数字炸弹类掩体题的通用必进开关
- glibc 2.35 无 hooks 时，劫 libc 自身 GOT（strlen.got）+ puts 调用链触发 one_gadget，比找 hook 目标更普适
- 唯一一次小 edit 应优先花在 heap leak，不用于直接写数据

## 标签
pwn, heap, off-by-one-null, glibc2.35, safe-linking, tcache-poisoning, chunk-overlap, GOT-hijack, libc-leak, menu-trick

## 记录
- GitHub api.github.com 本轮限流，降级 jsDelivr data API 列仓库 + CDN 下载附件（既定通道，未跳过轮次）
- 附件目录：`ctf-20min\2025-WQB-Quals__digtal_bomb\`（digtl_bomb + libc.so.6 + ld-linux-x86-64.so.2）
- WQB2025 剩余未做：pwn/ood_canary、pwn/signal、reverse/strangeApp（有附件）；imi_mqtt 等仅 README 在线题
- done.json 已更新

> AI生成
