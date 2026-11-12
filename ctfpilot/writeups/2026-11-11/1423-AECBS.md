# AECBS 刷题报告

- 仓库/题目: CTF-Archives/2026-NCO-Quals / crypto/Crypto-简单夺旗-AECBS（20 pts）
- Flag: `NCO26{875fe6ff-4e4d-4aa0-b3a7-39d982ee557a}`
- 耗时: 14:23:32 下载解压，14:27 解出，约 3.5 分钟；加复盘与落库约 12 分钟
- 通道: 快通道 `solve --mode auto` 150 秒超时 → 模型探索通道（ECB 字典反查）

## 标签
- 题型: crypto
- 识别信号: `AES.MODE_ECB` + 大量 `BIP39词: 32位hex` 行 + 尾部纯密文承诺
- 核心技术链: ECB 确定性 → 密文→单词字典 → 尾部 24 承诺映射 → md5(词拼接) → AES-ECB 解密
- 陷阱类型: 误以为需要 BIP39 词表或 2048^24 爆破
- 可迁移模式: ECB 承诺泄漏相等性，已知/小明文空间直接密文反查字典
- 变体预测: 换 CBC 固定 IV、换哈希、承诺覆盖不全需词表集合差集补词

## 一得
ECB 的“同词同密文”本身就是码本：16800 条标注对覆盖 2048 个唯一密文、0 冲突，尾部 24 个承诺 24/24 命中。另发现技能 learn-writeup 缺 ECB 技术标签，已补 `ecb|codebook|commitment` 匹配规则，recall 已验证可检索。

## 题解学习要点
- 赛事仓库 README 仅列题名与分值，未附题解链接。
- 两轮在线检索（题名+赛事+writeup、`"AECBS" NCO 2026`）无公开题解，赛事较新。
- 自评预期解与本次一致：无需完整词表，也无需爆破；先建密文→词映射即可。
- 更优路径：只解析尾部 24 个承诺做定点查表，建表可流式完成；若词表覆盖不全，用 BIP39 2048 词集合差集定位缺词（样本期望缺词约 0.56 个）。
- 边界：若换成 CBC 随机 IV，相等性泄漏消失，需另找 IV 复用或填充预言。

## 失败原因
- 引擎 `solve --mode auto` 在 679KB 文本上运行超过 150 秒被终止；原因是快通道没有“ECB 承诺字典”检测器，deep/mock 策略在该数据规模下不收敛。非题目失败，人工脚本约 1 分钟解出。

## 复现
- 脚本: `ctf-20min\2026-NCO-Quals__AECBS\solve.py`
- 依赖: `.temp\pydeps` 内临时安装 pycryptodome（未污染全局）
- 运行: `python -X utf8 ctf-20min\2026-NCO-Quals__AECBS\solve.py`

## 合规
- 仅静态解析文本与 Python 源码，未执行附件中的任何可执行文件；题目为公开练习题库。
- GitHub API 当次额度耗尽（0/60，14:41 重置），改用 jsdelivr data API 列仓库文件、jsdelivr CDN 下载附件，均不占 API 额度。

> AI生成
