# CTF 刷题报告：2026-qwbs10-quals / Reverse / license

- **结果**：未解出（15 分钟解题时间盒耗尽，按约定收尾）
- **flag**：无（需先恢复白盒 AES 密钥反推 license，超盒）
- **耗时**：选题+下载 ~6 分钟（API 限流等待+Range 取件）；解题 15 分钟（快通道 161.6s NOT SOLVED → 模型静态逆向 ~12 分钟）；学题解 ~4 分钟
- **标签**：`reverse` `whitebox-aes` `chow` `anti-debug` `custom-section` `prng-keystream` `static-glibc`

## 解题过程
1. **选题**：2024-CCB-CISCN-Quals 附件 18 题已全部做完 → 探测组织其他仓库 → 选 2026-qwbs10-quals（第十届强网杯线上赛）
2. **下载**：release 单包 82MB 超 25MB 红线，但为赛事打包物非单题；用 HTTP Range 只拉取 license 条目 <1MB（先取末尾 128KB 解析 EOCD/中央目录，再按 local header offset+comp size 定向下载，CRC 验证解压成功）
3. **快通道**：`solve --mode auto` NOT SOLVED（161.6s，自定义逆向超出引擎检测器边界，预期内）
4. **静态逆向（已完成 90%）**：
   - 剥离 ELF 残留 24 个 symtab 符号：`chow_wb_encrypt`/`tbox_round_lookup`/`wb_verify_fold`/`ext_encoding_apply`/`sec_env_probe` → Chow 白盒 AES 题型
   - 主链：反调试探测 → 读 license(≤97B) → 白盒 AES ECB 加密(48B) → 48 字节 XOR 折叠比对 `wb_tgt` → `ext_encoding_apply` 用 `.dty` 滚动哈希×g_acc×校验折叠值做 PCG 种子解密 128B `wb_rec` → 校验 "LIC\x01" → 打印偏移 0x1c 的 46 字节（即 flag）
   - **卡点**：种子分量 `g_acc` 由白盒加密尾部 3000 万次迭代混合末块密文生成——依赖正确输入且兼做 timing 反调试，离线不可伪造；已验证 g_acc∈{0,0x51d2f3a4b6c7e809}×fold∈{0,1} 四种组合均解不出 LIC 魔数
5. **完整解法（超盒，留待续做）**：BGE/DFA 白盒密钥恢复 → ECB 解密 `wb_tgt`(48B@0x57e3c0) → 得 license → WSL1 运行二进制输入 → 程序自解密打印 flag

## 题解学习要点（收尾 4 分钟）
- 官方系列 WriteUp（CSDN jinyiwei，169830703）覆盖 Crypto×2/Misc×3/Reverse×1（vmshare 中间相遇、frozen FFT 树聚类、ELFStudy 三层隐藏校验、0perator kiosk 逃逸、Dreambound 远端评分、NeuronShuffle 神经元等价类），**无 license 专项题解**（全网检索 2 次未果，赛事 11-01 结束仅 10 天）
- ELFStudy 方法论可迁移："表面正常 ⇒ 找被 nop 的调用点；目标是密文 ⇒ 找解密器；解密器自毁 ⇒ 重定位阶段 dump；校验是 Fibonacci ⇒ 反向递推"
- 本题学习对照：我定位的 PRNG 密钥流解锁结构与 ELFStudy "自毁解密器"同族，均属"把 flag 逻辑藏在静态不可见路径"；差异在 license 的种子绑定运行时加密状态，纯静态无法闭合

## 一得
- **固化候选**：`release-zip-range-fetch` —— GitHub release 大包用 HTTP Range（206 已实测）先拉末尾解析 zip 中央目录，再按条目 local header offset 定向下载，82MB 包只传 <1MB；api.github.com 限流场景同样适用
- 剥离 ELF 先查残留 symtab 符号与自定义段名（.dty），可直接读出函数语义省去大量定位
- learn-writeup 引擎归类再次误判（reverse→crypto，与借书码终端/教师面板同款），已记入 skill 自审项

## 失败原因
- 白盒 AES 完整密钥恢复（BGE 攻击需逐轮 T 盒分析，工程量大）+ g_acc 3000 万次迭代的运行时依赖，双卡点叠加超出 15 分钟时间盒；策略上若开局即判明白盒题型，应直接跳选轻量题（如 ELFStudy 同场 Reverse 或 Web/cwp 源码题）

> AI生成
