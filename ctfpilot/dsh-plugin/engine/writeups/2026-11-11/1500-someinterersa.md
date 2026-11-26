# CTF 20min 第 61 轮：someinterersa（2025-WQB-Quals 湾区杯初赛 Crypto）

- **题目标识**: CTF-Archives/2025-WQB-Quals/crypto/someinterersa（附件 372B，17:00 轮选题）
- **结果**: ❌ 未解出（数据不可得 + 算法还原不完全）
- **flag**: 无（归档仅 server.py 源码，N/e1/e2/c 输出在赛时服务端，flag 固化于 secret 模块，归档未含 output）
- **总耗时**: 约 20 分钟（超 15 分钟解题盒边缘收尾：选题排障 6 分钟 + 解题分析 9 分钟 + 收尾 5 分钟）
- **标签**: crypto / RSA多指数小私钥 / 素数私钥d / gcd(p-1,q-1)=2 / 归档缺数据 / 交互源码归档题

## 过程要点

1. **选题**: done.json 60 条；CCB/QWBS/ICSC/JKSN 场次已尽，按上轮记忆推荐选 2025-WQB-Quals（湾区杯初赛，非强网杯）。API 配额 0 次时距重置仅 35 秒，等待后恢复，未触发跳过条款。
2. **解题**: 快通道 auto NOT SOLVED（2.7s/50 次，无 RSA e/d 结构检测器，预期内）；模型通道读源码识别结构：d1/d2 为 880-bit 素数私钥，e_i=d_i^{-1} mod phi，flag 以 65537 加密；关键缺口是归档无 N/e1/e2/c 数值输出。
3. **模拟验证**（纯标准库 Miller-Rabin，小参数复现题面结构）：
   - `(e1·e2)^{-1} mod phi == d1·d2` 成立（组合指数的私钥恰为素数积，无模约简）；
   - 单组 d=2^880 > N^{1/4}=2^512 → Wiener 失效，> N^{0.292} → Boneh-Durfee 失效；
   - 组合 K=(E·D-1)/phi = k1+k2+k1·k2·phi → 组合指数使 Wiener 比恶化（反直觉，实测确认）；
   - 推断官方解法应为多指数联立小私钥格攻击（Howgrave-Graham & Seifert，r=2 理论界 N^{5/12}≈2^853 仍略小于 880，题设必含素数 d 的附加结构利用，模拟观察到 gcd(k1,k2)=3>1 不罕见，公因子方向可能是关键）。

## 失败原因

- **主因：数据不可得**——附件仅含从 secret 导入 flag 的 server.py，题目实为赛时交互/服务端打印 N/e1/e2/c，CTF-Archives 归档未含输出数据，真实密文无法获取（与 jksn 笔记系统同模式，第 2 例）。
- **次因：检索方向错误浪费一轮**——WQB 误当"强网杯"搜索，实际是"湾区杯"（先搜后读 README，顺序反了）。
- **参考：无公开题解**——4 轮检索（题名精确 / 题名+CTF writeup / 湾区杯+crypto / getPrime(880)+e1e2 技术词组合）；X1cT34m 初赛 WP 与 CSDN 部分 WP 的 Crypto 均只覆盖 new_trick，未含本题。

## 题解学习要点

- 同赛事 new_trick 解法（X1cT34m）：四元数群离散对数 BSGS + 范数归约解 2^50 秘密，md5 为 AES-ECB 密钥——与本题主线无关。
- 本题可确认的学习点均为自主验证产物：组合指数恶化关系 K=k1+k2+k1·k2·phi；素数 d 双私钥的攻击面收敛到多指数联立格 + k 公因子。
- learn-writeup 已入库：1 检查点 + 1 技能卡（crypto / RSA modulus/factoring analysis）。

## 一得

**"归档完整性预检"**：`from secret import` + `print 输出` + 无 output.txt + 无常量数据的源码归档题，数据在服务端不可复现，应在前 2 分钟内识别并止损或换题——固化为 solve 前置检查器候选。顺带修正流程：先读赛事仓库 README 拿准确赛事名（WQB=湾区杯）再定检索词。

## 下轮建议

- 2025-WQB-Quals 仓库剩余未做题：forensics/checkwebshell（流量+SM4，有公开WP可对照）、web/easy_readfile（phar链，有专项CSDN题解）、pwn/ood_canary（有WP）等——均有数据可得且题解可学；或按 05:20 轮首荐转 2025-WQB-Quals 之外的 jsDelivr 已同步仓库。
- 本题留续做条件：若能从公开渠道获取该题真实 N/e1/e2/c 输出（如其他选手 WP 抄录），10 分钟内可完成多指数格攻击实验（fpylll 装于 .temp）。

> AI生成
