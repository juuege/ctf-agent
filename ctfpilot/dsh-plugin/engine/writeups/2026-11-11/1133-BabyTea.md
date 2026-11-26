# BabyTea（2026-jksn-quals 中学组 Reverse/3）

- 时间：2026-11-11 11:33
- 题目：app-debug.apk（Android，JNI + 自定义 TEA）
- 结果：**SOLVED**（手动补位通道；引擎快通道/深通道均 NOT SOLVED，566 次尝试 24.2s）
- 耗时：约 28 分钟（超 15 分钟盒，主因：无 jadx/java，改走 capstone + androguard 现场搭工具链）

## flag
- 密码学验证的 48 字节：`flag:Congratulat1ons_y0u_4re_r3ally_go0d_4t_jni_`
- 长度校验要求 56 字节，但只加密校验前 48 字节（末尾 8 字节不参与比较，属设计冗余）；按语义补全推测为 `...jni_t34_t1me` → `flag:Congratulat1ons_y0u_4re_r3ally_go0d_4t_jni_t34_t1me`（该 8 字节无法用算法验证）

## 技术链（实测）
1. APK 解包：classes2.dex（6.5KB）+ lib/{arm64-v8a,x86_64,armeabi-v7a}/libbabytea.so（6KB）
2. `LogicGate.<clinit>`：state=0、armed=false；`StarterProvider.<clinit>` loadLibrary("babytea")
3. JNI_OnLoad：FindClass(LogicGate) → 静态 `arm(int)` 传 0x6a09e667 → 结果 K = ret ^ 0xbadc0ffe 存入 .bss
4. Java `arm(x)`：`state = rotl32(x ^ 0x7F4A7C15, 9) + 0x6D2B79F5 ^ 0x13573DDF`，返回 `rotr32(state ^ 0x31415926, 3)`
5. Java `material()`：armed 后由 state 派生 4 个 int（分别用 XOR/加法 + rotl5/rotr11/rotl17 与 0x243F6A88 等常量）
6. native check(String)：长度必须 56；取 material()[0..3]；密钥字 `key[i] = rotl32(K,{1,6,11,16}) ^ material[i] ^ {0x9e3779b9,0x7f4a7c15,0x6d2b79f5,0xc2b2ae35}`（NEON ushl 正负位移拼出 rotate）
7. 自研 "BabyTEA"：64 轮，sum 初值 0x8dde2e40/0xefa6b587，delta 0x61c88747；轮内
   - `b += (((a<<4)^(a>>5))+a) ^ (s1 + key[(s1>>11)&3])`
   - `a ^= (s2 + key[s2&3]) ^ (((b<<4)^(b>>5))+b)`（注意第二半是 XOR 不是加法）
   - 密钥下标用 `bfi` 改指针 bit2-3 实现「sp+4*idx」取字，是混淆点
8. 比较表 .rodata@0x5e0 共 12 字（48 字节），逐块 OR 累积判定全等
9. 逆向：按轮逆推（先减 s2 项还原 a，再减 s1 项还原 b），得明文并正向重放校验通过

## 标签（七维）
- 类型：reverse / android / crypto-tea
- 输入：apk(elf-so + dex)
- 技术：JNI 静态注册、dex 静态分析、ARM64 反汇编、自定义 TEA、常量派生密钥、指针位运算混淆
- 工具：capstone(arm64)、androguard、自写 TEA 逆推
- 难度：中（逻辑清晰但需现搭工具）
- 失败点：无 jadx/java 环境、引擎无 Android/TEA 检测器
- 复用点：JNI+dex 双端密钥派生、非标准 TEA 变体逆推模板

## 一得
「native 校验 + Java 派生常量」类 Android 题，先读 `.so` 的 rodata 定位算法骨架（位移表/常量表/dt表），再用 androguard 读 dex 算派生值，比先啃 dex 快一个数量级；TEA 变体逆推要按「轮内两半不对称」逐半还原，不能套标准 TEA 解密。

## 失败原因（引擎）
- triage 误判 category=pwn（.so 是 ELF），快通道无 APK/JNI/TEA 检测器；mock 深通道只做 strings/熵，无法反汇编 ARM64。

## 题解学习（收尾检索）
- 仓库 README/描述仅一句题面，无解法链接；`online_search` 搜"2026 金科赛 中学组 BabyTea writeup"无相关结果（赛事过新）。本次以自证闭合（正向重放 + 长度/校验路径一致）替代对照题解。

## 变体预测
1. 把 delta/sum 初值换成随机常量 → 仍可逆推，改用符号搜索或动态 trace 取常量
2. 增加 CRC/长度自校验 → 需同时满足尾部 8 字节约束
3. 换 XTEA/XXTEA 结构但保留 bfi 取 key 混淆 → 逆推公式改按 XTEA 逆序
4. 把 material() 改为 ContentProvider 查询（本题 query 是空壳）→ 需模拟 provider 返回值

> AI生成
