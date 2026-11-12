# Reverse-mips2（2023-fjgys）解题报告

- 轮次时间: 2026-11-12 09:15
- 仓库/题目: CTF-Archives/2023-fjgys / Reverse-mips2
- 附件: challenge（ELF32 大端 MIPS + C++）、mips/firmware（12KB squashfs，xz 压缩，strings 无题逻辑，判为干扰）
- Flag: `flag{d3d3cf2730a9829c4081cdea222f609a}`
- 解得输入串: ``qEO`^csCWjEVB<w6GjhO[Cu3^0n?v``（29 字符，含反引号）
- 耗时: 墙钟约 28 分钟（超 15 分钟盒；主因逐函数反汇编与 9×9 非规则宫建模，实际执行脚本很快）

## 标签

reverse / MIPS32 big-endian ELF / capstone 静态反汇编 / jigsaw sudoku（非规则宫数独）/ 字符编码两位九进制数字 / md5

## 一得

- MIPS 大端 ELF 可用纯标准库解析节表 + capstone（`.temp` 依赖）完成函数级还原；main 中的 `xori/sltiu/beqz` 组合即输入长度与字符域校验。
- 输入 29 字符按 `v=c-0x2a` 拆成 `v/9+1`、`v%9+1` 两位数字，按行优先填入 9×9 盘面 58 个空格；第二张 9×9 全局表是 9 个不规则宫的 block map，即 jigsaw sudoku。
- 该题唯一解；58 位数字按扫描序两两回编码为字符后取 md5 小写即 flag，与 README "flag 内 md5 小写" 一致。
- 固件 squashfs 与校验逻辑无关（chal 无文件 IO，imports 仅 iostream/malloc），不要被附件结构带偏。

## 题解学习要点

- 赛事仓库 README 仅提示"flag 内的 md5 值为小写"，无解法链接；在线检索（题名+赛事+writeup、mips2+数独）未找到公开题解，属闭门赛，按自证路径落库。
- 自证核验：回溯求得唯一解（solutions=1），逐行/列/不规则宫校验通过，md5 复核为 `d3d3cf2730a9829c4081cdea222f609a`。
- 可迁移：遇到"输入长度固定 + 每字符编码多位数字 + 全局盘面"的逆向题，优先怀疑数独/拉丁方；先还原填充顺序与字符域，再建模求解。

## 失败原因

- 已解出；唯一失败项是 CTFPilot 引擎 auto 21.2s NOT SOLVED（无 MIPS/ELF 检测器，triage 误判 misc），由模型补位完成。

> AI生成
