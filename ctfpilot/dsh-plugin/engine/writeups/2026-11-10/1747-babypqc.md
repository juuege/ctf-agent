# CTF 20min 轮次报告 — babypqc

- 时间: 2026-11-10 17:47
- 来源: CTF-Archives/2024-CCB-CISCN-Quals / attachments/babypqc.zip（41.9KB）
- 题目: babypqc（crypto，477pt，全赛仅 2 解）
- 判定: **未解出（无远程靶机，flag 不可离线取得；攻击链已完整推导并验证关键结构）**

## 结果
- flag: 无（题目为纯服务端交互题，附件不含 flag.txt / 密文，需 `remote(host,13337)`）
- 耗时: 快通道 80.7s NOT SOLVED（5450 attempts）；模型探索 ~12min；题解检索 ~3min
- 产物: `writeup_babypqc.md`（攻击链 + 失败路径 + 变体预测），已 `learn-writeup` 入库

## 标签（七维）
1. 题型: crypto / 服务端交互（PQC 签名系统）
2. 载体: task.py + libpqcrystals_dilithium2_ref.so（ctypes 调用 Dilithium2）
3. 识别信号: `getrandbits` 混用 + `ForkingMixIn` + 参数 `delta=1184 beta=256 tau=704`（=37/8/22 个 32 位字）
4. 技术链: IFP 隐式分解 N_i=(p+d_i)(q+d_i) → 反解 d_i → 拼 624 字 MT 状态 → untemper → 预测 m/num → 用 m 换取合法签名 → hex 大小写变形绕过去重
5. 陷阱: H 的 1250 条 LWE 方程是干扰项（1250<2048，且得 s1/s2 仍缺 rho/t0 无法签名）
6. 可迁移: 见到 `from random import getrandbits` + 分叉服务器，先算「输出字数 vs 624」，命中即 PRNG 状态恢复题
7. 变体预测: 改 ROUND 破坏 624 对齐；在 handle 内 reseed 使跨连接恒定失效；修掉 case/zfill 缺陷

## 一得
- **字账即指纹**：`8+37+37+25×22 = 632 = 624+8`，跨一次 twist，缺口恰好是 `self.m` 的 8 个字——出题人用参数把「MT 一整轮状态 + 一条待签名消息」缝在一起。实测 CPython 字消耗（idx 624→8→45→82→…→610→8）与推导一致，这类参数巧合可直接当作正解路标。
- **ForkingMixIn 把 PRNG 状态复制成共享密钥**：父进程不消耗随机数 → 每个连接 m、num、N 完全相同，泄漏算一次即可复用，且能在 A 连接恢复、B 连接利用。

## 题解学习要点
- 公开题解缺失：仓库 README 仅给「delta beta tau」提示；CSDN《2024 CISCN&长城杯 Crypto WriteUp》只覆盖 rasnd/fffffhash（均已做）；online_search 与 GitHub 仓库搜索（`babypqc` 命中 0）均无 writeup。
- 自学补齐（关键两点，来源为推导 + 本地实测）：
  1. `bytes.fromhex` 大小写等价 + 等长时 `zfill` 不填充 → 同一签名的多个大小写变体是「不同字符串、相同字节」，直接绕过 `len(set(signatures)) == len(signatures)`，把 1 个真签名放大成 num 个。
  2. 签名预言机的正确用法不是伪造，而是**先把 m 预测出来再让服务器签 m**——难点从「PQC 伪造」降级为「PRNG 预测」。
- 待补：IFP（Sarkar–Maitra）格参数与实现未实跑（本机无 fpylll/sage），登记为下一轮同类题的前置准备。

## 失败原因
- 题目无远程地址，附件只有源码与 .so，flag 仅存在于服务器 `flag.txt`，离线不可能取得。
- N_0 为 2932 位、仅知低 282 位（< n/4≈733），单模数 Coppersmith 不足；正解须用多模数 IFP，环境缺格工具，未在 15min 内实跑验证。
- H 的 LWE 路线耗时约 4min 后判定为干扰项并止损（1250 方程 vs 2048 未知，且无 rho/t0 仍不可签名）。

## 附注
- `learn-writeup` 抽取的技术标签仍只给出 "RSA modulus/factoring analysis"，未抓到「MT19937 状态恢复 / IFP / 签名去重绕过」关键词，第二次命中同类抽取缺陷，登记为固化候选（建议在抽取器中加 PRNG-MT、隐式分解、编码等价类三组关键词）。

> AI生成