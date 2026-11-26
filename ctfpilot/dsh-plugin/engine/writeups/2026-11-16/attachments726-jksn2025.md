# attachments726-jksn2025（2025-jksn-quals 多附件混合包，JS \u 转义藏 flag）

- **题目**：CTF-Archives/2025-jksn-quals / attachments726（misc 混合附件包：8 文件 = 妈妈的重要文档.xlsx(OLE2) / download.php / JinMianZhao.jpg / main.cpp / netframework.exe(.NET JScript) / shenmi.wav / unicode.html / usql.pcapng）
- **flag/答案**：`flag:{ErLaiSiWanBaQianSui}`（unicode.html 的 `var secret='\u0066\u006c...'` 解码；语义"尔来四万八千岁"——李白《蜀道难》，语义自洽）
- **耗时**：引擎 auto 346.6s NOT SOLVED（16516 attempts，triage=forensics conf 0.50）→ 模型补位约 6 分钟（两轮体检脚本+直读）；总对抗时钟约 400s
- **题型**: misc；**载体/格式**: 多附件混合包 → 命中载体为 431B 嵌 JS 的 HTML
- **题面特征**: attachments 包内含 ≤1KB 纯文本附件；unicode.html 431B 中 `var secret = '(\u00XX)+'` 单引号 JS 字符串字面量
- **识别信号**: 纯文本文件内出现成串 `\u00XX` 十六进制转义（≥8 个连排、首组 f l a g 2c 或可见 flag 头）
- **核心技术链**: 多附件按体积升序直读小文本 → 正则抓 `secret = '...'` → `decode('unicode_escape')` 一行解码 → `\u%04x` 逐字重编码==原文回验（通过）→ 语义校验（《蜀道难》成句）
- **有效技术**: `re.search(r"secret = '([^']*)'") + decode('unicode_escape')`；回验用 `''.join('\\u%04x'%ord(c))==原串`，双保险零猜测
- **失败路径**: 引擎深通道 12 步 read+strings 泛扫被 5MB pcapng 与 8 文件分流拖满未命中（\u 串短且在源代码字符串里，无检测器）；首版体检脚本对 OLE2 头 .xlsx（d0cf11e0 非 zip）抛 BadZipFile 中断 → 多附件体检必须逐项 try/except 隔离
- **适用限制**: 仅验证于 JS 字符串字面量 `\uXXXX` 且全码点 ≤0xFF 的情形；未测：\xNN、八进制 \NNN、`String.fromCharCode` 代码数组等变体
- **坑**: ①8 附件实为多道独立小题混包（shenmi.wav 为旧题 MORSE 复用文件、pcapng 为 pikachu 平台流量题、download.php 为 web 上传题源码），不逐个死磕、以首个可提交形态完整 flag 为停表点；② .xlsx 后缀但为 OLE2 老复合文档，zipfile 直接炸；③ flag 形态 `flag:{...}` 带冒号，超出引擎默认 `flag\{` 正则（即便解出示例也会漏判）—— flag 正则需含 `flag[{:]`
- **一得**: 转义串解码一律做**重编码回验**（编码唯一可逆，回验不等即停），比语义猜测硬；多附件题"体积升序直读"比逐深挖先到 flag。
- **可迁移模式**: "多附件包按 ≤1KB 文本优先直读；文件含成串 \u00XX → unicode_escape 一行解码+重编码回验"（一句话规则）
- **可固化性**: 是——「JS unicode 转义 secret 检测器」：特征=文件含 `('[ -~]{0,40}(\\u[0-9a-f]{4}){6,}[ -~]{0,40}')` 引号内转义串；判定=unicode_escape 解码后匹配 `(?i)flag[{:]|ctf\{` 等 flag 正则→直接产出；fastpath 级成本（亚毫秒、零依赖）
- **变体预测**: 换 \xNN/八进制/HTML &#NN;（旧案例 Crypto1 已遇）/ `chr()` 数组拼接；深色变形藏在 JS 反序列化、css content、svg text

> AI生成
