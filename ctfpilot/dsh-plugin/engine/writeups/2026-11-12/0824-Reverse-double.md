# CTF 20分钟循环：2023-fjgys Reverse-double — SOLVED

- **选题**：CCB 18 题穷尽 → zjsdxs 仅剩 Web-ezWEB（19MB Spring Boot jar，服务端 flag 题无靶机，按边界跳过）→ fjgys 第一道未做题 Reverse-double（zip 4.5KB）。
- **flag 主体**：`K1y_xXteA_1s_god`（16 字节裸串；按福建赛事惯例提交格式 `flag{K1y_xXteA_1s_god}`）
- **耗时**：约 14 分钟（选题 3 分钟 / 解题 8 分钟 / 收尾 3 分钟）
- **通道**：引擎 auto 3.9s NOT SOLVED（无 ELF 逆向检测器）→ 模型补位静态反汇编全链闭合，纯标准库解密脚本零依赖。

## 解法

1. ELF64 C++ 静态小程序，不执行；strings 见 "Input your flag/key"、"success!"，rodata 内联常量碎片。
2. WSL objdump 反汇编（先 Windows 侧输出截断 343 行，改 WSL /tmp 落盘完整 1068 行）。
3. main 通读：两输入提示语**与实际用途交叉**——"flag" 输入是第二层 key，"key" 输入才是 flag 明文；明文前 16 字节先经 **XXTEA 字节变体**（delta=0x9e3779b9，sum 64 位累加，108 轮，key="flag"，MX 字节化 result = A ^ (B+D) ^ (B+E)）加密后与内联 16 字节常量 `0c787ad2c424c320ed25b7c9671bb23d` 比对；再经 SM4 结构轮函数（S盒@0x2140 + rotl 13/23 线性变换，32 轮 Feistel）用 key 输入产出 32 字节与常量比对。
4. 第一层直接逆解：轮逆序 108→1、块内逆序 j=15→0（v29 用刚恢复的 P'[j+1]，最后 j=0 用恢复后的 d[15] 作滚动 prev），`K1y_xXteA_1s_god`，重加密回验一致。
5. 第二层不阻断：第一层已唯一确定明文，key 输入未知无需暴力。

## 题解学习

- 闭门赛无公开 writeup（检索证实，与 SSSMMM5 轮结论一致）；无更优路径可对照，自证路径即最优。
- learn-writeup 已落库（1 检查点 + 技能卡 Reverse-double-fjgys2023-00）。

## 一得

逆字节版 XXTEA 的推导顺序模板：轮逆序 + 块内逆序（先最后块拿 C[14]/C[0]，再 j 递减用刚恢复值，最后 j=0 用恢复后的尾部作滚动 prev）；`xor al,`/`add dl,` 只影响低位时 32 位中间量可安全 8 位化（低位加法无向下进位）。

## 标签

reverse, xxtea-byte-variant, custom-feistel, sm4-like, inline-constant-cmp, objdump-static, pure-stdlib-decrypt

## 失败原因

无（本解闭合）。附：Windows 侧直接 objdump 输出会截断，须 WSL /tmp 落盘再回拷。

## 下轮候选

fjgys 剩 Reverse-mips2；zjsdxs 剩 Web-ezWEB（服务端题，挂起）；jksn/ICSC 等其他仓库。

> AI生成
