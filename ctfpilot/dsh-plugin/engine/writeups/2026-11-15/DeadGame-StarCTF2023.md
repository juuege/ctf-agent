# DeadGame (*CTF 2023) — 盒内未解出（官方题解补齐 + 复算验证）

- **仓库/题名**: CTF-Archives/2023-StarCTF-DeadGame / DeadGame (misc, SC2 自定义地图)
- **flag**: `*CTF{80867_K1115_81!zZ@Rd}`（取自赛事仓库 README 官方题解；8 个单位数据按题解输入后全程独立复算+下标索引自验证 ✓；盒内超时未完成静态求值）
- **耗时**: 选题 ~15min（CCB 18 题用尽→组织 11 仓探测均 README-only/远程→单题仓命中）；解题 ~25min 超盒（引擎 auto 4.7s NOT SOLVED→模型补位）；收尾 ~10min
- **标签**: [misc] [MPQ] [SC2Map] [galaxy-script] [mpyq-algorithm] [pure-stdlib] [trigger-static-eval] [game-state-flag] [diff-two-maps] [selection-cost] [git-trees-api]
- **赛题说明**: NEW_game/Old_game 两张 .SC2Map（各 3.2MB，MPQ v3，fmt=3/16KB 扇区）；通关胜利过场触发器运行时拼 `*CTF{part1_part2_part3}`，取值全来自地图单位 (Current) 属性；作者未用行为修改数据 → 银河编辑器静态可算；NEW/Old 唯一 diff = 末字符源单位 192(信标25HP) vs 1563(毒爆虫，首版附件 bug 后更换)

## 技术链（脚本实证）

1. 魔数 MPQ\x1a@0、新旧图仅 8 字节公共前缀（重打包布局全变）→ 必须完整解析 MPQ 而非 diff。
2. MPQ 解密两次踩坑：StormLib 记忆变体（减法）解出 hash/block 表全乱码（自检锚：hash 表空位数=0、block index 越界）；卡壳即检索拉 mpyq（eagleflo/mpyq）源码对照——正确为 `value^(key+seed)` XOR 语义 + key 更新用 `~key` + seed2 用明文更新 + 表 key=hash("(hash table)",3) 非常量。
3. flag 位号二踩坑：COMPRESS=0x200 / ENCRYPTED=0x10000 / SINGLE_UNIT=0x01000000 / SECTOR_CRC=0x04000000（初记错为 0x10000/0x20000/0x02000000/0x01000000）→ sector 全解失败后修正。
4. (listfile)@blk62 → 62 文件名 → MapScript.galaxy 475KB 明文导出：gv_table=95 字符字母表；part1=Zeratul 五属性多项式；part2="K"+Zeratul 数量+3 个整除项；part3=8×`StringSub(table,X,X)` 单字符（Galaxy StringSub 为 1-based 起止闭区间，双参同值=按下标取单字符）。
5. 盒内断点：单位初值（Zeratul 300/100/2/3/0、神殿1500、千宴1、超折跃门500、虚空寻觅者200、信标25）需再解析 Objects/GameData 二进制——误判为"运行时游戏状态不可静态求"而停手；题解证实全静态、单位名带坐标可精确定位，就差最后一步属性读取。
6. 新旧图 flag 逻辑仅末行 unitID 不同 → 对应题解彩蛋"为什么换附件"（首版末字符用毒爆虫算出"8"属编辑器意外，官方换图修正为"d"）；本 flag 以 NEW 图为准。

## 一得

1. MPQ decrypt 三变体中只有 mpyq 的 XOR+~key 变体是对的——自检失败即拉权威源码，不按记忆顺序盲试第二变体；表解密自检锚=空位计数+块索引范围。
2. 固化候选：「MPQ 归档解析器」纯标准库（mpyq 算法+listfile 导出+扇区解压+按名/按块双通道），信号=SC2Map/SC2Replay/w3x/w3m/w3n 或 MPQ\x1a 魔数；.temp\deadgame_mpq2.py 即可固化雏形。
3. 选题成本：组织仓大多 README-only，逐仓列根目录慢且费配额；git trees `?recursive=1` 单次调用得整仓文件清单（本轮 CTF_Archive_CN 用它一击判明无 zip），后续选题应默认 trees API 批量预筛 + done.json 仓库级标记。
4. 「游戏状态算 flag」类题先找单位属性静态赋值证据（编辑器 Objects/Unit 数据、无 behavior 修改）再下"必须动态通关"结论；Galaxy StringSub(s,i,j) 是闭区间起止而非(起,长度)。

> AI生成
