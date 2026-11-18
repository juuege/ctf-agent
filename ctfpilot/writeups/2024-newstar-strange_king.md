# 2024-newstar__Strange_King（NewStar 2024 Week1 Crypto）— rsi-110 引擎新解

- **仓库/题名**: CTF-Archives/2024-newstar / Strange_King（challenge.txt 250B，单文本附件）
- **题型**: crypto（misc 路由面）
- **flag**: `flag{PleaseDoNotStopLearing}`
- **状态**: SOLVED via fastpath progressive-caesar（rsi-110 / evolve-20261117-40，entry 61）；此前引擎 NOT SOLVED 0.94s / 17 attempts（25 固定移位 caesar 无法到达等差递增移位）
- **镜像提示**: 同载荷在 `2024-newstar-category/crypto.md` 与 `2024-newstar-week/w1.md` 重发布（跨源判重按内容 md5 同族）；引擎在这两仓命中早于 progressive stage 的 token 链 flag（B@sE_0f_CrYpt0_N0W），progressive 备份链不干扰。

## 题面信号

- 中文叙事双关：`某喜欢抽锐刻5的皇帝想每天进步一些，直到他娶了个模，回到原点` → "每天进步" = 每位置移位**递增**（等差），"娶了个模" = mod 26。
- 密文形态：`ksjr{EcxvpdErSvcDgdgEzxqjql}` —— 4-6 字母 + `{` 的**裸编码段**（无引号/反引号包裹），payload 混合大小写。
- 前缀自验证：`ksjr` → `flag` 的位移差为 +5,+7,+9,+11（a=5, b=2），等差自洽是判别锚点。

## 解法（引擎视角）

1. 反推法（anchor back-solve）：对每个「字母+{」位置，假设明文前缀是已知 flag anchor（flag{/ctf{/DASCTF{…），由前缀前两个字母的位移差解出 (a, b)，其余前缀字母必须一致验证等差性。
2. 解码花括号段：`shift_i = (a + b*i) % 26`（i 为段内偏移，`{` 占位），字母保 case，非字母原样。
3. `_flag_text` 命中 `flag{PleaseDoNotStopLearing}`，raw-echo gate 验证明文不在原文件（challenge.txt 只有密文）→ derived 计功。
4. b == 0（常量移位）跳过——那是标准 caesar stage 的域，不越权。

## 鉴别要点

- 与标准 caesar 的分界：**b ≠ 0**（移位随位置变化）；b=0 时退化为 25 shifts 可解的常量移位。
- 与 token stage 的分界：裸编码行（无包裹标记）只有 per-line/whole-file 文本输入能到达；wrapped_tokens 只覆盖反引号/引号包裹。
- 全池 3,606 文件扫描证据：该家族当前题源中**单点**（仅本题及镜像）；等差反推的伪命中由「前缀全等差验证 + flag 正则 + garbage gate」三层拦截。

## 引擎参数

- 检测器：`progressive_caesar_sweep`（fastpath/detectors.py，stage 2.5）
- 候选位置正则 `[A-Za-z]{3,12}\{`，上限 2000 位置/输入，段长上限 160
- 零 miss note（green 面 attempts 数不变），provenance gate 与其他文本 stage 相同

> AI生成
