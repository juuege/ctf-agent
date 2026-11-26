# CTF 循环任务报告：works（2024-CCB-CISCN-Quals / PWN / 500 分 / 全赛 0 解）

- **flag**: 未取得（NOT SOLVED）。唯一 flag 格式串 `flag{flag_not_here}` 为 f2 盘 FLAG 文件中的明文诱饵。
- **耗时**: 解题约 17 分钟（超 15 分钟盒约 2 分钟，超在 FAT 解析脚本两次返工：LFN 死循环 + BPB 误读）；题解学习约 4 分钟。
- **标签**: 识别信号=.img 镜像+qemu start.sh+1883 端口=自制 OS/工控固件 pwn；陷阱类型=FLAG 文件明文假 flag（引擎快通道误报 SOLVED）；可迁移模式=磁盘镜像先读 MBR@0x1BE 分区表按 LBA 定位 FAT，勿从 0x0B 直接读 BPB；变体预测=MQTT 解析器内存破坏/VxWorks 网络栈类 CVE/f2 slack 与删除文件藏 flag。
- **题目结构（已探明）**: f1.img(FAT16, LBA63) 含 BOOTROM.SYS(431KB) + VXWORKS(920KB ELF, 全 stripped)；f2.img(FAT12, LBA63) 含 FLAG(诱饵 51B) + 空 MQTT 目录；start.sh 用 qemu-system-i386 把宿主 9999 转发到 guest 1883。内核字符串证实启动时打开 `ata01:1/mqtt`，含 `%s/%s/%s/%s`、"that long?"、"under development"、"not supported yet, ignoring" 等自定义 broker 响应串。
- **解题路径（已走部分）**: MBR 分区表→FAT12/16 递归提取（纯标准库自写脚本，实测可用）→内核 ELF 提取→字符串定位 broker。剩余路径：反汇编 920KB stripped i386 内核逆向 MQTT broker 协议处理逻辑，或 qemu 起环境对 1883 做 fuzz（本轮边界禁执行，未做）。
- **一得**: 磁盘镜像题的 BPB 不在 0 扇区 0x0B——必须先解析 MBR@0x1BE 分区表拿 LBA 起始扇区再读 BPB；FAT 子目录解析必须跳过 '.'/'..' 项否则递归爆炸；诱饵 flag（`_not_here`/`_test` 后缀）应加引擎过滤，本次又被快通道误报 SOLVED。
- **题解学习要点**: 官方 README 确认 Works 全赛 0 解、PWN 类 500 分，题面提示"服务启动需 1-2 分钟、打崩不自动重启"→ 攻击面是 MQTT broker 服务本体。公开题解 3 轮检索（online_search ×2 + A1natas 官方 WP 站，TLS 挂不可达）无果——0 解题无公开 wp 属预期，学习点转向本题已探明的攻击面结构：boot 链（MBR→VXLD 2.0→bootrom.sys→VXWORKS）+ 双盘架构 + 1883 服务；后续可对标 VxWorks 已知网络栈 CVE（如 CVE-2010-2965 IGMP 远程内存泄漏）与 MQTT 协议解析 pwn 模式。
- **失败原因**: ①时间盒内无法完成 920KB stripped VxWorks 内核的 broker 逆向（需反汇编工具链，构建成本高）；②边界禁止执行任何可执行文件，无法 qemu 起环境动态调试 1883 服务；③全赛 0 解，无题解可抄近路。
- **产物**: 本报告；`ctf-20min\CTF-Archives__works\.temp\fat.py`（MBR+FAT12/16+LFN+子目录通用提取器，纯标准库）；writeup 已 learn-writeup 落库（works_2024CCB，正确识别为 pwn）；done.json 已更新至 16 题。
- **固化完成**: 引擎 config.json flag_patterns 已加诱饵负向过滤 `(?!flag_not_here\}|test_flag\})`（第二次遇到诱饵误报，novel1 首次），正负样本验证 5/5 通过 + selftest 6/6 全过，已上线。FAT/img triage 提取器列为下轮固化候选（需按 SOP 六步走，本轮时间盒不足）。
- **下轮建议**: 2024-CCB-CISCN-Quals 可做 zip 已用尽（sxmisc 29MB 超限、server.tar.gz 非 zip），按规则探测 CTF-Archives 组织其他赛事仓库。

> AI生成
