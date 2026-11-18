# N1CTF 2025 n1share — 双通道共享错误位置的 RS 超界解码（Coppersmith-Sudan 2003）

## 题目形态（deployable-static crypto，本地材料齐全家族）

- 仓库：Nu1LCTF/n1ctf-2025::n1share（Nu1L 官方归档，第 8 次拉取）
- 附件 3 件：task.py（5.6KB）、ex.sage（3.4KB 作者侧 exploit，share 被脱敏为 `...`）、README.md（官方解法叙述 + 论文链接 people.csail.mit.edu/madhu/papers/2003/copper-conf.pdf）
- **与此前 7 连 remote 家族（bridge/n1flgsrv/N1drone/n1fhe/n1cat/this-challenge/bigfish）的本质区别**：FLAG 字面量虽是 `n1ctf{REDACTED}` 占位符，但 task.py docstring 内嵌**一次真实运行的完整输出**——share[1040]（520 点 × 2 通道，取值 0..520 全域校验通过）+ ct（32B hex，与 ex.sage line 110 硬编码完全一致）——**本地材料齐全，flag 可离线推导**
- 语义：两个 GF(521) 上 127 次多项式（key 的 256 字节分半做系数）在 1..520 处求值，**同一批 300 个位置**（`drunk = random.sample(range(1,p), 300)`）加均匀噪声；(n,k,w)=(520,128,300) 的双通道共享错误位置的 RS 解码

## 信号（recall 触发词）

- `random.sample` + 两多项式共享 drunk 位置集合 + `os.urandom` 密钥 + AES(md5(key), ECB)
- 附件带 ex.sage（SageMath）且 share 值被作者脱敏
- README 直接给论文链接 + Berlekamp-Welch 196 / Guruswami-Sudan 262 与 300 的差距分析
- c=2 通道共享错误位置的扩展 B-W 上界 = ⌊c(n−k)/(c+1)⌋ = 261 < 300（三条常规路线全不够）

## 解法路径（模型通道忠实复刻 ex.sage 的论文构造，纯 numpy）

1. **数据完整性三查**：share 1040 个数字（520 对）✓、min=0 max=520 ∈ GF(521) ✓、ct 与 ex.sage 硬编码逐字一致 ✓ → 判定非结构缺失，进入算法补位
2. **构造精确复刻**：l=1090、r=5、t=n−300=220（`assert t·r > l`：1100 > 1090 压线）；Mr = a+b+c<5 的 35 个单项式，Me = i+128·(j0+j1)≤1090 的 18240 行；A[(i,j0,j1),(e,(a,b,c))] = C(i,a)α^(i−a)·C(j0,b)y1^(j0−b)·C(j1,c)y2^(j1−c)，Kronecker 分块构造（18240×18200 int32 = 1.33GB）
3. **消元工程**：行主序原地前向消元，**延迟取模**（K_MOD=2000 步批处理，int32 累积界 5.4e8 < 2^31 安全余量 4 倍；主元列/主元行每步即时精确 mod）+ 分块外积减法（CH=1500 行控临时峰值 ~109MB）；本机（4 核 8GB 云机，无 GPU，实测有效带宽 ~4GB/s）12321s 完成全部 18190 个主元
4. **核提取与错误定位**：rank=18190、自由列 10；单随机向量回代（echelon 三角回代 O(n²) 秒级）→ **520 点分块（35/块）全零块 = 300 个，与理论错误数精确一致、一个不多一个不少**
5. **插值与解密**：220 个干净点拉格朗日插值 f1/f2（GF(521) 纯 Python O(k·t²)）——220 点全验证零误差；**f1/f2 系数全部 ∈ [0,255]（字节域强验证，key 字节本性的独立证据）**；key = bytes(f1 系数)+bytes(f2 系数)，md5 → AES-ECB 解密 ct
6. **FLAG**：`n1ctf{4lgEbra_he1p_m3_sOber_up~}`（呼应题面 "I want to share my key with everyone, but I'm drunk. (ﾟ∀ﾟ)つ🍷"）

## 陷阱与备忘

- **r=4 剪裁是死路**（实测否决）：条件 `l//r < t` 卡死 l≤879，#Me(879)=10079 < #Mr·n=15×520=10400，欠定条件不满足；r=3 更不可行（#Me(659)=4900 < 10×520）。**r=5 是论文构造的最小可行参数**，矩阵规模不可剪小——18502 行的"余量 40"不是浪费而是压线设计
- **Guruswami-Sudan 262 与共享位置扩展 B-W 261 都不够 300**：必须上 CS 2003 论文的 r-重根构造（等效条件 t·r > l 才能让 Q(x,f1,f2) 的 220 个干净零点压过 1090 度）
- **8GB 云机跑 1.33GB 稠密 GF(521) 消元的物理墙是内存带宽**（非 CPU）：外积减法 ~16B/元素 × 2.06e12 元素 ≈ 33TB 流量 @ ~4GB/s ≈ 3 小时；延迟取模把 % 成本从 4e12 降到 ~1e10 是唯一必要优化，多进程拆分不划算（重启沉没成本 + 复杂度）
- **fixture 教训（rsi-131 轮同日）**：答案文件放题目录会触发 raw-echo 守卫正确撤回——守卫工作正常，是 fixture 设计错误；RS 解码题的 flag 明文必须短于 N（m ≥ N 时 pow 截断高位）
- **share 数组的正则陷阱**：`re.search(r'share: \[([^\]]*)\]')` 一步提取 5012 字符行；task.py docstring 的 share 行恰好不换行，若换行需改 re.DOTALL

## Provenance

- 引擎 1.89s / 25 attempts 如实未解（fastpath 无数学解码臂；raw-echo 守卫正确撤回 task.py 的 `n1ctf{REDACTED}` 占位符逐字命中——占位符不是 flag，撤回正确）
- 模型通道补位解出：Coppersmith-Sudan 构造（CS 矩阵 18240×18200 GF(521) 前向消元 12321s + 回代/插值/解密 5s），flag 经 AES 解密流完整推导（非 raw-echo：flag 在密文内，原始附件无逐字副本）
- 模型通道墙钟 12900s（02:21:33 引擎未解定格 → 05:56:44 解出），account-model 如实入账

> AI生成
