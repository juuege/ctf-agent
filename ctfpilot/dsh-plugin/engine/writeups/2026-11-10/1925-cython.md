# cython（2024 CCB×CISCN Quals / reverse）

- 结果：**NOT SOLVED**（无 flag；本题纯本地校验，flag 需还原 encrypt 算法后解密）
- 总耗时：约 22 分钟（超 15 分钟解题盒，题解学习含在内）；上轮曾跑 solve NOT SOLVED 后中断，本轮续做
- 题目链：cython.zip → cython.exe（PyInstaller）→ pyinstxtractor 解出 `_extracted/test1.pyc`（Python 3.11, magic 3495）+ `ez.cp311-win_amd64.pyd`（Cython 3.0.8 编译）

## 已还原逻辑（test1.pyc，xdis/pydisasm 重建）

```python
import ez
flag = input('请输入flag:'); flag1 = list(flag)
if len(flag1) == 24:
    value = [ord(f[i])<<24|ord(f[i+1])<<16|ord(f[i+2])<<8|ord(f[i+3]) for i in range(0,24,4)]  # 6×uint32 大端
    key = [102,108,97,103]  # "flag"
    flag_encrypt = [ez.encrypt(value[i], value[i+1], key) for i in range(0,6,2)]  # 3 块
    ck = ez.check(flag_encrypt)
    print('yes!!!,you get right flag' if ck == 3 else 'wrong!!!')
```

## 黑盒实测（Python 3.11.9 embed + 隔离 pyd，环境存 `.temp\py311`、`.temp\ezbox`）

- `ez.encrypt(v0,v1,key)` 返回 **(r0,r1) 元组**；样本：e(0,0)=(846454484,3421924227)、e(1,2)=(1519462495,880661584)、e(0x666C6167,0)=(786528606,3538108699)
- `ez.check(x)` 内部访问 `x[0][j]、x[1][j]、x[2][j]`（传单元素列表→IndexError，传 int→'int' object is not subscriptable），返回匹配块计数
- `ez.__dict__` = 完整 ctypes 符号表 + encrypt/check → 源码形态 `from ctypes import *` + 两个函数，用 c_uint32 做掩码运算
- **三个 uint32 魔数以十进制字符串嵌入 pyd**（Cython `PyLong_FromString` 特性）：2248247555=0x86058B33、3785781401=0xE19D8ED9、3805970057=0xE2F13EA9；非 LE 二进制存在，疑似 encrypt 参数或 check 比较目标
- 排除：标准 TEA/XTEA（delta∈{0x9E3779B9,三魔数}, 轮数∈{16,32,33,64}, k=[102,108,97,103]）对拍 e(0,0)/e(1,2) 全不中

## 失败原因与卡点

1. encrypt 为 TEA 类自定义变体（PyNumber_Xor/Lshift/Rshift/And 全在导入表），标准参数枚举不中，需反汇编还原轮函数
2. check 的目标密文未能提取（3 个字符串常量 ≠ 3 对目标，check 可能对块内元素再运算）
3. 踩坑 2 个：① GitHub raw 下载 zip 超时留 0 字节（复用上轮已解压产物化解）；② `_extracted` 整目录入 sys.path 会命中 PyInstaller 版 ctypes.pyd（无 PyInit_ctypes 导出）→ ImportError，须隔离 pyd 单文件

## 下轮前置（已就绪）

- capstone（装 `.temp`）反汇编 pyd，锚点：`__pyx_pf_2ez_encrypt`/`check` 函数序、字符串 `2248247555` 地址 xref、`PyObject_RichCompare(...,3)` 找 check 比较目标
- Cython 逆向方法论（看雪《Cython逆向-语言特性分析》）：__pyx_string_tab 定位工程、__Pyx_InitConstants 的 PyLong_FromString 捞全部整数常量、函数命名 `__pyx_pf_<len><模块>_<序号><名>`、比较用 RichCompare(op: LT1/LE2/EQ3/NE4/GT5/GE6)

## 七维标签

reverse | cython-compiled | pyinstaller-extract | py311-pyc | tea-variant | blackbox-harness | largeint-string-const

## 一得

Cython 把大整数常量编译成 `PyLong_FromString("十进制串")`——**strings 扫数字串即可捞全部魔数/密文候选，无需反汇编**；本题 3 个 uint32 全部由此到手。固化候选：pyd/so 中"纯数字可打印串"提取器。

## 题解学习要点（本轮检索：无该题公开 WP；姊妹方法论文章补齐）

- 长城杯初赛 RE 公开 WP 覆盖 easyre/tmaze/xiran_encrypto，**不含 cython** → 本题公开 writeup 缺失，已按技能规则学习 Cython 逆向通法替代
- Cython 逆向通法（看雪文章核心）：annotate=True 生成 pyx↔C 对照、__pyx_string_tab 集中管理字符串常量、__Pyx_InitConstants 集中构造 int 常量、def 函数 wrapper(__pyx_pw_)→实现(__pyx_pf_)、引用计数 GOTREF/GIVEREF 模式可当指令边界识别

> AI生成
