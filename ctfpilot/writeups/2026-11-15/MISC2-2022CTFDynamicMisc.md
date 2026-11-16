# MISC2 わくわく (2022-CTF-Dynamic-Misc)

- **仓库/题名**: CTF-Archives/2022-CTF-Dynamic-Misc / MISC2（题名「わくわく」，出题人 IceCliffs，预估难度 简单-中）
- **flag**: 无静态可提取——归档附件为干净基带音频（动态题，run.sh `echo $GZCTF_FLAG` 注入）；官方 writeup 记录部署版 flag `flag{4bd390e9-78f8-4567-9204-1cf7477891de}`；提取链已本地端到端复放验证有效（合成 flag.wav → 全样本扫描 → hex 重组 → PK 边界切 zip → 密码解压）
- **耗时**: 选题约8分钟（CCB2024 18/18用尽→2025-CCB/2026-WQB/CCB2026-Quals 均仅-README→codeload批量探测→MISC2）；解题约7分钟（引擎 auto 20.6s NOT SOLVED 无 wav 样本检测器→源码审计定机制→全样本扫描 0 run 判定归档 wav 为基带→读 README 官方 writeup 学题解）
- **标签**: [misc] [audio-stego] [wav-sample-injection] [time-domain-stego] [zipcrypto] [hex-in-samples] [np-insert-flatten] [decoy-zip] [spectrogram-password] [dynamic-attachment] [gzctf-flag] [source-audit] [e2e-replay]
- **赛题说明**: Flask 8080 端口下发 flag.wav；源码级机制：`zip -P 'i1@mn!s*(e' /flag` → b2a_hex → `ord(c)*10` int16 样本 → `np.insert` 插入音频随机位（假压缩包诱饵插 ~16.5万、真压缩包插 ~185.9万）；压缩包密码 `i1@mn!s*(e` 藏频谱图（源码命令行注释同款）

## 技术链（全部脚本实证）

1. 判定：main.py 源码全透明——`np.int16(np.array([ord(c) for c in hexstr]) * 10)` 后 `np.insert(audio, pos, vals)`（无 axis 参数）注入样本流；假串大写 hex、真串 b2a_hex 小写 hex，尾部均拼 `'A'*random(10..100)` 干扰。
2. 提取器（纯标准库 array/struct/binascii/zipfile）：解析 RIFF→int16 样本→扫描连续 run（`s%10==0 且 480≤s≤1020`，长度≥16）→ `chr(s//10)` 重组字符串→定位 `504b/504B`→unhexlify→按 `PK\x03\x04..PK\x05\x06+22` 边界切 zip→zipfile 按密码读取。
3. 归档附件扫描：19,729,238 样本（44.1kHz 立体声 223s）0 命中→归档 wav 为干净基带音频，flag 不在附件内。
4. 端到端复放：模拟部署端合成 flag.wav（真数据：作者源码假压缩包 hex + 自造真压缩包 hex，×10 注入噪声音频）→ 提取链精确命中 2 run→假压缩包内容 `你是懂压缩包的…-rYu1nser`（与官方 writeup 截图一致）→真压缩包位提取出测试 flag ✓。
5. 官方 exp.c 锚点：`'5'*10=530, '0'*10=480, '4'*10=520` 三样本定位压缩包头（写入值域 480-1020 的 run 扫描是其泛化）。

## 标签

- 题型: 动态附件题 wav 时域样本值注入隐写 + 频域藏密码（misc）
- 识别信号: 附件 wav + 源码含 `np.insert`/`ord(...)*10`/`b2a_hex`/`zip -P` + run.sh `echo $GZCTF_FLAG`（动态题→归档附件多半是基带）
- 核心技术链: 样本 run 扫描（%10==0 小值域）→ hex 重组 → PK 边界切 zip → ZipCrypto 密码读取（密码源：源码命令行/频谱图）
- 陷阱类型: 前置假压缩包诱饵（内容文本嘲讽+`-rYu1nser`）；真假两包都要提；`np.insert` 无 axis 会把立体声展平成单声道；b2a_hex 小写 vs 源码字面大写
- 可迁移模式: 动态附件题先读 Dockerfile/run.sh 判 GZCTF 注入→归档附件全扫判基带→基带干净则直接学 writeup 或本地复放验证链

## 一得

1. np.insert 不带 axis 参数会先展平数组：立体声 wav 经该管道后输出变单声道；逆向时不能假设声道数不变。b2a_hex 产小写 hex，扫描器必须大小写双收。
2. 固化候选：「wav 时域样本注入提取器」——信号 = wav + 源码含 `np.insert`/`ord*10`/`b2a_hex` 任一 → 自动全样本 run 扫描（值域/取模/最小长度参数化）→ hex 重组 → PK 切片 → 密码候选（源码 `zip -P` 参数、频谱图）自动解压。
3. 动态附件题归档版速判：run.sh/Dockerfile 含 `GZCTF_FLAG`/`/flag` 回显 → 附件先全扫验证是否基带，干净即省下盲试时间，直接转 writeup 学习或 e2e 复放验证。

> AI生成
