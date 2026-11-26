# 2026-NCO-Quals EasyRSA 解题报告

- 时间：2026-11-11 17:20–17:27
- 题目：CTF-Archives/2026-NCO-Quals/crypto/Crypto-简单夺旗-EasyRSA（20pts）
- 附件：attachment.py、output.txt（Zip 1.9KB）
- 结论：**SOLVED**
- flag：`NCO26{9042a4f9-f106-4f07-b11a-af3273d19e63}`

## 耗时
- 选题/下载：约 2 分钟
- 解题：约 2 分钟（15 分钟盒内）
- 题解学习/落库/报告：约 3 分钟，总耗时 7 分钟（20 分钟硬截止内）

## 技术路径
1. 源码给出 `b = q - p*a`，故 `q = b + p*a`。
2. 代入 `N = p*q` 得 `a*p^2 + b*p - N = 0`。
3. `D = b^2 + 4aN`，`math.isqrt` 验证为完全平方；`p = (-b + √D)/(2a)`，整除校验通过。
4. 常规 RSA 求 d 解密，重加密回验 `pow(m,e,N)==c` 为 True。

## 引擎通道结果
- `solve --mode auto --write-trace`：triage 判为 misc（confidence 0.46），fast/deep 全为编码/压缩类检测，4.2s 返回 NOT SOLVED（118 次尝试）。RSA 代数关系不在引擎确定性检测器覆盖内，由模型探索通道补位 2 分钟解出。

## 题解学习要点
- 赛事 README 仅列题名与分值，无解法链接。
- 两轮在线检索（“NCO2026 入围测试 EasyRSA writeup 题解”“NCO2026 入围测试 crypto RSA 题解”）均未命中本题公开 writeup，NCO2026 入围测试暂无公开题解；改为自证：判别式完全平方 + p|N + 重加密一致，三重验证闭合。
- 可迁移：凡输出 `b=q-p*a` 这类仿射关系，先把 q 消元成 p 的二次方程，再判判别式，不要直接分解 N。

## 标签
- 识别信号：output 同时给 N,e,c,a,b；b 与 q 同量级
- 陷阱类型：直接分解 1792bit N；把 b 误当 q mod p
- 可迁移模式：已知 q=b+pa ⇒ N=p(b+pa)，二次方程判别式开方即分解
- 变体预测：b=p-aq、只给 a/b 高位、多组 (a_i,b_i) 线性关系

## 一得
RSA 题看到额外参数先做“消元成单变量方程”而不是“分解 N”；本题 a 为 256bit 恰好使 p 的二次方程可解，参数位数关系就是出题人的入口提示。

## 失败原因
无（引擎未解属边界外，模型通道已解出）。若未解出，下一手为：检查 b 是否为 q 的线性变换、尝试 Coppersmith（已知高位场景）。

> AI生成
