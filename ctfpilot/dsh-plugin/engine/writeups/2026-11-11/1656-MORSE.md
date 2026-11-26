# 2025-jksn-quals 小学组第二场 Crypto / MORSE

- 时间：2026-11-11 16:46–16:57（约 11 分钟，15 分钟盒内）
- 来源：CTF-Archives/2025-jksn-quals · 小学组-第二场/Crypto/MORSE
- 附件：shenmi.wav（788,550 B；8.94 s；44100 Hz 单声道 16 bit）
- Flag：`FLAG:MORSECODE`（音频原文；题面要求字母全大写，按原文提交。若平台需 flag{} 包装则为 `flag{MORSECODE}`；平台未回放验证）
- 标签：音频摩斯 / 方波包络 / RMS 阈值 / run-length / 单位时长 / 冒号 `---...`

## 选题与仓库探测

1. `2024-CCB-CISCN-Quals` 的 attachments 共 18 个 zip，全部已在 done.json，无未做题。
2. 组织内继续探测：`2025-WQB-Quals/forensics` 的 SilentMiner、bademail 仅有 README，附件指向已过期网盘/华为云链接，不可下载；`关注公众号获取-flag` 需关注公众号，无法获取。
3. GitHub API 配额耗尽（0/60），按预案降级到 jsDelivr CDN，改选 `2025-jksn-quals`（README 有完整题面与 ASCII 附件路径）；取小学组第二场 Crypto/MORSE（`attachments726/shenmi.wav`，附件 <25 MB）。

## 解题过程

1. 文件识别：RIFF/WAVE，8.94 s；RMS 包络为振幅恒定 23170 的理想方波，无噪声。
2. 5 ms 窗 RMS → 中值阈值切通断 → 83 段 run-length（41 个音）。
3. 最短 tone = 60 ms 定为 1 单位；180 ms 为划；gap 60/180 ms 分别为字母内/字母间间隔。
4. 摩斯解码：`..-. .-.. .- --. ---... -- --- .-. ... . -.-. --- -.. .` → `FLAG:MORSECODE`。
5. 用 2/3/8 ms 三种窗口独立复解码，均为 `FLAG:MORSECODE`（单位 55.9–59.9 ms），排除切分歧义。

## 通道与失败原因

- CTFPilot fast 通道：`NOT SOLVED in 118.8s (7955 attempts)`。引擎只有字节级编码/压缩检测器，无音频/摩斯检测器，属预期边界，不是题目逻辑失败。
- `--mode auto` 深通道在 120 s 工具上限被截断，无结论；未继续等待。
- 模型补位通道：约 30 行标准库脚本（wave + RMS + run-length）在 4 分钟内解出，未执行任何可执行文件（本题无）。

## 题解学习要点（≤5 分钟）

- 本题无公开专属 writeup：两轮在线检索与 Hikaru Lab 站点检索均未命中小学组第二场 MORSE。
- 同赛事 Hikaru Lab《第五届极客少年第一轮初中组初赛题解》可作格式参照：该平台答案形态不统一（签到题为裸答案 `20250720`，另一题为 `flag:{QrCodeSplit}`），必须按题面/音频给出的前缀提交，不能硬套 `flag{}`。
- 摩斯标准表复核：`---...` 为冒号，不是 O(`---`)+S(`...`) 连写；末尾 `-.-. --- -.. .` = CODE，与 `FLAG:` 前缀语义自洽（回读校验可自证）。

## 一得

- 音频摩斯判据：先量最短 tone 定单位（2 倍判划，3/7 倍判字母/词间隔），理想方波用 RMS 包络即可，不必上 FFT/Goertzel。
- 冒号 `---...` 是最易误拆点；解码后必须做语义回读（`FLAG:xxx` 结构）确认切分。

## 固化候选（按 RSI，首次出现只落库）

- 音频摩斯检测器：特征 = RIFF/WAVE + 包络双峰 + 时长 < 60 s；判定 = 5 ms RMS run-length，最短 tone 为单位，2 倍判划、3/7 倍判间隔，输出大写明文。纯标准库可实现，待二次出现再评估固化。

## 产物

- 题目目录：`ctf-20min\2025-jksn-quals__MORSE\`（shenmi.wav、writeup.md）
- writeup 已 `learn-writeup` 入库：category=forensics，技能卡 `jksn2025-小学组MORSE-00.json`，recall 可检索
- done.json 已追加本题

> AI生成
