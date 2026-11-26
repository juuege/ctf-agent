# 2026-11-11 06:46 · 2026-ICSC-ICS-Champ / GY10-VCL-FW（Reverse）

## 结果
- **NOT SOLVED**（超盒收尾：解题约 50 分钟，盒超时 35 分钟；flag 未取得）
- 题面（仓库 README）：车机 OTA 包提取加密 MCU 固件 `firmware.enc` + 固件校验器 `fw_check`，逆向 fw_check 弄清真实加解密路径，离线解开 firmware.enc

## 耗时
- 总计 ~50 分钟：选题/下载 4 分钟，快通道 solve 32s（NOT SOLVED，预期内），静态逆向+白盒求逆 ~35 分钟，模式组合试解 ~8 分钟，题解检索 3 分钟
- 超时主因：白盒 16 轮 SPN 求逆虽完成（MITM 全列唯一解），但 enc 密文的分块/模式假设全部未命中，模型定位偏差未能在盒内闭环

## 已还原结构（纯静态，未执行任何题目可执行文件）
- **自解密段**：`crypto` 段 0x3000-0x7343 由 .init_array 构造函数用 key@0x80d0（`0123456789abcdeffedcba9876543210`）按 `data[j]^key[j&0xf]` 解密后 mprotect RX
- **反调试双层**：ptrace(PTRACE_TRACEME) + rdtsc 4096 轮计时环（>5M ticks 判调试）→ 检出则跳过解密，main 调用 crypto 段即崩（解密绊线）
- **main@0x13b0 校验链**：256B 输入 → "VCLFW" 头 → payload_len(u32@+8, ≤240) → CRC32(payload)==u32@+0xc → stage1=SPN(block^K0)（K0@0x8040=`00112233…ddeeff`，逐 16B 块）→ bcmp 诱饵@0x8150（**结果被丢弃**）→ 真校验 0x1f40 变换输出 32B == 0x8050 区（dword0=4d3dff35、dword2=4bd846bb、余多零，自定义而非 SHA256）→ 打印 "MATCH"（输出经 ^0x5a）
- **SPN=白盒 16 轮**：S 层表 0x8800（16轮×16位×256B，全双射）→ 位置置换 SRC=[7,15,1,11,8,6,9,0,10,12,14,2,3,13,4,5] → 类 MixColumns 0x18800（y=A[x0]^B[x1]^C[x2]^D[x3]，表非仿射）
- **MITM 求逆**：A^B(2^16) 与 y^C^D 相交，64 组列映射全部唯一解 → 完整 SPN 逆变换（~45s/256B，numpy 临时装 .temp）
- **firmware.enc=296B** = VIN"WVWZZZ3CZJE12345"(16) + 8B"1122334455667788" + **K0(16B)** + 密文 256B

## 尝试与失败
- ECB/CBC/CTR × {逆 SPN, 正向 SPN} × 偏移{0x28,0x20,0x18,0x10,0x08,0x00} × IV/nonce{enc 内嵌,K0,VIN,zero} 全部未出 "VCLFW" 头
- 0x8150 诱饵镜像求逆亦为乱码 → SPN 模型仍有偏差（疑点：SRC 手工追踪 / Mix 表 lane 序 / 0x1f40 隐含 stage1 后另有变换）
- AES-128 快测（K0/VIN/K8Ni 等 8 密钥 × 5 偏移 ECB+CBC）未命中，排除简单 AES 路径

## 题解学习
- 仓库 README 题面确认方向正确（"弄清真实加解密路径，离线解开"）；online_search + GitHub API 搜 `2026-ICSC` / `VCL_FW` / `工控安全锦标赛 writeup` 均无公开题解（新赛事，仓库 1 star 1 commit 无 issue）
- learn-writeup 落库 2 卡（解密绊线模式 + 白盒 MITM 求逆法），闭合学习环

## 一得
- 自解密段+反调试构造函数 = 「解密失败即调用崩」绊线组合；白盒表密码求逆走 MITM(2^16 相交) 比解析线性层更快更稳；固件类文件先按 16B 对齐扫描找内嵌 IV（本题 IV=K0 躺在 enc 文件里）

## 变体预测与下轮候选
- 真实路径可能在 stage1 后接 0x1f40 中间变换，或密钥由 VIN+K8Ni@0x84f0 组合；下轮用 unicorn **内存仿真** 0x7000 验证 fwd 模型（内存内分析、不执行宿主二进制）
- 同仓库剩：GY10-反应釜温度与压力融合控制（369KB rar，S7 梯形图计算题）

## 标签
`#reverse #whitebox-cipher #self-decrypt-section #anti-debug-ptrace-rdtsc #spn-mitm-inversion #firmware-ota #crc32-check #custom-section #vin-key`

> AI生成
