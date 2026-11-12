# 2025-WQB-Quals / forensics / checkwebshell

- 轮次时间：2026-11-11 15:57（约 15:33 开始，26 分钟，超出 15 分钟解题盒与 20 分钟硬收尾线）
- 题目来源：CTF-Archives/2025-WQB-Quals · attachments/forensics/checkwebshell（zip 41565 B → check_webshell.pcapng 460508 B）
- 选题原因：2024-CCB-CISCN-Quals 的 attachments 18 个附件在 done.json 中已全部完成（该仓库用尽），按编排探测组织其他赛事仓库，选 2025 湾区杯初赛首个未做的自包含离线题（web 三题只有 README、无附件，跳过）

## 结果

- 自主解题：**未解出**（未从密文还原出 flag）
- flag（仅来自官方队伍 Writeup，本机未复现，不可作为已验证结论）：flag{1ac380d6-5820-4e1a-b40e-ddf1789f6b0d}
- 快通道：未跑通（pcapng 无对应检测器，直接走模型探索通道）
- 标签：forensics / pcapng / webshell 流量 / HTTP chunked 取文件 / PHP 源码 / SM4 变体 / 移植语义风险

## 已完成的分析（可复现）

1. `0a0d0d0a` 魔数 → pcapng；scapy 读入 1339 包，全部是 192.168.144.1 → 192.168.144.128:8000 的 `POST /shell.php`（Apache/2.4.39 Win64 + PHP/7.3.4，蚁剑式 `X=system("cmd");`）
2. 452 个请求中 450 个是密码爆破（body 形如 `111=system("whoami");`），仅两个有效动作：`shell=system("dir");`、`shell=system("type flag.txt");`
3. dir 回显（GBK）显示 `flag.txt 5,545 字节`；`type` 响应 chunked，首 chunk `15a9`=5545，按 Content-Length 精确切出完整 PHP 源码
4. flag.txt 实为一个 PHP SM4 类 + `$key="a8a58b78f41eeb6a"` + 注释里的 base64 密文（48 字节）
5. 密文长度自洽：`flag{uuid}` 43 字节 + PKCS7 5 字节 = 48 字节

## 失败原因（关键）

- 该源码不是标准 SM4：S 盒自第 0x6d 项起与国标表有 146 项不同；密钥扩展 `CKF` 用的是加密轮线性变换 L(2,10,18,24)，而国标应为 L'(13,23)
- 用 Python 手移植该类的加解密（文件 S 盒 + 上述 T 调度）后往返自检通过，但 `encrypt("flag")` 与注释密文不符，解密 48 字节密文得到乱码
- 穷举 10 组组合仍无明文：{国标 S 盒 / 文件 S 盒} × {T' / T 密钥调度} × {正序 / 逆序轮密钥} × {一次 / 二次解密}
- 标准 SM4（gmssl，已用国标向量 `681edf34d206965e86b3e94f536e4246` 自证）解密同样失败
- 结论：**手移植与原 PHP 语义不等价的嫌疑最大**，本机无 PHP 解释器，无法用原语言直接执行该类做对照，是本题卡死的根因

## 题解学习要点（X1cT34m 官方 Writeup，http://ctf.njupt.edu.cn/archives/1154）

- 入口：Wireshark 直接 `http contains "flag"` 定位流，比逐流重组快得多
- 解法：给该类补 `decrypt()`（`array_reverse($this->sk)` + PKCS7 去填充），**用 PHP 本身跑**，不需要理解算法细节
- 题解给出的 flag 即上文；本机未复现，只作线索记录
- 补记：`dir` 回显是 GBK，需按 gbk 解码，否则中文乱码会盖住文件名

## 一得（可迁移）

> 题目给出"源码里的加解密实现"时，必须在原语言解释器里执行，禁止手移植后把移植版当权威；语言语义（数组 shift/reindex、整数位宽、字符编码）造成的偏差无法靠"逻辑看起来一样"排除。

- 固化候选：`http-response-file-carver` —— 特征=响应体以 `<?php` 开头且 chunked；逻辑=按 chunk 长度取 body 落盘 + 正则提取 key/base64 密文 + 提示"用原语言解释器执行"，失败降级为仅导出文件
- 流程改进：解题盒内若判定为"源码自带密码学实现"，先花 2 分钟确认本机是否有对应解释器（php/ruby/node），没有则直接转搜索题解，不在移植上耗时间

## 其他

- 边界遵守：未执行压缩包内任何可执行文件；仅做离线流量解析与源码阅读
- GitHub API 配额：本轮开始 43/60，未触发失败重试
- 下轮建议：2025-WQB-Quals 的 forensics/checkwebshell 已记录；可转向 reverse/minigame（wxapkg）或 web/ez_python（需远程环境，跳过），或探测组织其他赛事仓库

> AI生成
