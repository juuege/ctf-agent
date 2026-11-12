# CTF 循环任务报告：2023-fjgys / Misc-ezudp

- **时间**：2026-11-12 03:46（本地）
- **题目**：CTF-Archives/2023-fjgys / Misc-ezudp（题面：udp server and client，1.5MB zip，解出双 Go ELF）
- **结果**：❌ NOT SOLVED（15 分钟时间盒用满，未拖轮）
- **flag**：未获取
- **耗时**：选题+下载解压 ~4min；类型侦察+引擎 solve(24.6s 假阳性)+脚本复核 ~4min；Go 符号恢复冲刺 ~7min；收尾 ~5min
- **标签**：go-reverse, elf-stripped, gopclntab, udp-protocol, misc, fake-positive-xor

## 过程

1. 选题：CCB 18 题复核穷尽 → fjgys 目录序，跳过 SSSMMM5（前轮已试、需 LLL 引擎）→ 第一道未做题 Misc-ezudp
2. 侦察：双 ELF 为 Go 1.17 静态编译（非 PyInstaller），client/server 各带高熵 base64url 样数据段
3. 引擎 solve --mode auto：24.6s 报 SOLVED `ctf{W~am}` → 脚本复核为**假阳性**（xor-single 对整 ELF 爆破，命中点 0x13cbb8 后随乱码 `kbkMlnb...` 无语义）
4. 模型补位：nm 确认 symtab 已 strip → .gopclntab 保留（Go 栈追踪必需）→ 纯 Python 解析器恢复函数表 → **连错 2 次**（1.17 pcHeader 无 textStart / _func.nameOff 位置）→ 按纪律停止盲试

## 失败原因

- 根因：pclntab 布局 Go 1.16/1.17/1.18+ 三代不同，临场凭记忆猜格式连错 2 次耗尽时间盒；属"临场才学外部知识"瓶颈
- 若先对标 Go 官方 runtime/symtab.go（10 分钟内可写对解析器），15 分钟盒内可定位 main.main 并完成初步反汇编

## 题解学习要点

- 两轮检索（在线搜 fjgys 2023 ezudp writeup + GitHub API 搜 writeup 仓库）**均无公开题解**：地方闭门赛，赛事仓库无 README/题解链接
- 自证路径已固化 learn-writeup 入库（1 检查点 + 技能卡）：gopclntab 恢复、假阳性复核法、pclntab 版本差异

## 一得

**Go pclntab 三代布局速查**：1.17=functab 项 16B(entry 绝对地址 8B+funcoff 8B)、pcHeader 无 textStart；1.18+=项 8B(entryoff 相对 textStart)、magic 0xFFFFFFF0 且含 textStart。strip 的 Go ELF 必留 .gopclntab，函数名可完整恢复。

## 下轮建议

- 对标 Go runtime/symtab.go 修正 1.17 解析器（到手 5 分钟定位 main.main）续攻 ezudp；或按序做 fjgys Reverse-double / Misc-签到2
- 固化候选：**Go pclntab 函数表恢复器**（纯标准库，跨 1.16/1.17/1.18+ 三代布局）
- done.json 90 条不变（NOT SOLVED 不标，避免阻塞下轮重复选择，续攻建议已记入本报告与记忆）

> AI生成
