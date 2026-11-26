# CTF 循环 2023-zjsdxs Reverse-apkre（二攻·算法全解，flag 形态存疑）

- **日期**：2026-11-12 07:24–07:50（选题+下载约 5min，解题盒 15min 内）
- **选题**：CCB 仓库 18 题穷尽 → zjsdxs 第一道未做题 Reverse-apkre（3MB zip < 25MB）
- **结果**：算法层 SOLVED，机器层正确输入 40 字节确定（前向重放验证全过）；**平台提交 flag 形态 NOT SOLVED**（无公开题解佐证）
- **flag**：正确输入 hex = `b5a547222610a62a16af77bf00b2a6d9526ddb2a48da2704bcbc1bda6da7c141f4b5631bd963926c`（80 hex）；候选包装：base64=`taVHIiYQpioWr3e/ALKm2VJt2ypI2icEvLwb2m2nwUH0tWMb2WOSbA==`、md5=`1b219efc8ca88ad912a6cff7663952a0`
- **标签**：jni-crackme, arm64, capstone, elf-dynsym, plt-relocation-mapping, reversible-round-function, modified-utf8-trap

## 解法路径（快通道固化目标）
1. APK=zip 解包：libsec.so(356KB,arm64) + 假 liblog.so(纯日志空壳) + classes3.dex(2KB，仅 UI 链)
2. dynsym 全量 dump → `Java_com_example_crackme_JNITest_getJNIString @0x28148(928B)` 静态导出（**推翻上轮"动态注册"误判**，上轮 writeup 落库中断于 step2 未及此步）
3. PLT 导入名校准（LLD arm64 PLT header 32B，rela[k]↔PLT[+0x20+k*16]，初版差 1 槽全错位，用 log_print(3,tag,fmt)/GetEnv(vm,&env,0x10006) 参数特征校准）
4. capstone 5.0.7 反汇编：strlen==40 → 5 组×(A,B)u32 → 每组 28 轮可逆轮函数（5 常量 K1..K5，lsr 逻辑右移）→ 末态 vs T[10]@0x1b8d4（B28==T[2i], A28==T[2i+1]）
5. tE 仅依赖(t7,s')、t6 仅依赖(B,t0) → 全轮可逆，逆推 40 字节输入，前向 assert 全过

## 失败原因（flag 形态未定）
- GetStringUTFChars 是 modified UTF-8（U+0000→C0 80），逆推输入含**裸 \x00** → 任何 Java String 均无法产生该字节流，UI 输入物理不可达 → 出题人不预期真实输入，flag=机器答案的文本包装，但格式（hex/b64/md5/DASCTF{}）无据可判
- 检索 2 轮无公开题解（同届仅 Ez8or 有 CSDN WP，apkre 无）；闭门赛事，自证路径已至极限

## 题解学习要点（无公开 WP，自证沉淀）
1. arm64 PLT 槽映射：rela.plt[k] ↔ PLT base+0x20+k*16，初判差 1 槽导致导入名全错——**必须用调用参数特征交叉校准**
2. 无 Java_ 前缀命中 ≠ 动态注册：先全量 dump 定义符号（本题为 844 个）再人工过滤
3. JNI crackme 输入含非法 modified UTF-8 字节即 UI 不可达判据：flag 必为机器答案的编码包装
4. CTFPilot learn-writeup 对自定义"## 标签"行解析不出 techniques（上轮同现象）——writeup 标签行格式需按 RSI §6 模板（下轮修正）

## 一得
apkre 二攻闭合上轮中断点：上轮卡在"动态注册误判+objdump 不支持 arm64"，本轮 capstone（上轮装的包资产留存）+全量符号 dump 15 分钟内完成算法还原；固化候选：①arm64 ELF 轮函数逆推模板 ②PLT 导入名解析器（32B header 偏移+参数自校准）

## 下轮候选
zjsdxs 剩 Reverse-ida_pro、Web-ezWEB；fjgys 剩 4 题；SSSMMM5 续攻（备 LLL）

> AI生成
