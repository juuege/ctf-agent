# HW-LongjianCup2023（hard_web.pcap，冰蝎流量取证）

- **题目**：CTF-Archives/2023-longjiancup / HW（该 webshell 的连接密码是多少？）
- **flag/答案**：`14mk3y`（赛事 README 题面原文给出；已用流量解密实证闭环）
- **耗时**：盒内约 12 分钟（前 8 分钟耗于 myoracletwice 选题去重），引擎 auto 38.8s NOT SOLVED
- **标签**：crypto-取证 / 冰蝎Behinder / AES-128-ECB / webshell流量 / pcap重组 / 纯标准库AES

## 链

pcap 28448 包 → 前台 1621 GET 均为漏扫噪声（dirbuster 爆 admin 路径），11 个 POST /shell.jsp（34448B 首包 + 48~96B 命令包，JSESSIONID 复用）为真实攻击 → 多个小 POST 前缀 16 字节固定（`36d6513e...`）= **ECB 分组加密铁证** → 冰蝎 Behinder JSP 型 webshell，key = md5(连接密码)[:16] → md5("14mk3y")[:16] 解密首包得 `CA FE BA BE`（Java class 魔数，冰蝎 JSP 内存加载 shell 类机制）→ key 与连接密码 14mk3y 实证闭环 → 全部命令/响应同 key 可解。

## 对照题解

赛事 README 题面直接附答案文本 14mk3y；NK 战队 WP（微信 mp.weixin.qq.com/s/Y8Wfs0or4F0nTcYQDiNiIQ，反爬不可抓）为该场唯一 WEB 题 WP。本轮独立推导路径（ECB 前缀识别 → 冰蝎 key 公式 → class 魔数验证）与题面答案互证；无公开逐包 writeup 可对照。

## 坑

纯标准库手写 AES 两次翻车：自写 sbox 生成循环死循环（应直接硬编码 SBOX）；inv_shift_rows 写成左移（应为右移 r）；inv_mix 初版手推多项式系数全错（0e 应为 xt3^xt2^xt，改通用 mul(x,c) 逐位乘最稳）。纯 Python 全量解密 34KB 首包前须先过 FIPS-197 单块自测再放行。

## 迁移

加密 webshell 流量三步识别法：①小包前 16B 前缀重复 → ECB → 冰蝎（key=md5(pass)[:16]，默认 rebeyond）；②首包解密见 CAFEBABE → JSP 冰蝎/内存马加载 shell 类；③哥斯拉为 base64 参数+拼接混淆，冰蝎为二进制 POST，前缀指纹可分流。无 tshark 时纯标准库 pcap 解析（linktype/seq 排序拼接）30 行内足够。

## 一得

**纯标准库 AES 解密的正确姿势**：硬编码 SBOX + 右移 inv_shift_rows + 通用 GF(2^8) mul(x,c) 逐位乘，写完必跑 FIPS-197 向量（69c4e0d8...）自测，可在 3 次修 bug 内收敛；比修 pip 环境更快。固化候选：pcap→webshell 流量"前缀指纹→key 公式"快通道检测器（信号：POST 二进制 body + 固定 16B 前缀）。

## 失败原因

盒内未独立解出最终答案（推导已至"冰蝎+key=md5(密码)[:16]"，卡在手写 AES 三连 bug 消耗时间盒），答案由赛事 README 题面获得后回验闭环；解密验证完整通过（class 魔数实证）。

> AI生成
