# CTF 20分钟循环任务报告：Echo_Badges（NCO2026 国家队线下选拔）

- **仓库/题名**：CTF-Archives/2026-NCO-Final/crypto/Crypto-Echo_Badges（NCO2026——国家队线下选拔，2026-05）
- **选题过程**：2024-CCB-CISCN-Quals 已清空（仅剩 sxmisc 29MB 超限跳过）→ 探测组织其他赛事仓库：近端 9 个 2026 赛事仓库（qwbhy-iov/zjsdxs/NESTCTF/qwbs10/YCB/Zeekr/WQB/CCB2026）均 README-only 无附件，2026-jksn-quals 仅中学组 → 2026-NCO-Final（2.2MB，crypto/forensics/pwn/reverse/web 五类）取 crypto 首道未做题 Echo_Badges；GitHub API 限额耗尽（07:40 重置），改走 github.com 网页探测 + codeload 整仓 zip 提取题目目录
- **flag**：`NCO26{3525da2b-edde-489c-aed6-c03a68d13070}`
- **结果**：SOLVED（模型探索通道·格攻击）
- **总耗时**：约 5 分钟（时间盒 15 分钟内）；快通道 11.8s miss 后一次成型
- **题目**：attachment.py+output.txt。LWE（78 样本/32 维/q=16777259/误差±3）隐藏 badge_secret=sha256(flag)前16B 的 32 个 nibble；p、q 由该 secret 经 counter-mode SHA256（digest_stream）派生成 1024bit 素数，n=pq=2048bit，c=RSA₆₅₅₃₇(flag)。解 LWE→重建密钥→解密
- **解题路径**：快通道 miss（无 LWE 检测器）→ 主格嵌入 [I₃₂|Aᵀ; 0|qI; 0|-b|16]（取 40 样本，dim=73，目标向量范数≈56 vs GH≈19700，巨大 gap）→ sympy `DomainMatrix.lll()` 198s 得 (s,-e,16) → s 对全部 78 条方程复核全过 → 逐行复刻 prime_from_badge（含 next_prime 与 gcd 调整循环）重建 p,q，p·q==rsa_n 硬验证 → d=65537⁻¹ mod φ 解密
- **七维标签**：
  - 题型：crypto（LWE + 派生密钥 RSA）
  - 载体：python 生成脚本 + 大数表 output.txt（静态附件）
  - 识别信号：A 矩阵+b 向量+q/error_bound 显式声明 + 秘密 nibble 化 + 哈希流派生素数
  - 核心技术链：LWE 小秘密小误差 → 主格嵌入 LLL → 秘密 nibble 恢复 → KDF 复刻重建 RSA → 模幂解密
  - 陷阱类型：无混淆，纯构造题；唯一坑是 LLL 实现选型速度
  - 可迁移模式："解出中间小秘密→复刻生成代码重建后半链"的两段式出题，前段格攻击/后段脚本镜像即可全链验证
  - 变体预测：误差升到 ±2⁷ 或样本压到 n+2 → 需 BKZ-20/30；秘密非小（均匀 mod q）→ dual attack；q 特殊近 2 的幂 → rounding attack
- **题解学习**（两轮检索，NCO2026 线下选拔新赛事无公开题解，repo README 无解法链接，学时约 3 分钟）：
  1. 自主推导即正解：格嵌入常数 C≈秘密量级、样本取 n+8 即可、LLL 后按末位符号翻短向量——全链一次通过
  2. 对照更优路径：`skills/crypto.md` 已记录 python-flint `fmpz_mat.lll()` 秒级，本次误用 sympy LLL 耗 198s（d=73）；若预热时读过技能卡可省 3 分钟——已把"d≥70 一律 flint"写回技能卡 LLL 条目，闭合
  3. learn-writeup 落库 1 checkpoint + 1 技能卡（含失败路径与变体）
- **每题一得**：LWE 小秘密小误差题的确定性打法——嵌入基 [I|Aᵀ; 0|qI; 0|-b|C] 一次 LLL 定 s（gap 千倍级 LLL 必中），样本 n+8 起步先小后全量复核；KDF 派生素数题以 p·q==n 作最强中间验证
- **固化候选**：fastpath/deep "LWE 小秘密"检测器——output 含 q/A/b/error_bound 四元组即自动建格出 s；LLL 后端默认 python-flint（.temp 依赖）
- **失败路径**：无实质失败；快通道对格题 11.8s 即让位探索通道，符合分工预期
- **下轮候选**：2026-NCO-Final 同目录 Crypto-Midnight_Tapes、Crypto-Torus_Locker，或 pwn/reverse/web 类；备选 2025-CCB-CISCN-Quals（API 限额恢复后再列目录）

> AI生成
