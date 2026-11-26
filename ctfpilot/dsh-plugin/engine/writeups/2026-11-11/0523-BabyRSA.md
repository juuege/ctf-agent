# CTF 20分钟循环任务报告：BabyRSA（数信杯2024 西部赛区）

- **仓库/题名**：CTF-Archives/2024-ShuXinCup-sjaq__BabyRSA（2024-ShuXinCup-sjaq 西部赛区，第二届数据安全大赛"数信杯"）
- **选题过程**：2024-CCB-CISCN-Quals 全部 zip 已做（sxmisc 27.9MB 超限跳过）→ 探测组织其他赛事仓库（zjsdxs-Finals/qwbs8/YCB/ISCC/newstar 均无 zip 附件）→ ShuXinCup-sjaq 西部赛区 28 个 zip，取第一道未做题 BabyRSA（1KB）
- **flag**：`flag{3b0ce326141ea4f6b5bf2f37efbd1b42}`
- **结果**：SOLVED（模型探索通道）
- **总耗时**：约 5 分钟（时间盒 15 分钟内）；快通道 solve 3s NOT SOLVED → 爆破脚本秒解
- **题目**：task.py 给 r(128bit 素数)/e=96001/c，N=r⁴q，q 为 12bit 素数未给
- **解题路径**：快通道 miss → 枚举 2048-4096 素数 q，φ(N)=r³(r-1)(q-1) 求逆解密，q=3347 首选命中，纯标准库实现
- **七维标签**：
  - 题型：crypto（RSA）
  - 载体：python 源码内嵌参数
  - 识别信号：N=已知素数幂×小素数结构 + 注释烟雾弹
  - 核心技术链：小因子枚举 → φ 求逆 → 模幂解密
  - 陷阱类型：注释的 next_prime 素数累加+iroot(p,3) 断言诱导"恢复已删初始素数"错误叙事
  - 可迁移模式：N 含已知素数幂因子→用已知因子部分解密，勿强求完整 N
  - 变体预测：q 增至 40bit+ 爆破失效→只剩部分模数法；m 加长超 r⁴→需爆破 q 补全再 CRT
- **题解学习**（博客园 backlion 官方 WP，学时约 3 分钟）：
  1. 官方更优路径：直接 φ(r⁴)=r⁴-r³ 求逆在 mod r⁴ 解密——因 m(约256bit)<r⁴(512bit)，c^d mod r⁴ 即明文，**q 完全是烟雾弹，无需枚举**（欧拉定理：c^d≡m mod r⁴）
  2. 对照结论：我的爆破路径 O(450 素数)可行但非最优；部分模数法 O(1) 且对 q 任意大小稳健
  3. 漏掉的技术点：部分(半边)模数恢复——RSA 中 N=A·B 且 A 已知时，只要 m<A 就可在 A 上直接解密
- **每题一得**：N=A·B 且 A 含已知素数幂时，c^d mod A = m（m<A 成立即还原），未知因子是烟雾；优先部分模数法，小因子枚举是兜底
- **固化候选**：fastpath crypto 检测器——"N=r^k·q 形态且幂底数已知/可爆破小素数"特征 → 先部分模数求逆再枚举补全；learn-writeup 已落库 1 checkpoint + 1 技能卡
- **失败路径**：无实质失败；快通道对纯数论题 3s 即让位探索通道，符合分工预期
- **下轮候选**：ShuXinCup-sjaq 西部赛区 Backpack（背包加密 LLL）/ 好大的公钥（Boneh-Durfee）/ 消逝的曲线（ECC）

> AI生成
