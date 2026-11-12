# CTF 20分钟轮次报告：Forensics-单选题（2026-NCO-Quals）

- 日期：2026-11-12 14:44
- 题目：CTF-Archives/2026-NCO-Quals / forensics / Forensics-单选题（5pts，纯题面无附件）
- **FLAG：C**（ZIP 压缩包）
- 耗时：约 14 分钟（选题 3 分、解题 4 分、学题解 3 分、落库与报告 4 分；时间盒 15 分钟+收尾 5 分钟内）
- 状态：SOLVED

## 解题过程

1. 进度：done.json 121 条，2024-CCB 18 附件已穷尽 → 转 NCO-Quals；forensics 目录序第一道未做即本题。
2. 题面：未知文件以十六进制 `50 4B 03 04` 开头，问文件类型（A PNG / B BMP / C ZIP / D GZIP），flag=正确选项字母。
3. 通道：引擎 `solve --mode auto` 2.7s/54 attempts NOT SOLVED（选项题无 quiz 检测器，预期内）→ 选项题语义判定为权威（硬规则），并用脚本核对魔数：PNG=`89 50 4E 47`、BMP=`42 4D`、GZIP=`1F 8B` 均不匹配；`50 4B 03 04`="PK\x03\x04"=ZIP 本地文件头 → **C**。

## 标签

- 题型：forensics；载体/格式：文本题面（hex 魔数四选一）
- 识别信号：hex 头若干字节+文件类型四选一
- 陷阱类型：无（直给标准魔数，干扰项均为常见格式）
- 可迁移模式：hex 头直接对照标准魔数表秒判文件类型；ZIP 三魔数族一起记
- 变体预测：换 RAR(`52 61 72 21`)/7z(`37 7A BC AF 27 1C`)/ELF(`7F 45 4C 46`)/PE(`4D 5A`) 干扰项或复合结构伪装

## 一得

**ZIP 家族三个魔数打包记（LFH / 中央目录 / EOCD），一次记全一族而不是单点**：50 4B 03 04（本地文件头，"PK"=Phil Katz 缩写，\x03\x04 为格式版本）、50 4B 01 02（中央目录项）、50 4B 05 06（EOCD 尾记录）——考题给其一任一都能秒判，且 EOCD 可用于截断 zip 修复定位。

## 题解学习要点

- 赛事仓库 README 仅分值清单、无解法链接；按题名+赛事 online 检索及核心技术词检索两轮均无公开 writeup（闭门赛），属教材级常识，通用资料在线对照佐证：50 4B 03 04 = ZIP 本地文件头（CSDN wenku + ZIP 伪加密文章双重佐证）。
- 增值关联技术（同源文章）：**ZIP 伪加密**——local file header 后通用标志位（偏移 6）与中央目录标志位（偏移 8）的加密 bit 被置 1，但正文从未加密；修复=两处标志位 `&= 0xFE` 清位。与本题魔数识别共用一张知识卡。
- 干扰项魔数速查（本题实证核对）：PNG `89 50 4E 47`、BMP `42 4D`（"BM"）、GZIP `1F 8B`。

## 失败原因

无实质失败。引擎 NOT SOLVED 属选项题类型盲区（无 quiz 检测器，54 attempts 各步重扫无意义却仅耗 2.7s），语义判定通道一次定案；learn-writeup 首次入库 techniques 为空（TECH_PATTERS 无魔数规则，writeup 文本未命中任何正则），补写真实学到的关联技术（伪加密清位修复）后重跑，成功落 1 检查点 + 1 技能卡 + technique（file carving / repair）。

## 落库与收尾

- learn-writeup：`Forensics-单选题-NCO2026` → 1 checkpoint + 1 skill card（skills/learned/Forensics-单选题-NCO2026-00.json）
- done.json：121 → 122 条
- 下轮候选：forensics 多选题（10pts 无附件）、pwn 简单夺旗-超级无敌扭蛋机（20pts 带附件，复用史莱姆实验室 pwn 模式卡）、web 校园表白墙 / EasyLogin（20pts）

> AI生成
