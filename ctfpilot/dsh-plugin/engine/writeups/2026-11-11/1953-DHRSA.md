# CTF 20min 轮报 — 2023-Bluehat-Quals crypto/DHRSA（SOLVED）

- **题目**: CTF-Archives/2023-Bluehat-Quals `crypto/DHRSA.zip`（2023 第七届蓝帽杯初赛，奇安信 datacon）
- **选题过程**: CCB 18 题已尽 → WQB 剩余可下载题穷尽（web 三题 easy_readfile/ez_python/ssti 附件均为空 README 在线环境题，forensics 两题外链失效，imi_mqtt 在线题）→ 探测组织其他赛事仓库 → 2023-Bluehat-Quals 为完整分类赛事仓且未做过，取第一道未做题 crypto/DHRSA（10.9KB）
- **FLAG**: `flag{a_trivial_r5a_bbbbbbackd00r_***712***_c3nt5}`
- **耗时**: 选题约 7 分钟；解题约 6 分钟（引擎 auto 12.7s/542 尝试 NOT SOLVED → 模型探索通道一次解出）；总约 19 分钟，20 分钟盒内收尾
- **标签**: crypto / hidden-modulus RSA / LLL integer relation / Pohlig-Hellman / 平滑 r−1

## 解题路径
1. 结构：p ≡ C·W^{t1}·X^{c} (mod r)，q ≡ W^{−t2}·X^{−c} (mod r)，C=g^{c}；n=pq，r/g 全隐藏；out.txt 泄露 62 组 (c_i, C_i=g^{c_i} mod r) Try 对
2. LLL（flint，行向量 (e_i | K·c_i)，K=2^620）得 61 条精确关系，max|a|=319 → P−N ≡ 0 (mod r)
3. 多关系 gcd 恢复 r（512bit 素数，6/6 Try 对按公式重构 p_i 素性验证通过）
4. r−1 全部素因子 ≤40bit → gmpy2 平滑分解；g=C_i^{c_i^{-1} mod r−1} 恢复底数
5. Pohlig-Hellman 逐位 dlog（同 q 复用 BSGS 表）得 x、w、L=log_g(n)；6 组合枚举（Δ×t1）命中 Δ=−1,t1=0 → p|n
6. RSA 解密 → flag；**重加密回验 pow(flag,65537,n)==ct 通过**

## 一得
- 已知指数的模幂泄露对 (c_i, g^{c_i} mod r) 可经 **LLL 整数关系 + 积差 gcd（r | ΠC_i^{a_i+} − ΠC_i^{−a_i−}）** 恢复隐藏素模数，62 对时系数仅 319
- 信号识别：`getStrongPrime` 导入而未用 = r−1 可平滑分解的暗示，PH dlog 一路通杀

## 题解学习要点
- 赛事 README 无解法链接；两轮在线检索（题名+赛事+writeup、初赛+crypto+隐藏模数）无本题公开题解，按模板**自证落库**（1 检查点 + 1 技能卡 DHRSA-00.json），证据链完整（结构验证 6/6、dlog 断言、整除分解、重加密回验）
- 检索中发现同赛事半决赛 Dual_ECDSA（知乎 J1an）为同源 EC 后门题型，可作后续参考

## 失败原因
无——一次解出；引擎 NOT SOLVED 属预期（隐藏模数 RSA 超确定性检测器边界，12.7s 即让位模型通道）

## 下轮候选
2023-Bluehat-Quals 剩余未做题：web/LovePHP、misc/ez_Forensics、reverse/Story、reverse/瓷砖计算机、pwn/heapSpary、pwn/takeway、取证目录

> AI生成
