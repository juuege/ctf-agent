# GY10-AutoCrypt-ADB 解题报告（2026-ICSC 工控安全锦标赛 Reverse）

- 赛题：2026 第十届工业信息安全技能大赛工控安全锦标赛 · Reverse · GY10-AutoCrypt-ADB（来源 CTF-Archives/2026-ICSC-ICS-Champ）
- 附件：autocrpt_adb（84KB ELF64 x86-64 stripped 用户态，题面称"驱动"）、firmware.enc（200B）
- flag：**NOT SOLVED**（token 生成链已完整还原，唯一缺口=VM 解释器重实现，超出 15 分钟盒）
- 耗时：约 25 分钟（超 15 分钟盒约 10 分钟；超在 VM handler 逆向量评估前的结构勘察阶段）
- 通道：快通道 solve auto NOT SOLVED 12.4s/50 尝试 → 模型探索通道静态逆向（WSL objdump，全程未执行样本）→ 2 轮 online_search + 鱼影团队博客核查均无公开题解 → learn-writeup 落库收尾

## 实证发现（核心价值）
1. **autocrpt_adb 是用户态 challenge-response token 机**：`usage: %s <challenge_hex16>`，内嵌序列号 `LSJU22AC1A5001234`（17B，0x1359a+0x34）与 64KB RAM(0x15090)。
2. **自定义字节码 VM**（2aa0，37 操作码 switch，跳转表@0x5100）：三代码库 129B@0x25090 / 2153B@0x25120 / 52459B@0x25990，解码器 0x51a0/0x5230/0x5aa0；VM 失败打印 "VM error"，成功在 RAM[0x39] 产出 16B。
3. **token 链**：VM 16B → 16 轮 S 盒链（S1@0x12790、S2@0x12890、M@0x129b0，challenge 为密钥，索引 (7i+buf[i]+key[i&7])&0xff）→ challenge 派生置换 → 逐字节链式 XOR 常数 0..0x0f → 16B token。
4. **反调试 7 层**（污染计数@0x3267c，>1 时输出被 PCG 式 LCG 污染）：ptrace TRACEME、TracerPid、maps 扫 gum-js-loop/linjector（Frida）、rdtsc 时序×2、connect 127.0.0.1:10050 成功即污染（防本地伪造服务）、FNV-1a 自校验。
5. **token 永不打印**：成功路径用去混淆 4af0（`dst[i]=src[i]^0x5A+i`）将消息覆盖进 token 缓冲区，仅输出 "ONLINE ONLY: reverse+firmware; submit token remotely."。
6. **firmware.enc 布局**（200B）：[0:16] 序列号前缀 + [16:24] challenge=`1122334455667788` + [24:40] 种子 `00,11..ff` + [40:200] 160B 密文（10×AES 块尺寸）→ flag 即该密文经 token 解密。

## 尝试与失败原因
- 9 组 XOR 组合（serial/seed/chal/md5/sha1/拼接变体）对 160B 密文全无命中 → 排除简单 XOR。
- VM 37 handler + 3 解码器重实现约需 30-60 分钟，盒内仅完成结构级逆向（~1164 行 asm 已存 `.temp/acdb/dis.txt`）。

## 题解学习要点（2 轮检索 + CSDN 工控CTF 专栏 12 篇核查，无公开 wp）
- 本题无任何公开题解；鱼影团队仅覆盖该赛事 Misc 第一场（可佐证赛事实名）。
- README 题面"分析驱动程序+编写应用层程序与驱动通信"实为 VM 固件逆向 + token 复算；"ONLINE ONLY" 提示真机流程为远端校验，离线收口在 firmware.enc。
- 续解路径（变体预测）：①提取跳转表 0x5100 的 37 个 handler 逐个重实现，RAM=serial17+chal8，取 RAM[0x39:0x49]；②套 S 盒链+置换+链式 XOR（常量表 .rodata 直读）得 token；③token 作 AES-128 key（种子 00,11..ff 或作 IV）解 160B 密文。

## 七维标签
reverse / custom-vm-interpreter / anti-debug-7layer / challenge-response-token / sbox-chain-keyed / xor-obfuscation-0x5A+i / firmware-enc-pairing

## 一得
ELF 题指纹识别：导入 ptrace+syscall、maps 扫描串（gum-js-loop/linjector）、`usage: %s <challenge_hex16>`、内嵌序列号同时出现在配套 .enc 文件头 → 自定义 VM+反调试 token 机，且 **hex16 challenge 直接藏在配套附件里**（本题 10 分钟才确认，应 5 分钟内完成）——"驱动+应用层"话术的题本质是 token 复算，配套 .enc 的头部常量就是 challenge。固化候选：①ELF 指纹速判器（导入表+字符串特征→VM/反调试/解混淆 0x5A+i 预判）；②VM 题跳转表 handler 自动切分器（objdump -s 提取跳转表+边界切分，为重实现提速）。

## 登记与产物
- writeup 已 learn-writeup 落库（GY10-AutoCrypt-ADB_2026ICSC，5 checkpoint + 2 技能卡）
- 中间产物：`.temp/acdb/`（dis.txt 全量反汇编、writeup.md）
- done.json 已登记 CTF-Archives/2026-ICSC-ICS-Champ/GY10-AutoCrypt-ADB
- 下轮候选：续攻本题 VM 重实现（从 .temp/acdb/dis.txt 接续）或同仓库 GY10-Driver / GY10-VCL-FW（均 Reverse）

> AI生成
