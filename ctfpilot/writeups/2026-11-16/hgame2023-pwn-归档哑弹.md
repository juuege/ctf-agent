# hgame2023-week1 simple_shellcode 与 week2 editable_note：归档 pwn 哑弹复盘

- 题目：hgame2023-week1 simple_shellcode 与 week2 editable_note 归档哑弹复盘（pwn）
- 题型：pwn
- 结果：NOT SOLVED
- flag：无
- 载体：两个题包各含 vuln（ELF64 动态链接）+ ld-2.31.so + libc-2.31.so 三件套；无源码、无 flag 文件、无 docker 或靶机配置
- 识别信号：附件构成 = vuln + libc + ld 配对三件套且无任何 flag 样本；simple_shellcode：导入表 prctl + mmap + read，字符串 "Please input your shellcode:"，符号表含 sandbox → seccomp 沙箱读 shellcode（典型 ORW 题，flag 在远程）；editable_note：导入 read/malloc/free，菜单串 "1. Add note / 2. Delete note / 3. Edit note / 4. Show note"，符号 add_note/edit_note/show_note → 堆管理菜单题（flag 在远程）；两题 flag 均只存在于已下线的远程靶机，归档附件本地物理不可恢复
- 核心技术链：triage 判 pwn → strings 全附件找 flag 样（0 命中）→ 导入表与菜单串核对具体题型 → 结论 remote-flag-only 哑弹 → 直接出 NOT SOLVED 定论，不再烧深通道预算
- 有效技术：strings 找 flag 样本；导入表核对（prctl/mmap → seccomp+shellcode；malloc/free+菜单 → 堆题）；哑弹三查（flag 样本 / 附件构成 / 导入表）
- 失败原因：引擎 fastpath 对 ELF 跑 xor-single、decompress 等无关检测器 22 次全部 no-flag；深通道 read+strings 泛扫 libc/ld 2MB 大文件多步 no-progress；Windows 无 ELF 运行时且无远程靶机 = 物理不可解，任何检测器都救不了
- 可迁移模式：pwn 归档题先做哑弹三查，命中即秒判 NOT SOLVED 省真实墙钟；注意对抗时钟把 unsolved 记满 timeout（arena 公式 mean_solved×rate + timeout×(1−rate)），提前止损只省真实时间不改善时钟，唯一真实路径是多解题或解得更快
- 陷阱类型：把解不出当知识缺口去加检测器是方向错误——pwn 真实解出率 0/37（库里 2 个名义 solved 是路由误标：novel1 实为小说题、ezudp 实为 Misc 题），这是能力边界（需运行时交互）不是卡片薄
- 变体预测：远程地址写在题面 README 而非附件内；部分归档带 docker-compose 可本地起靶机（此时可换本地复现路线，当前环境无 docker 未测）；PE/Windows 类 pwn 哑弹形态未遇
- 适用限制：仅判离线不可解；若附件含 flag 文件、内嵌 flag 串或靶机可达则不适用；不能外推到 PE 恶意分析类
- 未测试：docker-compose 本地复现靶机路线；WSL 下 pwntools 交互路线
- 可固化性：不可固化为 fastpath 检测器（交互族，违反 memory 不等于 capability 边界）；以知识卡固化哑弹三查法即可

## 题目

CTF-Archives 归档拉取 2023 hgame 第一周 simple_shellcode（seccomp 沙箱读 shellcode 执行，导入 prctl mmap read）与第二周 editable_note（堆菜单题 Add Delete Edit Show note，导入 malloc free read）。两题附件均为 vuln 加 libc-2.31 加 ld-2.31 三件套，无 flag 文件与靶机配置，flag 只存在于已下线的远程服务，离线环境物理不可恢复，应秒判哑弹并如实计 NOT SOLVED。

## 一得

pwn 专项 RSI 触发（连续两道同类 NOT SOLVED）后的诚实结论：0/37 的真实解出率源于远程 flag 依赖运行时交互，属能力边界而非知识缺口；引擎侧唯一诚实的知识固化是哑弹三查秒判法，让模型通道在未来 pwn 拉题上把确认时间从分钟级压到秒级。

> AI生成
