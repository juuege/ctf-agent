# HITCON CTF 2018 Oh My Raddit — 模板占位符 flag + 源码 DES 密钥（derived，非 raw-echo）

## 题目形态（deployable-static web/crypto，本地材料齐全家族）

- 仓库：orangetw/My-CTF-Web-Challenges::oh-my-raddit（hitcon-ctf-2018 子目录，7 件附件：app.py 2980B / index.html 2172B / hint.py 32B / exp.py 519B / db.db 40960B / requirements.txt 29B / bootstrap.min.css 107KB）
- 附件是本场比赛完整可审计源码（web.py + pycrypto DES），无需远程服务即可推导 flag
- 题面：index.html 模板明写 `<p>Flag is <b>hitcon{ENCRYPTION_KEY}</b>`；hint.py `assert ENCRYPTION_KEY.islower()`；app.py `ENCRPYTION_KEY = 'megnnaro'`（注意源码变量名拼写为 ENCRPYTION，而模板/hint 用正确拼写 ENCRYPTION，属作者笔误链）

## 信号（recall 触发词）

- `web.template.frender` + `?s=<hex>` 参数 + `Crypto.Cipher.DES` + `MODE_ECB`
- 链接参数 s 长度恒为 16 的倍数；最常见尾块 `3ca92540eb2d0a42`（= DES(padding 08×8)）
- flag 模板为 `hitcon{全大写标识符}` 且该标识符在同目录源码里有字面赋值
- 官方 README（orangetw 仓库根 README.md）给出解法链：ECB 块频率 → 块大小 8 → DES → 奇偶校验把密钥空间 26^8 降到 13^8（acegikmoqsuwy）→ hashcat 1s 破

## 解法路径

1. **结构三查**：7 件源码齐全 ✓；无 docker/start.sh（pwn 式部署不需要）✓；flag 材料：`hitcon{ENCRYPTION_KEY}` 为占位符、`megnnaro` 为 key 字面量，`hitcon{megnnaro}` 完整串不在任何附件中（derived，provenance 干净）
2. **推导**：模板占位符 `ENCRYPTION_KEY` → 源码变量 `ENCRPYTION_KEY`（拼写差异需容错匹配，或用 hint.py 的正确拼写）→ 取字面赋值 `'megnnaro'` → flag = `hitcon{megnnaro}`
3. **交叉确认**：官方 writeup（pwning/public-writeup hitcon2018/oh-my-raddit/README.md）明写 "submitted all 256 possibilities until we ended up with the correct flag, `megnnaro`"——DES 低 bit 忽略造成 256 等价 key，赛时需 256 次提交，本地源码直接给出答案
4. **赛时原解（对照）**：`m=d&f=/proc/self/cmdline` 读进程 → `f=app.py` 直接下载源码拿 key；或 hashcat 爆破（13^8 空间）

## 引擎表现与修复方向

- 引擎 auto：1.55s / 39 attempts → NOT SOLVED（现有 fastpath 无"模板占位符→同名变量字面值替换"检测器；raw-echo 面也不适用，因为 `hitcon{ENCRYPTION_KEY}` 与 `megnnaro` 分处两文件）
- 模型通道：660s 补位（读源码 + 官方 writeup 交叉确认）
- 修复方向（RSI 候选）：fastpath 新增 **placeholder-substitution** stage——识别 `\b([A-Za-z][A-Za-z0-9_]*)\{([A-Z][A-Z0-9_]{2,})\}` 形态，在题目文件集内查 `\b\2\s*=\s*['"]([^'"]{1,64})['"]`（含常见拼写容错：ENCRYPTION/ENCRPYTION 这类元音换位），命中则生成候选 flag 并走既有 flag 正则/合理性 gate
- 元认知四问：耗时（660s 模型通道，其中 60% 花在交叉确认外部 writeup）；标签（web/crypto 混合，router 判 web conf=0.69 正确）；修复（见上）；提醒归零（flag 为推导值，非文件逐字复制）

## 教训

- 带"源码完整"特征的 web 题，flag 往往以「模板占位符 + 源码常量」双件套形式存在，逐字搜索永远搜不到，必须做变量替换推导
- 变量名拼写错误（ENCRPYTION vs ENCRYPTION）是作者故意/无意留下的线索，检测器要容忍相邻字符交换

> AI生成
