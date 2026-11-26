# webshell（2025 陇剑杯决赛 RHG 第1轮 · 应急响应 · 18 解）— NOT SOLVED

- **Flag**: 未解出（flag{part1_part2_part3}，part1=黑客工作目录、part2=黑客输出内容、part3=黑客找到的秘密）
- **耗时**: 约 22 分钟（超盒 7 分钟，含 3 轮外部检索；协议壳已完整逆向，会话密钥未恢复）
- **来源**: CTF-Archives/2025-LongjianCup-Finals · attachments/1.webshell.zip（421KB）

## 现场结论（已确证部分）

- 流量全景：2194 条 HTTP 流，192.168.20.130(攻击者) ↔ 175.27.229.115:2333(政务云门户)；其余为随机文件名目录扫描噪声。webshell 交互仅 **8 次 POST /indexa.php**（PHP 7.1.33）。
- 协议壳完整逆向（双向同构）：
  - 请求体 = `[16字符常量 "VS?/eSb38A:=~msC"]` + `md5("123456")[8:20]="49ba59abbe56"` + `b64串("Ha1"开头)` + `md5("123456")[20:32]="e057f20f883e"` + 13字符噪声 + `\nqG`
  - 响应体 = `[16字符常量 "vkzJl2VQbzhPhLHS"]` + 同两段 md5 片段夹 `b64串("Ha0"/"Ha1"/"Ha3"开头)`
  - b64 解码后 → **魔数 `\x1d\xad`** + 类型标志（b64 第4字符为数字0/1/3，占第3字节高2位：请求恒=1，响应=0/1/3）+ 载荷
  - 载荷长度任意（请求 20/50/50/147/58/59/72/68 字节，响应 13/14/20/12/12/67/17/28 字节）→ **排除 AES/ECB/CBC（非16倍数）**，为 XOR/RC4 类流密码
  - 同位异或消密钥证明明文共享前缀：req1≡req2 前 20 字节、req5/6/7 前 ~50 字节、res0≡res1 第4字节相同 → 确定性逐位流加密，密钥周期 >50
- 密钥素材候选：REQ0（20 字节）疑似握手包 = `1dad7b` + `0x2c` + 16 字节 `aaabe409d0018755b1d735397d17335f`；但以其为密钥的 RC4/XOR 试解均失败。

## 失败路径与原因

1. 快通道 solve --mode auto 误报 SOLVED——**README 占位 flag `flag{part1_part2_part3}` 被当真 flag**（引擎缺陷，见一得）。
2. 约束密钥恢复（假设明文为 b64/可打印/ASCII 字母表，L=1..32）全部无解 → **明文含 ≥0x80 字节（疑 UTF-8 中文或 gzip）**，可打印字母表假设错。
3. AES-ECB/CBC（md5("123456") 派生 6 种密钥）→ 乱码；RC4 同密钥组+REQ0 会话密钥+md5链（md5(md5("123456"))、sha1/sha256、pass拼接）×偏移0-3 → 全乱码。
4. 哥斯拉/冰蝎标准密钥（md5[:16] 两半、$i+1&15 起始偏移）×b64 串层与解码层 → 全乱码。工具身份未定：md5 分片夹心像哥斯拉家族，但分片是 12+12（md5[8:32] 对半）而非哥斯拉典型 16+16，且魔数 1dad、HaN 标志、请求尾部噪声均为该赛自制协议特征。

## 题解学习要点（本轮检索）

- 本题（第1轮 webshell）**无公开题解**；同赛 ShellDecoder（第5轮）WP（cnblogs Linyuuy）给出同赛事 webshell 全套范式可迁移：
  - Cookie 型 webshell 源码范式：`system(base64_decode($_COOKIE["cm"])." 2>&1")` + `ob_start/ob_get_contents` + `setcookie($cn, $cp.b64(输出).$cp)`——**回显藏在 Set-Cookie 响应头**，被 cm/cp 包裹；
  - 哥斯拉 JAVA_AES_BASE64 解密链：**去头尾 16 位 md5 片段 → b64 解码 → AES-ECB(key) → 可能为 gzip**；请求明文为 TLV（`methodName…getBasicsInfo`）；批量爆破工具 **github.com/webraybtl/webshell_detect**；
  - flag 拼接常见为 流量中分段取证（hex 段拼 zip、Base 多重解码链 Base64→Base92→Ascii85→Base62→Base45）。
- 下轮续攻缺口：① 用 webshell_detect 批量试哥斯拉全密钥空间；② 明文按 UTF-8/GBK 中文重评分；③ XOR 密钥=16 字符常量前缀（b64 串层未试）；④ REQ0 后 17 字节做 RC4-drop-N 变体。

## 七维标签

赛事=2025陇剑杯RHG | 题型=forensics/webshell流量 | 难度=中 | 信号=pcapng+POST+md5("123456")分片+16字符常量前缀+b64(HaN)+魔数1dad | 技术链=pcapng解析→HTTP流→协议壳剥离→魔数→流密码密钥恢复(未破) | 工具=自研stdlib pcap解析+Cryptodome | 结果=NOT SOLVED

## 一得

**webshell 自定义协议先剥壳再谈密钥**：常量前缀→握手/密钥指纹、md5 分片→密码本(md5("123456"))、b64 长度规律→编码层、载荷长度排除 AES→流密码、同位异或证共享前缀——五步把未知协议缩成"求一个流密钥"问题。次得：**快通道把 README 占位 flag 当真解**（flag{part1_part2_part3} 无真实值）——固化候选：占位符模板 flag（part\d+/xxx/<>/…占位体）不得判 SOLVED。

> AI生成
