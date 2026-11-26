# PixMatrix-chb2024（楚慧杯 misc，xz 伪装 zip + 图像矩阵变换）

- **题目**：CTF-Archives/2024-chb / PixMatrix（misc，附件 PixMatrix.zip 9.7KB）
- **flag/答案**：盒内未解出（无公开题解可补，闭门题自证收尾）
- **耗时**：盒内约 20 分钟（选题约 5 分钟，解题约 12 分钟，检索学习约 3 分钟）；引擎 auto 2.2s NOT SOLVED（16 attempts，无 xz 伪装检测器）
- **标签**：misc-隐写 / zip自定义method=95 / XZ魔数伪装(fd377a585a) / 图像矩阵转置 / 块粒度枚举 / 视觉判读陷阱

## 链

外层正常 zip → 内层 PixMatrix.zip 的 LFH/CD 全部 method=95（非标准）→ payload 魔数 `fd377a585a` = XZ 流，lzma 秒解出 JPEG（512x72 RGB）+ README（`(flag.jpg)^T => (PixMatrix.jpg)`，8x8 示意图演示 2x2 块矩阵转置、块2↔块3 互换）→ 恢复尝试链：整图十字 2 块交换 / k=4、k=8 块矩阵位置转置 / 纯像素转置（72x512）/ 逻辑位图（8x8 块均值 64x9 与 2x2 min-pool 256x36 ASCII 化）→ 全部判读为碎片噪点，无收敛。

## 对照题解

两轮在线检索（楚慧杯 PixMatrix writeup / 精确引号查询）+ CSDN《2024楚慧杯WP》合集全文抓取（misc 仅收录不良劫、gza_Cracker 两题）+ 赛事仓 README（PixMatrix 说明为"无"）三源确认：PixMatrix 无公开题解。README 示意图兼容"纯矩阵转置"与"块位置转置"两种粒度解释，8x8 示意是否意味着 8 像素/64 像素块未定；512x72 比例 64:9 下"尺寸不变的转置"仅当块宽高比 9:64，与视觉模型观察的"64 竖条"结构存在矛盾，真变换可能是分块粒度的转置或转置+其他扰动。

## 坑

①`Expand-Archive` 报 unsupported compression method 时应第一时间 hexdump zip 而非怀疑工具——method 字段偏移 8-9、CD/LFH 双处可核对；②XZ 流藏在自定义 method 的 zip 里，Python `zipfile`/`lzma` 组合可纯标准库恢复（`lzma.decompress` FORMAT_XZ 自动）；③视觉模型判读 512x72 小图放大 2x 仍不可靠（三次"未恢复"均基于模型描述），位图题应尽早 ASCII 化用文本判读；④PIL 像素坐标是 px[列,行]，块转置脚本首版两次写反坐标序。

## 迁移

自定义 zip method（非 0/8/99/95 之外任意值）+ payload 魔数对照 = 快速识别伪装压缩：payload 首字节即可分流 XZ(fd377a585a)/zlib(789c)/bz2(BZh)/LZMA(5d00)/LZ4(04224d18)。恢复图像矩阵类题先确定"逻辑像素尺寸"（视觉模型估块结构）再谈变换粒度；示意图若兼容多种粒度解释，应列出所有满足"输出尺寸约束"的候选集再逐一验证（本题 512x72 的块比例 9:64 约束是关键推论）。

## 一得

**伪装 zip 的"method+魔数"双指分流法**：zip 结构解析（struct 手解 LFH/CD）30 行内定位 payload，payload 首字节对照压缩魔数表即知真身，比工具报错盲试快一个量级。固化候选：`zip 自定义 method → payload 魔数识别 → XZ/zlib 解压` 快通道检测器（信号：Expand-Archive 报 unsupported method + zip entry method∉{0,8,12,14,93,95,99}）。图像矩阵变换题的粒度枚举应先算尺寸不变性约束（块宽高比），把无限枚举缩成有限候选集。

## 失败原因

未解出的核心卡点：①"转置"粒度不确定——README 8x8 示意图仅 2x2 块矩阵，真实图 512x72 与示意比例不符，枚举的十字/块矩阵/像素级转置均未恢复；②视觉判读不可靠，三候选的"未恢复"结论可能误杀正确候选（正确恢复的文字图也可能因块内像素转置不完整而残破）；③时间盒耗尽前未做"尺寸不变性"约束推导（块比例 9:64 候选集），也未尝试以逻辑位图矩阵为单位的 8x8 块整体转置输出 72x512（与已试的纯像素转置差异在块内像素是否跟随）。

> AI生成
