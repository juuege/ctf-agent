# CTF 刷题报告：2025-WQB-Quals / new_trick（四元数离散对数）

- **flag**: `flag{ef9b2a64b3ead115a48ee0b842dc19ed}`
- **结果**: SOLVED（模型补位通道，纯标准库 PH-DLP，未执行任何附件可执行文件）
- **耗时**: 选题/下载约 4 分钟；解题约 6 分钟（引擎 miss 4.1s + 补位数学推导与脚本）；收尾/学习/报告约 4 分钟；总约 14 分钟，时间盒内
- **赛事**: 2025 湾区杯网络安全大赛 初赛（院校组）crypto / new_trick「简单的数学知识」
- **标签**: crypto | 四元数 DLP | 群同态 | Pohlig-Hellman | BSGS | AES-256-ECB | 纯标准库

## 解题路径
1. 题面：`task.py` 定义四元数类（mod p 乘法），R = Q^secret，secret < 2^50；key = md5(str(secret)).hexdigest().encode()（32 字节 → AES-**256**-ECB）解密 flag 密文。
2. 关键同态：norm(x) = a²+b²+c²+d² mod p 满足 norm(xy)=norm(x)norm(y)，四元数群 DLP 归约为 F_p 上的 DLP：norm(R) = norm(Q)^secret。
3. 分解 p−1（试除 + Pollard rho）= 2·3·29·222587·1521613·4463413·q₂₀₁，squarefree，含一个 201-bit 大素因子。
4. 光滑部分 L0 ≈ 1.32e20 ≫ 2^50 ≈ 1.13e15 → 只对小素因子做 Pohlig-Hellman（每因子一次 BSGS，√f 步）+ CRT，**无需**在大素因子上做不可行 DLP；ord(norm(Q)) 不含 2，含 3/29/222587/1521613/4463413，CRT 得 secret = 895942422329，唯一解。
5. 验证：按题目四元数乘法复算 Q^895942422329 == R ✓（独立于 DLP 的整链闭合）。
6. md5 hexdigest 32 字节作 AES-256-ECB key（`.temp` 装 pycryptodome，不污染全局），解出 flag，明文带合法 flag{} 格式与 PKCS#7 填充。

## 题解学习要点
- 公开题解检索三路（online_search、GitHub code search 需认证 401、repo search 空）均无果——赛事小众，无现成 writeup；以自解双重验证（四元数幂复算 + 解密明文格式）代替对照。
- 更优路径评估：备选方案「特征值层双 DLP（四元数嵌入 F_p×F_p，λ1,λ2 各解一次）」与「四元数群直接 BSGS（2^25 baby steps，纯 Python 分钟级）」均可行但更慢/更复杂；norm 同态 + 「秘密上界 < p−1 光滑部分」是本题最短路径。
- 漏掉的技术点自审：无——32 字节 key → AES-256（而非 16 字节 AES-128）已正确处理，padding 验证通过。

## 一得
- **可迁移模式**：非交换群（四元数/矩阵群）DLP 先找乘法同态（norm/行列式）降为 F_p DLP；再问「秘密上界 < p−1 光滑部分？」——成立则纯 PH 秒解，完全不必惧怕 p−1 中的大素因子。
- **固化评估**：不固化（quaternion 类题目低频）；PH+BSGS 通用件经 recall 可复用，落库 writeup 已完成（1 checkpoint + 1 skill card）。
- **坑**：GitHub code search API 需认证（401），公共 writeup 检索只能靠 online_search 与 repo search；AES key 长度由 hexdigest 字节串长度决定（32B=AES-256），别默认 16B。

## 失败原因
- 无实质失败：CTFPilot 引擎 fast/deep NOT SOLVED（无四元数 DLP 检测器，预期内 miss），一次网络 IncompleteRead 重试成功；解题主路径一次通过。

## 仓库探测结论（编排记录）
- CTF-Archives/2024-CCB-CISCN-Quals 18 个附件已全部做完；组织 140 仓库中多数赛事库仅存 README（writeup 集），**2025-WQB-Quals** 为标准 attachments 结构（crypto/forensics/pwn/reverse/web 五类 14 题），后续轮次可继续在此仓库刷：someinterersa(RSA)、SilentMiner/bademail/checkwebshell(取证)、ssti/ez_python/easy_readfile(web)、minigame/hardtest/strangeApp(rev) 等。

> AI生成
