# CTF 20min 循环报告：Reverse-ezRE（2023-zjsdxs）

- **选题**：CTF-Archives/2024-CCB-CISCN-Quals 18 题已全部做完 → 转探测其他赛事仓库；2023-zjsdxs 剩 4 题中 apkre（3MB APK，本机无 dex 工具链）与 ezWEB（19MB Java Web，需靶机）不可行，选第一道可做题 **Reverse-ezRE**（jsDelivr CDN 下载 22KB，双层 zip 解压得 ezRE.exe 47KB + encrypt 10KB）
- **结果**：❌ NOT SOLVED（flag 未获取），15 分钟时间盒用尽
- **耗时**：总约 21 分钟（选题/下载 4 分钟，解题 13 分钟，学题解+落库 4 分钟）
- **通道**：引擎 solve auto 16.1s NOT SOLVED（956 attempts，无 PE 逆向检测器，预期内）→ 模型补位静态逆向，进度约 60%

## 已还原逻辑（下轮续攻基础）
1. 32 位 PE，Debug CRT 构建（ucrtbased/VCRUNTIME140D），含 IsDebuggerPresent/CheckRemoteDebuggerPresent/PEB BeingDebugged 三重反调试
2. main(0x412220)：提示输入 → 初始化 **6×6 目标矩阵**（36 个 DWORD，如 0x5eaa,0x6146,0x6412…）+ **24 元素密钥数组**（0x12,0x34,0x56,0x78,0x9a,0xab,0xcd,0xef,0x11,0x45,…）+ 96 字节输入缓冲
3. 坏指令+SEH 混淆区内藏输入复制循环（字节→DWORD 数组），随后双层循环做矩阵减法校验：`mat6[i][k] -= arr24[i*4+k] * arr96[j*6+k]`（i:0..5, j:0..3），非零打印 "Wrong!"，全零打印 "Congratulation!" + "But where is real Flag?Becareful!"
4. 核心加密在 0x41134d(input=0x41b150, len)，与 encrypt 文件读写相关，未逆向完
5. .data 0x41b000 宽串 "DllMain.dll"——IAT 无 LoadLibrary，判定为红鲱鱼或隐藏提示

## 失败原因
- 15 分钟盒内 PE 逆向工作量超限：反汇编提取踩了 PowerShell 数组 range 反向遍历坑（浪费约 2 分钟），坏指令混淆区甄别耗时，0x41134d 未及展开
- 无现成逆向工具链（IDA/x32dbg 缺失），objdump + 纯手工读汇编速度受限

## 题解学习
- 官方思路仅 Misc 篇（先知社区 AabyssZG xz.aliyun.com/t/13043，已核实无 Reverse 部分；微信链接无法抓取）；按题名+赛事检索无公开 Reverse 专项题解——闭门赛自证路径，与该赛事 R_r/Ez8or/Xcode 等既往调研结论一致
- learn-writeup 已入库（1 检查点）：矩阵校验题模板、坏指令+SEH 甄别法、"Congratulation!"双重误导陷阱、红鲱鱼 DLL 名

## 一得
矩阵校验逆向题的正确姿势：**先抄全常量矩阵再做线性代数反解，不追运行时流**；Debug CRT PE 的字符串交叉引用定位法（objdump + Python 按 VMA 正序重组）可复用；.data 首串常是红鲱鱼，IAT 是最快否决证据。

## 下轮
- 续攻 ezRE：反汇编 0x41134d 闭合 encrypt 生成逻辑（预计 10-15 分钟），或直接解矩阵方程求合法输入
- 或切换：zjsdxs 剩 Reverse-ida_pro（41KB，Readme 有 38 字节提示）/Web-ezWEB、fjgys 剩 Reverse-double(4.5KB)/mips2(12KB)

> AI生成
