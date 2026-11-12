# 20261112-1502 TinyNTRU

- 赛事/仓库: CTF-Archives/2026-WQB-Quals（第二届湾区杯网络安全大赛初赛）
- 题目: TinyNTRU（Crypto）
- 附件: `ctf-20min\2026-WQB-Quals__TinyNTRU\`（challenge.py / ntru.py / public_key.txt / output.txt）
- 结果: SOLVED
- Flag: `flag{27dec48f-f9c7-4bad-9a1b-f7aa77badc5f}`
- 耗时: 约 38 分钟（超出 15 分钟单题时间盒；LLL 本体 8.6s，主要耗时在格基行序与 f 系数集合排错）
- 通道: 引擎 `solve --mode auto` NOT SOLVED（8.3s / 442 attempts，无 NTRU 检测器）→ 模型格攻击补位

## 标签
- 题型: crypto
- 载体/格式: 文本参数 + 三进制多项式密文
- 识别信号: `public_key.txt` 含 N=127 / p=3 / q=12289 / h；源码 f=e0+3F、g=3G
- 核心技术链: NTRU 格 `[qI 0; H I]` → LLL → 取范数²=127 短行 (g,f) → 中心提升 mod 3 → 三进制块解码
- 陷阱类型: 格基行序敏感；f=e0+3F 而非整体 1+3F；循环矩阵方向 H_i[j]=h[(j-i) mod N]
- 可迁移模式: NTRU 短向量攻击优先试 `[qI 0; H I]` 行序，扫范数²最小的稀疏行
- 变体预测: 换 N/q/权重、加 padding、换 p；增大参数后需 BKZ

## 一得
LLL 对**基的行排列**极敏感：同一 NTRU 格，`[I H; 0 qI]` 行序 LLL 最短范数² 为 13811、BKZ-30 为 6391，均取不到目标；改为 `[qI 0; H I]` 后 8.6 秒即出现范数²=127 的 (g,f) 行。另确认 f=e0+3F（系数 1、±3），不是 1+3F（4、-2）。

## 题解学习要点
- 赛事 README 仅题面与附件链接，无解法链接；在线检索“2026 湾区杯 TinyNTRU writeup / NTRU 格攻击”无公开题解（闭门赛），以自证与通用 NTRU 格攻击对照为准。
- 通用路径: h=f^{-1}g mod q，构造格 `{(a,b): a*h ≡ b mod q}`，短向量 (f,g) 范数²=1+7·9+7·9=127，LLL 取出后按 `center_lift(f*c) mod 3` 解密。
- 排错记录: ①`[I H; 0 qI]` 行序 miss；②误判 f 系数为 1+3F（4/-2）导致扫描漏检；③修正系数后仍 miss，换行序 `[qI 0; H I]` 命中。

## 失败原因 / 边界
- 引擎无 NTRU/格攻击检测器，auto 通道必然 NOT SOLVED。
- 本机 Windows Python 无 fpylll 轮子（源码编译缺 MSVC）；改用 WSL Ubuntu 24.04 安装 fpylll 0.6.4 + cysignals 解决。
- 未执行附件中任何可执行文件；仅静态读取源码与文本参数。

## 产出
- `ctf-20min\2026-WQB-Quals__TinyNTRU\solve_final.py`（fpylll LLL 解题脚本）
- `ctf-20min\2026-WQB-Quals__TinyNTRU\writeup_TinyNTRU.md`（已 learn-writeup 入库）
- 本报告

## 下轮候选
- 2026-WQB-Quals 其他静态题（FractalTrace / committed / SilentWeights）
- 2026-NCO-Quals web 类剩余题

> AI生成
