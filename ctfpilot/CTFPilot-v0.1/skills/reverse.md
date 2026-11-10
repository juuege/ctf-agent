# reverse 技能卡

## 决策顺序
1. 先做无损侦察：字符串、节表、入口点、是否加壳（UPX 特征 `UPX!`）。
2. 纯静态逻辑还原优先（把关键函数反编译成伪 C 或 Python 等价物），其次模拟执行。
3. 若题目是 Python 打包（pyinstaller）→ 解包提取 pyc，再 uncompyle/decompile。

## 常见打法
- 字符串/常量表扫描：flag 混淆常直接藏在常量表或逐字符异或表。
- XOR 型逻辑：提取循环体，写成 Python 等价函数直接运行。
- 打表转换：识别 256 字节的 S-box（直接把表 dump 成 python bytes）。
- VM 题：提取 opcode 表 + dispatch 结构，写模拟器（偏好静态翻译而不是单步模拟）。
- 反调试：时间差（rdtsc）、isDebuggerPresent 标志位跳转。
- Android：apktool 解包 → smali/反编译成 java。

## 产物要求
每次分析输出：关键函数伪代码、数据表 dump、下一步假设。逻辑还原后直接用 Python 等价脚本跑出 flag。
