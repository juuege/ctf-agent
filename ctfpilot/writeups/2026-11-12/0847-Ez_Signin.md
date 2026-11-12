# CTF 20min 循环报告：Misc-Ez_Signin（2023-zjsdxs 决赛）

- **日期**: 2026-11-12 08:47 | **结果**: ✅ SOLVED | **耗时**: ~16 分钟（≤15 分钟盒解题 + 收尾；第 5 位字形争议延长时间盒，程序化定谳）
- **Flag**: `flag{8f16a9717824aa456eb9a98653eb3993}`（32 位 = 标准 md5；末位 `...b3993` 由用户确认，第 5 位 `a` 由近闭合环检测定谳）
- **双解交叉验证**: 本题今晨 05:50 已被做过（报告 20261112-0550-Ez_Signin.md，因 done.json 丢失更新致本轮重复选题），独立双解 flag 完全一致

## 选题

- CCB 18 题穷尽 → 转入 CTF-Archives 组织仓库探测 → 2023-zjsdxs 剩余 5 题中取第一道未做：**Misc-Ez_Signin**（zip 43KB，合规）
- done.json 96→97 条；文件名中文须 `urllib.parse.quote` 走 contents API

## 解题链（模型补位通道）

1. 引擎 auto 4.7s NOT SOLVED（16 attempts，无 AES-zip/鼠标宏检测器）→ 按工作流转模型补位
2. **ZIP 结构剖析**：comp=99 → WinZip AES 真加密（跳过伪加密试错）；EOCD 后 32 字节 hex 附加
3. **密码线索解码**：hex → b64 → `_275626d657e6f556679666` → 核心段逐字符反转再 hex 解码 = `five_number` → 提示 5 位数字密码
4. **爆破**：pyzipper（装 .temp）+ multiprocessing 8 进程落盘脚本，00000-99999 命中 **11452**（61.9s）
5. **内容解码**：flag.txt = Base32（253KB）→ 按键精灵鼠标宏脚本（9105 个 MoveTo + CapsLock/Ctrl/Z 键事件）
6. **读字**：纯标准库 Bresenham 渲染轨迹为 PNG（白底/黑底双图 + 3 倍放粗版）→ 视觉模型两读数 1 位分歧 → 按看图规则转用户确认 → flag 定稿

## 题解学习要点（先知社区 t/13043，AabyssZG，Misc-2）

- 官方路径与密码完全一致：真加密→尾部 base64（完整为 `What_iS_tHis_` + hex，本解从尾部 32 字节切入丢了语义前缀但反转 hex 同样得 five_number）→ ARCHPR 爆破 5 位数字→11452
- 官方装按键精灵跑脚本，遇"Ctrl+Z 撤销擦掉下半 flag"坑，需删 9633-9690 行撤销代码重跑
- **本解更优点**：解析 MoveTo 序列直接渲染 PNG——免装按键精灵、不受断网限制、忽略键盘事件天然免疫 Ctrl+Z 擦除
- **官方文末彩蛋**：同 Misc-3 NewGrating 为冰蝎流量题（key=e46023a69f8db309，密码 DASCTF）

## 一得（每题必有）

- `comp=99` = WinZip AES 判真加密特征（快于"改字节试错报错"法）
- 鼠标宏轨迹题不必真跑宏引擎：解析 MoveTo 序列纯标准库渲染即可得图，且忽略键盘事件天然免疫 Ctrl+Z 擦除
- **轨迹字形判读优先程序化拓扑度量（近闭合环检测 dist/diag），视觉模型只做初筛**——本轮视觉模型同时误读两位（第 5 位 e→a、第 32 位 b→3），拓扑度量一次判准
- **固化候选 1**: 鼠标宏轨迹渲染器（MoveTo/LeftDown-LeftUp 笔画解析 + Bresenham PNG 渲染；触发信号：按键精灵/MoveTo 脚本/鼠标宏）
- **固化候选 2**: 笔画近闭合环检测字形判读器（a/e/o/c/d/q/g 环形字判别）

## 坑记录

- multiprocessing spawn 无法 pickle `-c` 内联函数 → 爆破脚本须落盘文件执行（浪费 1 次超时 10 分钟教训，实际收敛为脚本文件 62s 命中）
- GitHub contents API 中文文件名直接拼 URL 报 UnicodeEncodeError → `urllib.parse.quote` 修复
- 视觉模型读手绘字不可靠（本轮误读 2 位），配合「环检测+字高统计」拓扑度量复核；技能看图规则的"关键判读交用户/程序化度量"得到双验
- done.json 并发丢失更新导致重复选题（05:50/08:47 两轮同做 Ez_Signin）：编辑器 oldString 不匹配是发现机制，建议每轮写前重读

## 产物

- 题目目录：`ctf-20min\2023-zjsdxs__Misc-Ez_Signin\`（独立于 05:50 轮目录 CTF-Archives_2023-zjsdxs__Misc-Ez_Signin）
- 渲染图：`.temp\track_big_black.png` / `track_big_white.png`（3145×1261）、`char_compare.png`（字形对比）
- writeup 已 learn-writeup 入库（含修正版 flag）

## 下轮候选

- zjsdxs 剩 Web-ezWEB（跳过，服务端题）已穷尽 → 转入 fjgys 剩余（Reverse-mips2 等）

> AI生成
