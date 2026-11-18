# 2024-ShuXinCup-sjaq IDN的绘画：网盘附件哑弹 + punycode IDN 复盘

- 题目：2024-ShuXinCup-sjaq IDN的绘画（南北部赛区，misc；引擎路由误判 web conf=0.50）
- 题型：misc / IDN punycode 编码 + 图片隐写（附件不可达）
- 结果：NOT SOLVED（环境约束：附件本体在百度网盘，无登录通道全线风控，离线物理不可得）
- flag：无（不可验证）
- 载体：仓库仅描述.txt 736B；引用外部 file3.7z（pan.baidu.com/s/1NNcb4QrA6y1y5sF2iTdzTA 提取码 GAME，md5=9c26770d174c682d60ddbe66149f9da2，解压密码 cc42721a3bd4988e6cabea6eb8f622db）
- 识别信号：仓库题目录仅描述.txt 且含「pan.baidu.com/s/ + 提取码 + md5」三件套 → 附件网盘分发型；描述含 `xn--` 前缀 ASCII 串（punycode/IDNA 签名）；题面自带「密码长度为23位」「需根据关键字构造字典」提示；解压密码本身是 32 位 hex（flag 同格式，易混淆但非 flag）
- 核心技术链：引擎 1.1s/24 attempts NOT SOLVED（快通道对 736B 文本跑 chain-decode/caesar/xor-single 全 no-flag）→ 模型通道：`'xn--7nraaa738k9tnba223fba479qe42gda'.encode('ascii').decode('idna')` → `叁柒叁柒比幺零比玖叁零叁`（中文大写数字，「比」= 冒号语义 → 3737:10:9303）→ 网盘无登录四步探测（landing→verify pwd→BDCLND cookie→share/list / wap / rest xpan）verify errno:0 拿 randsk 但 list 全线 errno:2/-7「链接出错」（未登录风控层）→ 原仓库 CTF-Archives/2024-ShuXinCup-sjaq 与全部 2 个 fork（arwin-cc/ShuXinCup2024、jinnywc）git 树 74 条目复核：IDN的绘画 仅描述.txt，无任何附件补传，仓库按东/西/南北部赛区组织且其余题附件均为 `题名_md5.zip` 同仓形态（反证本题附件确实只走网盘）→ md5 与「file3.7z」全网检索无镜像 → 首届数信杯公开 WP（eason612、backlion、Ahisec 等多队）均无此题解 → 定论：附件物理不可达，非技术链断裂
- 有效技术：punycode 一行解码（Python 内建 idna codec，无需外部库）；网盘探测 SOP：share/verify 成功拿到 randsk 后注入 BDCLND cookie 再打 share/list，wap 端点与 rest/2.0/xpan 作备用；fork 网络全树复核（GET .../git/trees/main?recursive=1）找附件补传；附件 md5 指纹全网搜镜像
- 失败原因：附件本体存于百度网盘，无登录 cookie 的程序化下载被风控拦截（verify 通过但 list 报链接出错，属无登录态风控而非链接失效——fork 树反证链接即主办方分发渠道）；附件里「墙上的画」（图片隐写主体）与 23 位字典密码均无法获取，题面信息量不足以恢复 flag
- 可迁移模式：「描述即全部」题面榨取 SOP：①题面密文先逐个编码解码尝试（xn--/base 系/纯文本）；②网盘附件走四步探测但预期无登录 90% 拦；③fork 网络全树复核附件补传；④md5 全网搜镜像；⑤同场比赛 WP 交叉检索。四路全空即按 attachment-unreachable 定论，不再烧深通道
- 陷阱类型：把网盘不可达当知识缺口加检测器是方向错误（物理边界）；百度 verify errno:0 不代表 list 可用（randsk 之外还有登录态风控）；题面给的 32 位 hex 解压密码形态与 flag{32位字符串} 一致，是最强语义红鲱鱼，勿直接提交
- 变体预测：网盘链接换 123pan/蓝奏云/夸克（提取更松，无登录可下）；加密包预分发模式常见于政府/行业赛事附件统一发放平台
- 适用限制：仅判附件不可达；若附件可达（提取宽松的网盘或镜像）则本题核心是 punycode 解码出的数字串驱动字典构造 + 图片密码爆破，本环境无法验证该分支
- 未测试：带百度 BDUSS 登录 cookie 的下载路线（需要账号凭证，超出当前边界）；附件若可达后的 23 位字典爆破分支
- 可固化性：知识卡先行（本题）：punycode IDN 解码信号卡（xn-- 前缀 + ASCII body → decode('idna') 出 CJK 即成证据）+ 网盘哑弹分类卡（描述含网盘链接三件套 + 零附件实体 → attachment-unreachable 提前止损）。引擎层候选 backlog：fastpath 增 punycode 检测器（xn-- 前缀自动解码并比对 flag 正则）、dud 预分类器（netdisk-link 零附件实体 → 跳过 24 次盲扫 attempts 直出结构化 NOT SOLVED 原因）——代码变更按单变量纪律走 rsi 提案 + arena 消融门控 + 人类确认

## 题目

首届数信杯（2024 第二届数据安全大赛）南北部赛区 misc 题「IDN的绘画」：IDN·MRF 出生在 2082 年 3 月，出生唯一 ID 丢失只留 `xn--7nraaa738k9tnba223fba479qe42gda`，提示墙上画作辅助、需按关键字构造字典爆破 23 位密码，flag{32位字符串}。附件 7z 在百度网盘（提取码+md5+解压密码齐全），但网盘无登录下载被风控拦截、仓库与全部 fork 无附件、无镜像、无公开 writeup，离线环境物理不可解，如实计 NOT SOLVED。

## 一得

本题唯一可再生产行为是把「网盘附件探测」变成固定 SOP（四步 API + fork 树 + md5 搜索）并固化 punycode 信号卡：下一道 xn-- 题引擎侧 recall 可直接命中解码路径，模型侧可把网盘类哑弹的确认时间从本轮的十余分钟压到一分钟内。环境边界题的正确姿势是结构化止损而不是穷举检测器。

> AI生成
