# CTF 循环报告 — 2023-zjsdxs Misc-NewGrating

- **时间**: 2026-11-12 06:09（选题 05:35 起，总计约 35 分钟，超出 20 分钟盒——超时原因见"失败原因"）
- **题目**: CTF-Archives/2023-zjsdxs/Misc-NewGrating（"到底怎么样才能拿到密码啊"，附件 蝎子.pcapng 3.7MB）
- **结果**: ✅ SOLVED — `DASCTF{d308299b-2eaf-453a-8788-b4f7979c90f5}`
- **通道**: 引擎 auto/fast NOT SOLVED（3.7s，无 pcap/HTTP 检测器）→ 模型补位通道解出
- **耗时**: 解题约 30 分钟（超 15 分钟盒）+ 收尾 5 分钟

## 解题链

1. CCB 仓库已穷尽（18/18 done），GitHub contents API 列 2023-zjsdxs → 第一道未做题 Misc-NewGrating（zip 2.44MB，api.github.com raw 下载）
2. 纯标准库解析 pcapng（4667 包）→ 会话概览：HTTP POST /upload/shell.php 多流（冰蝎）+ upload_file.php 明文上传 + UDP 54915 广播 991 包（magic `Byxs20`，每包 263B）
3. 明文上传包里直接提取 shell.php 源码：冰蝎 v2 变体，注释给出 key=`e46023a69f8db309`（md5("DASCTF")[:16]，"密码32位md5值的前16位"）
4. c2s：b64decode → AES-128-**CBC 零IV** 解密 → `assert|eval(base64_decode('...'))` 全通（信息收集/phpinfo/文件管理×6/大文件上传）——响应里无 flag
5. s2c：dechunk → gzip → b64 → AES-CBC 同 key → `{"status":...,"msg":<b64>}` → msg 递归 b64（"ok"/目录列表/命令输出）——无 flag
6. **flag 在 UDP 54915 广播**：内容实为内存片段 dump（堆指针、时间戳），字符串常量 `{d308299b-2eaf-453a-8788-b4f7979c90f5}`，991 包一致；同仓 R_r 已证本赛事 flag 格式 = DASCTF{uuid} ⇒ `DASCTF{d308299b-2eaf-453a-8788-b4f7979c90f5}`

## 题解学习要点

- 两轮在线检索（题名+赛事+writeup / 赛事全称+题名）无公开专项题解，该赛事 writeup 覆盖极少（与上轮 R_r 同况）——自证路径即最优
- 对照公开冰蝎 v2 分析知识核对：默认 key=md5("rebeyond")[:16]、请求=裸 base64 体、`openssl_decrypt($post,"AES128",$key)` 无 IV 即 CBC 零 IV——与本题实测完全一致，无漏点
- learn-writeup 已入库（1 检查点 + 3 技能卡：NewGrating-zjsdxs2023-00/01/02.json）

## 一得

1. **ECB→CBC 判据**：ECB 解密后仅首 16 字节可读、后续乱码 ⇒ 实际是 CBC + 零 IV（PHP openssl 省略 IV 参数的默认行为）
2. **同 seq 冲突重组**：keepalive 6 字节零包与真实数据段同 seq，必须"同 seq 保留最长 payload"再拼流，否则长段被静默丢弃
3. **zlib.decompressobj(31) 优于 gzip.decompress**：容忍 chunked 尾巴等尾部垃圾
4. **密钥先找明文**：取证场景 webshell 源码往往随流量上传，注释直接给 key，不必先解密再猜
5. 固化候选：冰蝎 v2 流量全链路解密器（裸 base64 POST 体 + AES128 shell 特征）+ pcapng→TCP→HTTP 纯标准库骨架

## 失败原因 / 超时剖析

- 超时根因：无 tshark 环境下临场手写 pcap 解析器，4 个坑各耗 3-5 分钟（BOM 比对值写错致 0 包、ECB/CBC 模式误判、同 seq 零包覆盖、gzip 尾部垃圾）；若固化第 5 条候选，同类题可回到分钟级
- 记录：本轮检索配额内完成学习，done.json 98→99 条

> AI生成
