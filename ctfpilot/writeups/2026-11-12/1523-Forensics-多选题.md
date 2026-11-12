# CTF 20min 轮次报告：Forensics-多选题

- 赛事/仓库: CTF-Archives/2026-NCO-Quals（forensics）
- 题目: Forensics-多选题（10pts，纯题面无附件）
- 状态: SOLVED
- FLAG: `ACE`
- 耗时: 约 5 分钟（时间盒 15 分钟内；含 GitHub API 限流规避）

## 解题过程

1. 选题：done.json 124 条，2024-CCB 18 附件 API 复核穷尽 → NCO-Quals 五类目探测，crypto 全做完，forensics 目录序第一未做题即本题（pwn/reverse 列举遭限流，不影响选题——forensics 在目录序中更靠前且已确认未做）。
2. 下载：api.github.com 限流（remaining=0，15:28 重置）→ jsDelivr data API 列目录 + CDN 下载 description.txt，25 秒完成，未动用重试配额。
3. 通道：引擎 `solve --mode auto --write-trace` 3.0s/58 attempts NOT SOLVED（quiz 无检测器，预期）→ 语义判定定案。

## 语义判定（电子取证 5 选项）

| 选项 | 判定 | 依据 |
|---|---|---|
| A | ✓ | 篡改 PNG IHDR 宽/高字段隐藏图像内容，取证/隐写经典事实 |
| B | ✗ | "时间戳无法被用户态工具篡改"绝对化错误：$SI 时间戳可被 timestomp 类用户态工具篡改，取证时间线须校验可信度 |
| C | ✓ | `tcp.stream eq N` 隔离会话 + Follow TCP Stream 重组应用层数据，Wireshark 标准功能 |
| D | ✗ | 前半句 RSA 密钥交换+私钥可解密正确；后半句 ECDHE 同理可解错误——前向保密下会话密钥由双方临时密钥派生，服务器私钥不可解密（需 SSLKEYLOGFILE/临时私钥） |
| E | ✓ | 曾加载执行的文件即使磁盘被安全擦除，仍可从内存转储恢复（Volatility dumpfiles 类场景） |

## 题解学习要点

- README 仅分值清单无解法链接，在线检索（"NCO2026 入围测试 writeup 取证 多选题"）无该题公开 writeup——闭门赛，与既往轮次一致。
- 语义判定自证对照无漏点：5 选项均为取证教材高频考点（图片隐写/timestomping/流量重组/TLS 解密条件/内存取证），无争议项。
- learn-writeup 落库 1 检查点 + 1 技能卡（标签：半对半错选项陷阱、绝对化措辞判错）。

## 失败原因

无。引擎 NOT SOLVED 属 quiz 题型预期，非失败。

## 一得

D 选项"半对半错"结构（RSA 可解✓ + ECDHE 不可解✗ 拼成"同理"）是新的干扰项范式：前半句建立可信度，后半句偷换条件——判选项必须拆到每个技术断言独立核证，不能因前半句正确而整体放行。

## 下轮候选

- NCO-Quals：reverse 单选题/多选题、forensics 已清、pwn 超级无敌扭蛋机（20pts 带附件）、web 校园表白墙/EasyLogin。
- 注意：api.github.com 限流 15:28 重置，下轮选题时 pwn/reverse 目录列举需补验。

> AI生成
