# 2024-ShuXinCup-sjaq-easyjava 解题记录

## 题目

- **题目**：2024 数信杯 sjaq 赛区 easyjava（CTF-Archives/2024-ShuXinCup-sjaq::easyjava，ledger 114）
- **分类**：misc（Java 取证 / 内存取证 / 编码）
- **附件**：描述.txt（105B，「某个精通百家姓的开发者把自己的小秘密藏在了附件中，来查查他有什么秘密。」）+ easyjava_2dfb11a009041a3879f33f8af00c5121.zip（692KB，内含 easyjava.hprof 1,994,113B）
- **引擎**：NOT SOLVED 2.81s / 18 attempts rc=1（router misc conf=0.80，zip magic + .txt）
- **模型通道**：约 2370s 解出，`flag{aa534ac5-2f1a-466c-baf7-1ad7edb6adb2}`

## 识别信号

- 描述.txt 含「百家姓」+「小秘密」双关键词 → 汉字编码类（百家姓码表）强信号
- 附件唯一文件 `easyjava.hprof`，魔数 `JAVA PROFILE 1.0.1\x00` → Java heap dump 取证
- dump 头 identsize=8；`thread#6 = main @ Test.java:15`（STACK_FRAME 0x04 记录），main 线程停在 `Thread.sleep` —— 程序 dump 时运行中，主类名 Test

## 解法链

1. **HPROF 1.0.1 手写解析器**（零依赖）：
   - header：version\x00 + u4 identsize(8) + u4 ts_hi + u4 ts_lo，记录体从 offset 31 开始
   - 记录：STRING(0x01) id+bytes；LOAD_CLASS(0x02) serial+cobj+sts+cnameId；HEAP_DUMP(0x0C) 单段
   - heap 子记录：0x21 INSTANCE(objId+u4+classId+u4 nBytes+fields)；0x22 OBJECT ARRAY(objId+u4+u4 nelem+arrayClassId+ids)；0x23 PRIMITIVE ARRAY(objId+u4+u4 nelem+u1 type+data)；0x20 CLASS DUMP
   - **CLASS DUMP 头 = id(8)+u4 serial+6×ID(super,loader,signers,protDom,reserved1,reserved2)+u4 instanceSize**；constant pool/static/instance field 值尺寸表 **{2:8, 3:8, 4:1, 5:2, 6:4, 7:8, 8:1, 9:2, 10:4, 11:8}**（type=8 byte 占 1 字节，type=2 object 占 idsize）
   - root 子记录（0x01-0x08）额外字节表：JNI_LOCAL/JAVA_FRAME +8、JNI_GLOBAL +8、STICKY_CLASS/THREAD_BLOCK/NATIVE_STACK +4
   - 解析产出：691 类 / 8093 实例 / 5118 primitive array（4 次自愈重同步，兜底扫描下一个 0x21）
2. **应用类定位**：非 JDK 前缀类仅 `Test`（无实例字段，heap 无实例）；其 CLASS DUMP statics 只有 JVM 内部 `<resolved_references>`（id 0x6c5a11268）→ 该 Object[5] 即 Test 引用的常量池对象（**字符串字面量全在里面**）
3. **字节序陷阱（本题最大坑）**：String.value 指向的 char[] 数据是**大端（UTF-16BE）**。LE 视角全是「高字节递增的 CJK 假象」+「高字节是真 ASCII 的错位串」；JVM 消息 `Stream closed`、`malformed input: partial character at end` 的 LE-高字节序列反向证实。**对 4 个字面量直接 UTF-16BE 解码即得真实文本**
4. **4 个字符串字面量**（resolved_references 元素）：
   - ①「想跟你说一个小秘密：我把码表修改成了\n」
   - ② 64 字百家姓码表「赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐」+费+\n
   - ③「真的旗帜怎么会这么轻易就让你拿到,但你都找到我了那我给你一个密文吧：\n」
   - ④ 56 字百家姓密文「曹窦韦谢曹方范谢孔许何马沈郑吴喻沈尤苗昌曹喻吴谢卫许朱花沈窦蒋范孔窦吴窦沈昌苗韦孔施朱方曹施秦邹沈窦吴柏孔喻陈鲍」
5. **解密**：64 字码表 = 2^6 → 自定义 base64 字母表；密文 56 字 → 索引序列 → 6bit 拼接 → 336 bits = **42 字节** → `flag{aa534ac5-2f1a-466c-baf7-1ad7edb6adb2}`
   - 交叉验证：索引映射到标准 base64 字母后 `base64.b64decode` 字节级一致（ZmxhZ3thYTUzNGFjNS0yZjFhLTQ2NmMtYmFmNy0xYWQ3ZWRiNmFkYjJ9）

## 陷阱

- hprof 手工构造痕迹：JAVA PROFILE 1.0.1（老版本号）+ 字节序 BE —— 标准 x86 JVM dump 是 LE，**直接 UTF-16LE 解码会全军覆没**（递增 CJK 假象把注意力带向「垃圾填充数组」）
- 4 个字面量看起来是「二进制乱码」是因为提取了每字符高字节（LE 视角）；BE 解码才是正解
- 码表字符串 nelem=66（64 码表字 + 1 个「费」+ \n），「费」是百家姓第 65 位但不参与编码——若把 65 字当字母表会错位
- 类/字段名线索（USER_PASSW、getPassword、'flag' 孤立串）全是 JVM 内部字符串表噪声，与 flag 无关

## 可迁移模式

- **hprof 取证三板斧**：①解析 heap dump 拿 Test 类；②CLASS DUMP statics 里的 `<resolved_references>` 指向常量池 Object[]——**程序所有字符串字面量的藏身处**；③对字面量 char[] 做 BE/LE 双解码（LE 视角高字节序列若还原出可读 ASCII/英文单词，说明信息在「错位字节」里，换 BE 重新解）
- **百家姓 64 字 = 自定义 base64**：密文长度 ×6/8 = 明文字节数；56 字 → 42 字节恰好 `flag{` + 36 + `}`；标准百家姓前 64 位（赵…唐）是默认码表，「精通百家姓」题面 = 该编码的直接信号
- 单文件 2MB 内 + 描述带汉字关键词 → 优先手写格式解析器而非 strings 大海捞针

## Provenance

- flag 不在附件中逐字出现（编码于 56 字百家姓密文内），provenance = derived
- 引擎未解（无 hprof 检测臂），模型通道解出并 account-model 入账（--time-s 2370）

> AI生成
