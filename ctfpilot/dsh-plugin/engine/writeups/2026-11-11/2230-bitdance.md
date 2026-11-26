# CTF 刷题报告 — bitdance (2026-NCO-Quals)

- **日期**: 2026-11-11
- **题目**: CTF-Archives/2026-NCO-Quals reverse/Rev-简单夺旗-bitdance (20pts)
- **结果**: ✅ SOLVED
- **flag**: `NCO26{BiT_Ops_ruLE_iS_v3ry_E4$y}`
- **耗时**: 解题约 6 分钟（引擎 4s miss → 反汇编 + 逆向脚本），含收尾全程约 10 分钟，15 分钟盒内
- **标签**: reverse, elf, static-decompile, xor-key, rol8, state-feedback, div5-magic, algorithm-inversion

## 解法
ELF64 flag 校验器。objdump 反汇编定位校验函数：len==32，state=0x5a，key8=`{13,37,c0,de,42,99,aa,55}`，target32 在 .rodata 0x2040。
核心循环（全 8 位流密码）：
```
for i in 0..31:
    state ^= key[i%8] ^ flag[i]    # 注入
    out[i] = rol8(state, i%5)      # 旋转量 = i mod 5（除法 magic 0xCCCC...CD 识别）
    state ^= out[i]                # 反馈
out == target
```
逆向逐字节：`s1 = rol8(out[i], 8-i%5)` → `flag[i] = s1 ^ state ^ key[i%8]` → `state = s1 ^ out[i]` 链回代，正向重演回验 PASS。

## 一得
1. `mul 0xCCCCCCCCCCCCCCCD; shr rax,2` 是无符号除以 5 的 magic 常量模式，见之即读出模 5 量（旋转/桶索引等）。
2. rol 旋转对象易误读：本题旋转的是**状态字节**（xor 后）而非 key^flag 直接流——反馈链使 flag 逐字节依赖前缀，逆向必须链式回推，不能独立解。
3. 固化候选：NCO 逆向三连（unzip/pygloom/bitdance）都是"objdump + 自定义校验链手撕"，特征 = 静态比对表 + magic 模运算 + 字节级位旋转；引擎无 ELF 反汇编检测器是确定缺口。

## 题解学习要点
两轮在线检索 + 仓库 README 核查：NCO2026 闭门赛无公开 writeup，自证落库（learn-writeup 1 检查点 + 2 技能卡：bitdance-NCO2026-00/01.json）。解法唯一（校验算法可逆直接解），无更优路径。

## 失败原因
无。引擎 fast+deep 42 attempts 全 miss（预期内：ELF 静态逆向超出引擎边界），模型探索通道一次解出。占位符 `NCO26{.*}` 已按既定方案预先脱敏 + flag 正则收紧，本轮无假阳性。

> AI生成
