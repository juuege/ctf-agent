# CTF 刷题报告：2025-WQB-Quals pwn/signal

- 日期：2026-11-11 19:10（本轮任务窗口）
- 题目：CTF-Archives/2025-WQB-Quals pwn/signal（ARM32，qemu-arm 部署，745KB zip）
- 结果：**NOT SOLVED**（结构性与边界双重原因，见下）；静态分析闭环完成，全链漏洞路径已确认
- Flag：未获得（服务端 flag，赛后未公布明文；官方 X1cT34m WP 未覆盖本题）

## 耗时

- 总计约 20 分钟（盒内约 17 分钟收尾）：选题/下载 ~4 分钟（jsDelivr 断连一次，改走 git blobs API），引擎 solve 103.5s NOT SOLVED（46 次尝试，pwn 超引擎边界），静态分析 ~8 分钟，题解检索与学习 ~4 分钟
- 单题 15 分钟盒：静态分析已用满，收尾符合 5 分钟预算

## 题目与静态分析结论

- `cli`（ARM32 PIE + canary + NX）为**客户端**：connect 127.0.0.1:8000 "controller server"，失败即 exit(1)；alarm(300)；pthread 子线程 + mutex 维护红绿灯状态
- 菜单：1.car forward(fn_df0) / 2.pedestrian forward(fn_101c) / 3.backward(仅提示) / 4.stop(close+exit)
- fn_df0 红灯（state==3）时 coinflip `rand()%2`：掷中进入"闯红灯拍照"流程——`read(0, state+1, 0x34)` + `sprintf("your record msg: %s", state+1)` 回显
- **独立发现两条确定性漏洞原语**（后经官方 WP 佐证）：
  1. read off-by-one：state 结构 52 字节，`read(0, state+1, 0x34)` 第 52 字节覆盖 main 帧 canary 最低字节（LSB）
  2. sprintf `%s` 无界读：覆盖 canary LSB 破掉首 0x00 截断后，%s 穿越 canary 读出栈帧内容并 printf 回显 → 栈地址 + canary 泄露
- 反汇编路径：WSL objdump 不支持 ARM → capstone（.temp 依赖）+ 自研 ELF32/PLT 解析脚本，修正了 PLT 桩 12 字节布局的注解错位

## 题解学习（CSDN z22168《2025湾区杯pwn部分wp》）

官方 X1cT34m WP 的 Pwn 节仅覆盖 ood_canary/digital_bomb，signal 由民间 WP 补齐，与自析对照：

| 环节 | WP 解法 | 自析状态 |
|---|---|---|
| 复现环境 | pwntools `listen(8000)` 自建 controller（"signal\n"→"3 5"） | ✅ 已判明 controller 必须存在 |
| 阶段一泄露 | 'a'*0x2F 漏栈地址(-0x64)；'a'*0x34 覆 canary LSB 漏 canary(-0x61) | ✅ 两条原语均独立发现，❌ 偏移值盒内未算出 |
| coinflip 随机数 | 掷不中就重连重试 | ✅ 已定位该分支 |
| 阶段二溢出 | **fn_101c**（行人前进）：输 0xFFFF 行走时长 → "registration information:" 处溢出写 ARM shellcode + canary*4 + 栈地址*2 | ❌ 盒内未分析到 fn_101c 的溢出点 |
| 关键特性 | **qemu-user 开 NX 后栈仍可执行** → 直接 ret2shellcode，免 libc 泄露 | ❌ 漏掉（本年度重要新知） |

已按 writeup 模板 learn-writeup 入库：5 检查点 + 3 技能卡（signal-WQB2025-00/01/02.json）。

## 一得

**qemu-user 的 NX 不可信：ARM pwn 题若部署为 qemu-arm 用户态模拟，即便 ELF 标 NX，栈依然可执行——优先 ret2shellcode 而非 ROP**，可省去 libc 泄露环节。配套一得：canary LSB 覆盖是 %s 泄露 canary 的前置钥匙；客户端型 pwn 题（binary connect 固定端口）用本地 listener 伪造协议即可复现。搜索词层面：国内赛事用「题名 赛事 wp」比 "writeup" 命中率更高。

## 失败原因

1. **结构性**：flag 存于赛时服务端，附件仅有客户端 cli；执行题给二进制属任务禁区（不做动态运行），无远程靶机 → flag 不可得，与 digtal_bomb 同类收尾
2. 15 分钟盒内静态分析完成了漏洞识别（两条原语），但未能完成：fn_101c 溢出点定位、泄露偏移计算、qemu NX 特性判断——均已在收尾期从民间 WP 补齐入库

## 附注

- 报告与附件：`ctf-20min\2025-WQB-Quals__signal\`（signal.zip + src 解压，未执行任何可执行文件）
- done.json：更新至 73 条（新增 `CTF-Archives/2025-WQB-Quals/pwn/signal`）
- 下轮候选：WQB web 三题（easy_readfile/ez_python/ssti）无附件、为在线环境题，赛后无环境，结构上同样不可解出 flag，但官方 WP 已覆盖三题（ez_python=JWT 爆破+yaml 反序列化、ssti=Go 模板注入、easy_readfile=phar 包含+软链接提权），可选做「纯 WP 学习轮」或转向其他仓库（WQB 已仅剩 imi_mqtt 附件在百度网盘不可达 + web 三题）

> AI生成
