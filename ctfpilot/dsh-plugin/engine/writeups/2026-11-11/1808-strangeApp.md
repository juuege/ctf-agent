# CTF 刷题报告 — strangeApp（2025-WQB-Quals reverse）

- **日期**: 2026-11-11 17:42–18:08 | **仓库/题名**: CTF-Archives/2025-WQB-Quals/reverse/strangeApp
- **结果**: SOLVED | **flag**: `flag{just_easy_strange_app_right?}`
- **耗时**: 总约 26 分钟（选题+下载约 3 分；引擎 42s NOT SOLVED；模型补位解题约 18 分，**超 15 分钟解题盒**；收尾 5 分）——超 20 分钟硬截止，原因见下

## 选题过程
- CCB 18/18 已尽 → WQB-Quals 剩余未做题按序探测：forensics/SilentMiner、bademail 仅有过期外链（华为云 OBS 签名 2025-09 已失效，GitHub 无附件）→ 跳过；"关注公众号获取-flag"非技术题 → 跳过；pwn 4 题需运行服务拿 flag，与"不执行可执行文件"边界冲突 → 跳过；**选定 reverse/strangeApp**（4.36MB zip，经 api.github.com blob 通道下载，raw 不通）

## 解法链（纯静态，未执行任何文件）
1. APK 按 zip 解包：classes.dex（10MB 宿主）+ classes2.dex（10KB 壳框架 com.swdd.shell）+ libshell.so（hook ART `ClassLinker::LoadMethod`）+ assets/extract.dat（818B）→ **指令抽取壳**
2. 纯标准库解析 classes2.dex 定位壳结构；解 extract.dat 记录格式 `[u16 id][u16 0x3b][u32 insns_size][insns]` 共 19 条
3. 用 classes.dex 索引池（61472 方法/32791 字段/75333 字符串）解析记录内引用 → 手写 Dalvik 反汇编还原全部真实方法：`a()/aa()/compareBytes/<clinit>/onCreate`
4. 核心逻辑：`aa(input)` 加密输入与 `TARGET` 比对；`a(s)=(char)(s[0]^5)+s[1:]` 把字面量 `"DES"`→`"AES"`、`"DES/CBC/PKCS5Padding"`→`"AES/CBC/PKCS5Padding"`（**算法伪装**）；key=IV=`b"1234567891123456"`（均不经过 a()）
5. `<clinit>` 的 fill-array-data 提取 **TARGET 48 字节**：`7611077c...4ac45`
6. 纯标准库 AES-128-CBC 解密 → `flag{just_easy_strange_app_right?}`（34 字节 + PKCS5\x0e×14）

## 验证（三重）
- FIPS-197 标准向量加密/解密双向自检通过
- PKCS#5 padding 合法且明文全可打印
- 明文重加密 == TARGET 逐字节一致（往返闭合）

## 超时原因（诚实记录）
1. 初版 Dalvik opcode 表把 0xdf 归入 2addr 族，实为 **xor-int/lit8**（22b 格式），导致反汇编错位、重写两轮
2. 纯标准库 AES 首版 MixColumns 系数错位 + FIPS 自检误用赛题 key 而非 FIPS key，多一轮修复
3. pycryptodomex 不在 .temp（记忆说已装，实际被清理），被迫现场实现 AES

## 题解学习（X1cT34m 官方 writeup，ctf.njupt.edu.cn/archives/1154）
- 官方路线：**frida-dump 动态脱壳**后看代码，"只有一个 AES 加密"——与本解结论一致
- 对比：本解纯静态（解 extract.dat + 宿主 dex 索引池），无需运行 App，适配无执行/无设备场景；且还原了官方页面未展开的 DES→AES 伪装与 key/iv 细节
- learn-writeup 已入库（1 检查点 + 1 技能卡，writeup-strangeapp 版本化落库）

## 一得
- 指令抽取壳静态通法：**抽取资源记录解析 + 宿主 dex 索引池解析引用**，免 frida/免运行
- 固化候选：通用 Dalvik 反汇编器（opcode 表含 0xd8-0xe2 lit8 族）内置为脚本资产
- .temp 依赖不可持久假设：pycryptodome 需随用随装或固化纯标准库 AES（本次已实测后者可行）

## 标签
android, instruction-extraction-shell, dalvik-disasm, fill-array-data, aes-128-cbc, des-disguise, xor-first-char, pure-stdlib-crypto, LoadMethod-hook

## 下轮候选
2025-WQB-Quals 剩余：web 三题（easy_readfile/ez_python/ssti，纯源码审计适配静态边界，X1cT34m 有完整题解可对照）；forensics 两题附件已失效建议永久标注跳过

> AI生成
