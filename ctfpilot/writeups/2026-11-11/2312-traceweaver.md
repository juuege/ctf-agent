# traceweaver（2026-NCO-Quals reverse/中等夺旗 40pts）

- **flag**: `NCO26{d38u6_M3_1f_Y0u_C4n!!}` ✅ SOLVED
- **耗时**: 约 10 分钟（15 分钟盒内；引擎 4s NOT SOLVED → 静态分析 ~6min → 脚本解密 1min → 收尾 3min）
- **题目**: traceweaver，14KB 静态 ELF64，读入 28 字节 flag 校验，"绕过干扰，还原真实校验逻辑"
- **标签**: 识别信号=ptrace 反调试+memcmp+scanf、无 flag 明文；陷阱类型=花指令反汇编错位（11b7 假 jmp）+ "干扰函数"实为密钥装载 + ABI 寄存器残留隐式传参；可迁移模式=「看似破坏输入的函数可能是 key 装载，需跟踪 call 时刻 rsi/rdx/rcx 残留再判校验参数」；核心技术链=objdump 静态反汇编 → 内联 RC4（KSA@0x1380 div+swap、PRGA@0x1500 异或）→ 密文@0x2040 标准库解密
- **解法**: f1@0x1370 向 caller 栈顶写 "can_you_debug_me"（16B，看似干扰）；call f2 时 rsi=0x1c/rdx=栈顶/ecx=0x10 未重设即隐式传参（数据/密钥/长度）→ f2 内联标准 RC4 KSA+PRGA → memcmp(rsp+0x10, 0x2040, 28)。key="can_you_debug_me" 直接解 0x2040 密文即 flag
- **失败原因**: 无。引擎 fast/deep 42 attempts NOT SOLVED（缺 ELF 静态反汇编检测器），模型补位一次通过
- **题解学习要点**: 公开检索（题名+赛事+writeup）无结果，赛事仓库 README 仅题目清单无解法链接，GitHub search API 限流——NCO2026 闭门赛无公开 writeup（与前 4 轮结论一致），自证落库：writeup 入库 + 技能卡 traceweaver-NCO2026-00/01.json（-00 为 learn 自动概括偏离核心，-01 版本化修正为 RC4 静态识别）
- **一得**: ①RC4 识别看两个指纹：KSA 的 `div r9` + 256 轮 swap 循环、PRGA 的 `(i+1)/j/S[(S[i]+S[j])&0xff]` 异或循环；②"绕过干扰"题的干扰常是双层——花指令让 objdump 错位 + 假密钥函数，真正参数靠跨 call 寄存器残留传递，反汇编时必须跟踪 ABI 寄存器在 call 时刻的值而非只看显式 mov；③固化候选：ELF 静态 RC4 检测器（KSA 指纹 + memcmp 引用密文 + 可读 key 串），待第 2 次同类命中按 SOP 固化
- **进度**: done.json 更新至 83 条；NCO 仓库 reverse 已尽（unzip/bitdance/pygloom/traceweaver 全解）。下轮候选：NCO pwn/PWN-入门夺旗-parrotbooth（5pts）→ PWN-简单夺旗-史莱姆实验室/超级无敌扭蛋机（20pts），或 2023-Bluehat-Quals 剩余 7 题

> AI生成
