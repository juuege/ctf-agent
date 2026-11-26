# CTF 循环任务报告：2025-jswa crypto1（签到题）

- **日期**: 2026-11-11 18:50
- **题目**: CTF-Archives/2025-jswa · crypto1 签到题（2025 江苏省第七届大学生网络空间安全知识技能大赛）
- **结果**: ✅ SOLVED
- **flag**: `flag{dfb5beee846947}`
- **耗时**: 选题+下载约 9 分钟（CCB 已尽 → 探测组织仓库，2025-CCB/WQB2026 等无 attachments 或 zip 超 25MB 跳过 → 定位 2025-jswa）；解题约 7 分钟（引擎 2.8s NOT SOLVED → 模型补位脚本枚举）；收尾约 4 分钟。总约 20 分钟盒内。

## 解题路径

1. 题面：51B 文本「解密如下内容: kaljviko8ohhh59;49:p」（20 字符密文，小写字母+数字+标点混合）。
2. 引擎 auto（fast+deep mock）34 次尝试 NOT SOLVED——密文内嵌中文文本文件，triage=misc，deep 的 xor-report 不触发。
3. 模型补位：先按外观假设键盘密码，全量枚举行内循环移位/列移位/8 对角/QWERTY 26 序凯撒/全键盘 47 序凯撒/DVORAK/AZERTY/QWERTZ/T9/镜像/奇偶拆分——全 miss。
4. 单字节 XOR 全 127 键位扫描 → **0x0D 命中 `flag{dfb5beee846947}`**；解密↔重加密往返一致，flag 结构闭合。

## 一得

- **已知明文 XOR 推导 > 全量枚举**：密文首 5 字符 `kaljv` 若对应 `flag{`，则 k=c[0]^'f'=0x0D 一次出密钥，秒级定论；本题先走了 20+ 种键盘变换弯路，混合字符集"键盘密码外观"是假象——XOR 后的 flag 文本同样产生混合字符集。
- **引擎 miss 根因**：deep 的 xor-report 仅对 crypto blob 触发，中文题面内嵌密文的 .txt 不被扫描；短密文场景应先做已知明文推导再谈其他变换。

## 题解学习要点

- 赛事 README 仅一行赛事名，无解法链接；两轮检索无公开 writeup（省级赛事签到题），按模板自证落库：**1 检查点 + 1 技能卡**（single-byte XOR key derivation）。
- 对照自证发现更优路径：flag{ 已知明文推导比全键位扫描快一个量级，已写入技能卡 when_to_use。

## 框架修复（当场执行）

- `writeup_learner.py` TECH_PATTERNS 无 XOR 模式，首次落库技能卡为空 → 增量补 `xor|异或 → single-byte XOR key derivation` 一条（不动既有模式），重跑落库生效，recall 检索验证通过。

## 标签

- 识别信号=短混合字符集密文(小写+数字+标点、无空格)+中文题面内嵌；陷阱类型=键盘密码外观假象；可迁移模式=疑似密文先做 flag{ 已知明文 XOR 推导；变体预测=XOR 换密钥/前缀变体。
- 可固化候选（频率不足暂缓）：.txt 含 15-60 字符连续混合字符集串 → flag{ XOR 一致性推导，命中即解。

## 失败原因

无（本题解出）。记录弯路：键盘密码假设耗约 4 分钟，教训已转化为可迁移模式。

## 下轮候选

2025-jswa 剩余：crypto2、crypto3、misc1、misc2、re1-3、pwn1、web1（下轮第一顺位 crypto2）。

> AI生成
