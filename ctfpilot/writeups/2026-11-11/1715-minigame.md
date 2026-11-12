# CTF 20分钟循环 2026-11-11 17:00 — 2025-WQB-Quals reverse/minigame

## 结果

- **flag：`flag{fae0b27c451c728867a567e8c1bb4e53}`**（SOLVED，模型探索通道）
- 选题：CTF-Archives/2025-WQB-Quals reverse/minigame（附件 60KB zip，解出无后缀文件 wxe4679cbcec91e410）
- 总耗时：约 14 分钟（15 分钟解题盒内：选题/下载 2 分、解包与源码还原 5 分、WASM 逆向与解 flag 5 分、验证 2 分；收尾含在 20 分钟内）

## 解题路径

1. 选题：CCB-CISCN 18/18 已尽；api.github.com 限流重试 1 次仍 403 → 按既定降级通道走 jsDelivr 数据接口列目录，WQB 剩余未做题中 pwn×3 离线不可交互验证，选 reverse/minigame（60KB，静态分析友好）。
2. 魔数侦察：`BE 00 00 00 00 ... 03 ED` = 微信小程序包 wxapkg（头 14B：firstMark/idx_len/body_len/lastMark），文件名 `wxe4679cbcec91e410` 即 wx+appid。
3. 自写纯标准库解析器（30 行）解包出 22 个文件：utils/validator.wasm（293B）、app-service.js、sourcemap×4 等。
4. 用 app-service.js.map 的 sourcesContent 直接还原 utils/validator.js 与 pages/index/index.js 原始源码：输入须 strlen==38，`input[i] ^ data[1024+i] == 0x99`（JS 层 TextEncoder + wasm _validateString）。
5. 自写纯标准库 WASM 反汇编脚本（opcode 表 + LEB128）还原 func0 校验循环与 data 段 38 字节 → `data ^ 0x99` 解出 flag；重放 wasm 逻辑正例通过、负样本（翻转 1 字节）被拒。
6. 引擎 solve --mode auto NOT SOLVED（96.8s，wasm 超快通道边界，预期内），模型探索通道解出。

## 题解学习要点（X1cT34m 官方 Writeup，ctf.njupt.edu.cn/archives/1154）

- 官方路径：后缀改 .wxapkg → `npm i wedecode -g` 解包 → IDA 反汇编 wasm → 逐字节异或 0x99，结论与本解完全一致。
- 更优点：本解零第三方依赖（wedecode 为 npm 工具，本机环境无 node），纯 Python 30 行解包 + sourcemap sourcesContent 免读混淆插桩 JS，路径更短。
- 漏掉的技术点：无；官方亦无额外技巧。

## 一得

- 小程序逆向不必依赖 wedecolve/wedecode：wxapkg 是简单 TLV 格式，纯 Python 30 行可解包；带 sourcemap 的包直接 sourcesContent 拿原始 JS，绕过全部混淆插桩。
- 坑：`os.path.join` 拼以 `/` 开头的包内文件名会落到盘根（Windows drive-relative 路径）→ 本轮误写 20 文件到 C:\ 根，已全部移入回收站；解包脚本必须先 `lstrip('/')`。
- RSI 候选：**wxapkg 解包器 + WASM 逐字节异或校验反解**已具备确定性特征（魔数 BE/ED + data 段定长比较），可评估固化为 fastpath 检测器（wxapkg unpack → 提取 wasm data 段 + xor 常量）。

## 标签

wxapkg、wasm-reverse、sourcemap-restore、xor-0x99、length-check-38、mini-program、WQB2025、reverse、jsdelivr-fallback

## 失败原因

无（本轮解出）。唯一波折：api.github.com 限流，按边界重试 1 次失败后改用记忆中既定 jsDelivr 降级通道，未跳过本轮。

## 产物

- 题目目录：`ctf-20min\2025-WQB-Quals__minigame\`（附件 zip + 解包 ext\）
- 工具（.temp，复用）：`CTFPilot-v0.1\.temp\wxapkg_extract.py`、`wasm_dis.py`
- writeup 入库：minigame-WQB2025（reverse，1 检查点）
- done.json：更新至 67 题
- 下轮候选：2025-WQB-Quals 剩余 reverse/strangeApp（4.5MB，Android/frida）、pwn×3（离线难验 flag）；或 2026-qwbs10-quals Web/cwp；或 2025-WQB-Quals forensics/bademail、SilentMiner（大附件走 OBS 链接）

> AI生成
