# CTF 20min 循环报告 — 2023-zjsdxs 决赛 Reverse-ida_pro

- **日期时间**: 2026-11-12 08:03（本地）
- **选题**: CCB 仓库 18 题已穷尽 → 探测组织仓库 → 2023-zjsdxs 字母序第一道未做题 Reverse-ida_pro（附件 41KB）
- **FLAG**: **NOT SOLVED**
- **耗时**: 引擎 7.3s（16 attempts，fast+deep 均未命中）+ 模型补位约 12 分钟；20 分钟盒到点收尾

## 过程

1. 题面"你会不会用ida_pro"，解压得 86577B PE（未执行）。
2. 纯标准库 PE 剖析，四项结构异常证据：
   - `ptrsym=0x15800` 超出文件末尾 `0x15231`（COFF 符号表"截断"1487 字节）
   - 三节名全部被抹为 `OPXX`（原始 4f505858，非 .text/.rdata/.idata）
   - 节1 vsize=0x19000 但 raw rsize=0
   - overlay 58KB = mingw COFF 调试符号/字符串表（crtbegin.c / ida_pro.cpp / .debug_*）
3. `.rdata` 偏移 0x101f 找到 `DASCTF{W` + 4 乱码字节（7f 82 8a e4）+ `ow_p=@`，与 MSVC matherr 字符串（#DOMAIN/(retval=%g)）交错混排——疑似部分字节变换的 flag，未还原出变换逻辑。
4. 盲试两轮到上限 → 题解检索：README 官方链接为 Misc 篇（t/13043，无此题）；微信 Reverse 篇已删；通用搜索污染；GitHub code search 401、repo 搜索仅题库 ⇒ **闭门赛无公开题解**。

## 标签

`reverse` `pe-coff` `mingw` `static-analysis` `ptrsym-out-of-file` `section-name-wiped` `overlay-coff` `partial-plaintext-flag` `not-solved`

## 一得

**带 COFF 调试信息的 MinGW PE 先做结构一致性剖析再谈反汇编**：ptrsym 越界 filesize = 符号表截断/伪造第一判据；三节同名 = 节表被人为改写；.idata$7 系字符串出现在"节外"偏移 = 数据实为 COFF 字符串表而非节内容。固化候选：**PE 结构一致性剖析器**（ptrsym/nsym/节 raw 越界 + overlay COFF 分类 + 节名异常，3 秒出全部结构证据，本案可省 ~8 分钟）。

## 题解学习要点

无公开题解可学（自证落库）。已沉淀检查点：①ptrsym>filesize 判据 ②节名被抹判据 ③明文 flag 片段+乱码交错 ⇒ 部分字节变换而非全文加密 ④overlay 分类方法。writeup 已 learn-writeup 入库（1 检查点）。

## 失败原因

- 无反汇编器环境（无 IDA/Ghidra），15 分钟盒内纯静态手解 x86 变换逻辑未闭合；
- "DASCTF{W" 后 4 字节变换未定性（GBK/UTF-8/单字节 XOR 均排除，疑似位置相关变换，需反汇编 0x101f 的交叉引用）；
- 时间盒约束下两次盲试即触发技能搜索纪律，检索确认无公开题解后按自证模式收尾。

## 下轮候选

- 2023-zjsdxs 剩 Web-ezWEB；2023-fjgys 剩 Reverse-double / Reverse-mips2；
- 备选：预研"PE 结构一致性剖析器"固化（对照 RSI SOP），或先解 Web-ezWEB。

> AI生成
