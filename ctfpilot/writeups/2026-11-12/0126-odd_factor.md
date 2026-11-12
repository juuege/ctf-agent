# CTF 20min 循环任务 20261112-0126 odd_factor SOLVED

## 结果
- 题目: CTF-Archives/2023-zjsdxs/Crypto-odd_factor（2023 第六届浙江省大学生网络与信息安全竞赛决赛）
- FLAG: `DASCTF{b4920de4-9b76-9222-1bc4-ae05a6714ce6}`
- 耗时: 解题净约 3 分钟（模型探索通道，纯标准库 10 行脚本）；全轮约 20 分钟（选题占约 10 分钟）
- done.json: 88 → 89 条

## 选题过程
- CCB 18 题、NCO crypto/forensics/reverse 均穷尽；列 NCO web 目录时 api.github.com 限流 403 → 按既定方案降级 jsDelivr（data API 拉文件树可用）
- NCO web 4 题全为 DynamicContainer 服务端 flag，离线不可取真 flag，按既定规则跳过：
  - 个性签名生成器: Jinja2 SSTI（render_template_string 直拼 + 11 词黑名单子串匹配），payload 思路已产出（字符串拼接/attr 过滤器绕过，读 /tmp/flag.txt），真实 flag 在 GZCTF_FLAG 环境变量
  - 超级无敌保险箱: flag 在 HTML 注释但为占位符 `NCO26{xxxxxxxxx}`（运行时注入），前端禁右键/F12 全是烟雾
- 转 2023-zjsdxs 仓库（仅做过 0_1_Game），第一道未做题 = Crypto-odd_factor（附件 1.4KB）

## 解法
- 结构: n = (p·q·r·s·t)·(p+q+r+s+t)；real_p=2558bit 五素数积、real_q=514bit 素数（和）、e=65537，源码含 assert size(m)<360
- 关键推导: real_q|n 且为素数且 m<real_q → c ≡ m^e (mod real_q)，故 m = c^(e^-1 mod (real_q-1)) mod real_q，**无需分解 real_p**
- 验证: 解出 m 351bit<360 与 assert 吻合，解码得整洁 DASCTF{uuid}；五素数积为纯烟雾弹
- 脚本: `.temp\odd_factor_solve.py`（纯标准库，pow 三行核心）

## 题解学习要点
- 该题无公开题解: 2 轮在线检索无命中；README 官方题解链接中先知文章仅覆盖 Misc 篇（Xcode v5.8=XXencode 链、Ez_Signin=伪加密排除+末尾 b64 线索 5 位数字爆破+按键精灵画图、NewGrating=冰蝎 3 流量密钥 e46023a69f8db309 解密+光栅图列采样爆密码），公众号 Crypto 篇文章已删除
- 已解出无对照必要；自证路径为最优（跳过全部分解难题）

## 标签与一得
- 标签: crypto/RSA变形；识别信号=assert size(m)<位数上限 + 素因子单独给出；陷阱类型=多素数积诱导强行分解；可迁移模式=先比较明文位长与已知素因子位长，明文小则免分解单因子解密
- 一得: RSA 变体题先做"明文位长 vs 已给素因子"检查表；固化候选升级为「素因子上界解密检测器」（源码含 assert size(m)<N 且输出含素因子 B → B 位数>N 即单因子解密）
- 附带情报: NCO web 4 题 + pwn 4 题均 DynamicContainer，整个 NCO-Quals 仓库判定穷尽，后续不再扫描

## 失败原因
- 无（一轮解出）。损耗仅在选题段: NCO web 两题离线分析 + 限流降级约 10 分钟

## 下轮候选
- 2023-zjsdxs 剩余 9 题: Crypto-R_r(1KB)、Misc-Ez_Signin(43KB)、Misc-NewGrating(2.5MB，已有官方 Misc 篇题解可对照)、Misc-Xcode_v5.8、Reverse-Ez8or/ezRE/ida_pro/apkre、Web-ezWEB(19MB jar)
- 2023-Bluehat-Quals 瓷砖计算机续攻（TileGX 纯标准库 bundle 解码器计划不变）
- 2023-longjiancup 剩余: HD/HW/SSW/TP/WS 五题

> AI生成
