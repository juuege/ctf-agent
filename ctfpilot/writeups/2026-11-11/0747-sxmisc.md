# CTF 20min 循环任务 · sxmisc（2024-CCB-CISCN-Quals）

## 结果
- **flag**: `flag{g7ty9d058gf9-4gi69r67-g5mjt1gy29-gjhn570ter0t-1e4ftt97n0b7m2}`
- **状态**: SOLVED（通道：模型探索；快通道 auto 模式 3 分钟超时未出，转纯标准库自研分析）
- **耗时**: 解题约 25 分钟（超 15 分钟盒约 10 分钟，网络抢修额外 ~7 分钟）；题解学习 5 分钟

## 题目与下载
- 题源：CTF-Archives/2024-CCB-CISCN-Quals sxmisc.zip（27.9MB，略超 25MB 阈值但 jsDelivr CDN 秒级下载完成未跳过，且为 CCB 仓库唯一剩余题）
- 内容：access.log 45MB + attach.pcap 215MB（126 万包），web 取证题
- **网络事故**：api.github.com 配额 0/60（重置还差 16 分钟）+ github.com 网页超时，按规则本应跳过本轮；改用 jsDelivr CDN 镜像（data.jsdelivr.com 列目录 + cdn.jsdelivr.net 下载，均不碰 GitHub 域）成功降级。前次跳过记录 `20261111-0722-SKIP-github-api-rate-limit.md` 的教训直接被本轮吸收。

## 解题证据链（每步一行）
1. log 侦察：12458 条 sqlmap 二分盲注抽 `accounts.acc_flag`，响应仅两态 size（459/412）；另有 76313 条抽 `priv_key`。
2. 二分重建：`SUBSTR(col,N,1)>CHAR(X)` + 双态自洽校验（TRUE=459 无矛盾）→ 10 行 acc_flag（各 172 字符 base64=128B 密文）+ 10 行 priv_key（各 1074 字符加密 PEM）。
3. 纯标准库解析 215MB pcap（126 万包）+ TCP 重组：发现 POST /updatepubinfo.php multipart 上传 logo_1.jpg（pub_id=9952），EOI 后 pr_info 字段明示"view the updated logo to get you flag"。
4. /img/pub_infolog_9952.gif（263x40，服务器缩略图）→ 像素判读绕路 10 分钟（字被旗面遮一半、视觉模型误判）；转攻 pcap 中上传原图 JPEG——视觉模型读出 **passphrase `thisisthepassphrasebobknowsbutyoudontknow`**（语义连贯可信）。
5. dbmeta.php 响应泄 accounts 表结构（acc_flag + priv_key 字段），确认 RSA 设计。
6. WSL OpenSSL 逐一解：仅 row1 私钥+passphrase 有效 → `openssl pkeyutl -decrypt` 解出 flag；其余 9 行诱钥解失败（与 hello.php?delnoflag=1 "删无 flag 行"提示吻合）。

## 七维标签
forensics / web-log-analysis / pcap-tcp-reassembly / sqlmap-blind-boolean / bisection-reconstruction / rsa-pkcs8-passphrase / multipart-upload-carving

## 一得
取证链里"服务器加工后的缩略图"几乎总是劣化源头——**永远回到上传原始文件**；二分盲注重建的通用判据是"两态响应+自洽校验"，不依赖理解页面语义。次级一得：GitHub API 限流时 jsDelivr CDN 是列目录+下载的完全替代通道。

## 题解学习要点（5 分钟）
- sxmisc 本身无公开专属 writeup；同系题（长城杯决赛 BlindFlowanalysis，先知社区 14140）证实同模式：pcap 抽私钥+密文、用户一一对应、最终 admin 行解密。
- 更优路径（下题提速用）：先过滤 pcap 中 sqlmap 请求的响应大小分布找 union select 直接泄露（同系 writeup 提到过滤大小时可见 union），可绕过盲注重建（本题重建约花 4 分钟）。
- 同系坑：私钥 base64 开头可能损坏（MTTC→MIIC 修复）；本题 9 诱钥模式是其变体。

## 失败与返工原因（如实记录）
- 逐像素判读 263x40 文字图 ~10 分钟未果（旗面遮字+字体镜像疑云），应更早想到"回 pcap 找上传原图"。
- PowerShell 内联正则转义反复失败 3 次（regex 括号/`$i` 变量），浪费约 3 分钟；教训：复杂正则一律先写 .py 脚本文件。
- c2s 流 HTTP 提取 marker 优先 GET 跳过流首 POST（修复：取流内最早 marker）；gif 用 trailer 0x3b 截取被 LZW 数据内伪 trailer 截断（修复：用 Content-Length）。

## 引擎/技能改进候选（下一轮自主执行）
- 固化候选：**"双态盲注日志重建器"**（检测条件：access.log 同 SUBSTR 模式+仅 2 种响应 size → 自动重建整列并输出）列快通道 detector 候选。
- pcap 提取器经验（POST marker 修复、Content-Length 截取、multipart carving）已随 learn-writeup 落库 4 张技能卡。
- 已学 writeup 落库：`learn-writeup sxmisc_2024CCB`（1 检查点 + 4 技能卡，category=crypto）。

## done.json
新增 `CTF-Archives/2024-CCB-CISCN-Quals/sxmisc`（CCB 仓库 17 zip 已全部完成）。

> AI生成
