# GY 循环任务：MatCipher（NCO2026 入围测试 · Crypto-中等夺旗 40pts）

- **状态**：SOLVED
- **flag**：`NCO26{dfd13ce1-6fdc-4e62-894d-2e13baad9867}`
- **耗时**：约 6 分钟（盒内 15 分钟）；题解学习 + 收尾 4 分钟
- **来源**：CTF-Archives/2026-NCO-Quals（crypto/Crypto-中等夺旗-MatCipher，1574B）——CCB 仓库已清空（仅剩 sxmisc 29MB 超限跳过），切换至组织内新赛事仓库；题目附件经 api.github.com raw media type 下载（raw.githubusercontent 本机不通）

## 解题路径
1. 知识预热 recall crypto + 快通道 `solve --mode auto --write-trace`：3.4s / 104 次尝试未中（引擎无此检测器，意料中）。
2. 模型探索：attachment.py 为 Hill 密码退化形态——128B 已知明文分 16 组，每组与 8 维 key 内积 mod 257 → 16 方程 8 未知数。
3. 模 257 高斯消元（逆元 pow(x,255,257)）→ key=[79,170,244,185,229,201,71,79]，16 方程全验证通过。
4. md5(bytes(key)) → AES-128-ECB 解密（.temp 装 pycryptodome）→ 去填充得 flag。

## 七维标签
crypto / hill-cipher / known-plaintext / linear-system-mod-p / Zmod(257) / gauss-elimination / md5-key-derivation / AES-ECB / sage-reimport

## 一得
已知明文×行向量内积型 Hill 密码：方程数≥未知数即满秩可解，模素数高斯消元纯标准库秒出；Zmod(257) 下 key 元素全<256 才能进 bytes()，题面未崩本身就是隐含可解性证据。

## 题解学习
- 仓库 README 仅题面清单，无题解链接；online_search 两轮（题名+赛事+writeup / NCO2026 writeup）确认无公开题解（2026 新赛事，国际网络安全奥林匹克中国区入围测试）。
- 本题自主推导即经典打法，无更优路径；learn-writeup 落库 6 checkpoints + 2 技能卡（MatCipher_NCO2026）。

## 失败原因与变体预测
- 快通道 miss 属预期边界；固化候选："已知明文+模素数线性方程组"确定性检测器（方程组满秩→高斯消元→密钥派生→AES 解密链）。
- 变体：未知数>方程数需 LLL 补信息；模 2^k 非素数需处理零因子；key 出现 256 则 bytes() 崩。
- 工程侧注意：CTFPilot-v0.1 已无 references/ 目录（技能卡在 skills/），SKILL.md 的 references 索引已过期，待下轮核对修复。

## 产物
- writeup：`ctf-20min\2026-NCO-Quals__MatCipher\writeup-matcipher.md`（已 learn-writeup 落库）
- 下一轮候选：NCO2026 仓库剩 AECBS / EasyRSA / ROT13 / 残像 / 碎影 / bitdance 等 25 题

> AI生成
