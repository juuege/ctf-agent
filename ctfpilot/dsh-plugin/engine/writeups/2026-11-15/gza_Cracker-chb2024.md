# gza_Cracker-chb2024（楚慧杯 misc，哥斯拉流量密钥爆破+解密）

- **题目**：CTF-Archives/2024-chb / gza_Cracker（misc，Crack_me.pcap 33.6KB，SLL2 链路 linktype=276）
- **flag/答案**：`DASCTF{M0Y_W1sh_Y0u_LogF1le_Usg32WEM}`（解出，PAIR14 命令回显 base64 解码）
- **耗时**：盒内约 15 分钟（选题约 7 分钟，解题约 15 分钟，学习约 4 分钟）；引擎 auto 3.8s NOT SOLVED（16 attempts，无哥斯拉检测器）→ 模型补位
- **标签**：misc-流量分析 / 哥斯拉Godzilla-XOR变体 / 密钥爆破 / gzip内层 / 偏移(i+1)%16 / SLL2-pcap / 字典植入密码 / 多假设oracle-sweep

## 链

纯标准库解析 pcap（SLL2：IP 头在记录偏移 20）→ TCP 重组提 16 组 POST/响应（`Antsword=<b64>`，响应=`16hex头+b64+16hex尾`）→ REQ11 自带 100 词密码字典（含植入项 Antsw0rd）→ 标准哥斯拉 md5 链假设失败（head/tail≠md5(key)）→ 字典全 sweep（候选×key 派生 md5hex16/hex32/raw16/原文×偏移 0/1×oracle gzip头/serialize/inflate）→ **Antsw0rd 命中**：key=md5(pw)[:16]、XOR 偏移 (i+1)%16、内层 **gzip**（非标准 b64+XOR，1f8b 头铁证）→ 16 组全解（whoami/pwd/find flag/ifconfig/echo…）→ PAIR12 提示 "try them one by one"、PAIR14 回显 `REFTQ1RG...==` → base64 解码得 flag。

## 对照题解

博客园 Alexander17《楚慧杯Misc—复现》：追 TCP 流→找字典→保存字典用工具"一把梭"爆破→base64 解密，路径与我一致；我方差异：①纯标准库脚本可复放（工具依赖为零）；②识别两处自定义变体——响应头尾 `e71f50e9773b23f9`/`792dea3a7ae385ca` 并非标准哥斯拉的 md5(key) 两半（md5(key)=8991e6… 不符）、内层是 gzip 而非标准 PHP_XOR_BASE64 的纯 b64；无漏点，官方"密钥爆破"意图（字典逐个试）已完整复现且更细。

## 坑

①约束求解被 REQ11 字典报文污染（含 `\r\n` 非 b64），全位置空集——非协议层候选集须先过滤异形报文；②标准哥斯拉格式两次假设失败（md5 链验证不中、b64 字符集约束空集）后才转向 oracle sweep，应变体意识应更早；③linktype 276=SLL2 罕见，按记录首字节经验判断（ethertype 在偏移 0）比查表快。

## 迁移

哥斯拉类流量先验结构 `param=b64(xor(gzip(payload), md5(pw)[:16]), 偏移(i+1)%16)`；密码不确定时把 字典候选×key派生×偏移×压缩oracle 全叉乘一次跑，gzip 头/`a:2:{`serialize/inflate 成功三 oracle 任一命中即锁定；流量内自带字典=出题人留的爆破通道，优先扫非经典词项（本题 Antsw0rd 混在 top100 词典中）。

## 一得

**多假设 oracle sweep 优于单假设串行验证**：加密变体不明时，一次全叉乘 sweep（109 候选×6 派生×2 偏移×5 oracle）秒级收敛，比逐假设推演省一半时间；`1f 8b 08 00 00 00` gzip 头是最强判定 oracle。固化候选：「哥斯拉流量解密检测器」——信号=POST 参数值纯 b64+响应体 16hex 头尾包裹+多密文共享前缀；判定=内嵌字典 sweep+gzip oracle。

## 失败原因

无（解出）。中间弯路：标准哥斯拉 md5 链与 b64 字符集假设各耗约 3 分钟；REQ11 污染排查约 1 分钟。

> AI生成
