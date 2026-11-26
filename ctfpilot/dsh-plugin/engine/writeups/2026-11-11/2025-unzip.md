# 2026-NCO-Quals Rev-入门夺旗-unzip

- **题目**：CTF-Archives/2026-NCO-Quals/reverse/Rev-入门夺旗-unzip（5pts，StaticAttachment）
- **FLAG**：`NCO26{unz1p_m3_1f_y0u_c4n}`
- **状态**：SOLVED（总耗时约 12 分钟：选题约 9 分钟、解题约 2 分钟、收尾约 1 分钟）
- **解法链**：纯标准库 `zipfile`+`io.BytesIO` 内存递归解压 42 层同名套娃 zip（每层单成员 attachment.zip），最深层 flag.txt 明文即 flag；全程未落盘、未执行任何文件。
- **过程**：api.github.com 两次 403 限流 → 按既定方案降级 jsDelivr 列目录；WQB-Quals 确认用尽（剩余题均为在线环境/过期网盘外链/公众号题）→ 探测 2026-NCO-Quals；第一候选「Web-入门夺旗-超级无敌保险箱」源码副本注释中 flag 被归档抹为占位符 `NCO26{xxxxxxxxx}`（DynamicContainer，真 flag 在远程环境），结构性不可解 → 换题 unzip；引擎 `solve --mode auto` 0.5s 返回假阳性 flag `NCO26{.*}`（把 description.txt 里的格式模板字面量当 flag）→ 按规则脚本复核，2 分钟出真 flag。
- **标签**：`misc/zip-nesting`、`zip-recursive-unpack`、`chain-decode`、`triage=misc`、`false-positive/flag-regex-template`、`stdlib-only`
- **题解学习要点**：NCO2026 为新赛事，README 无解法链接，两轮在线检索（题名+赛事+writeup、"NCO2026" 整卷）均无公开题解，按模板自证落库；本题解法唯一（递归解压），无更优路径；套娃深度 42 层无压缩比陷阱，纯体力活归 fast 类。
- **一得**：CTFPilot fast 通道假阳性新形态——`--flag-regex` 指定的格式模板若以字面量出现在题面文件（description.txt）中，会被直接提取为 flag 返回；修复点：flag 提取应排除与 regex 模板字符串完全一致的候选。固化候选：「zip 套娃递归检测器」——单成员 zip 递归解到非 zip 层 + 尾层正则扫描，可并入快通道压缩解包族。
- **失败原因**：无（本题解出）；本轮废弃题「超级无敌保险箱」因源码副本 flag 被抹为占位符而不可本地解出，未计入 done.json，待官方 writeup 或在线环境补齐。

> AI生成
