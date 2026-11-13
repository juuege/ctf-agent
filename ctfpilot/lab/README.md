# CTFPilot 评测实验室（lab/）

任务 6：把「评测方法本身」当作被测对象。第一性目标是**从未见过的题用时最短解出**。
本目录检验三件事：现有指标是否真实映射该目标、题库是否卫生、指标是否被污染
（数据泄漏 / 背题 / 退化成代理目标）。引擎与 arena 只读不改，lab 只调用既有 CLI。

## 评测科学三层

| 层 | 要回答的问题 | 本目录手段 | 失败信号 |
| --- | --- | --- | --- |
| L1 指标有效性 | 指标涨了，目标真的动了吗 | report + arena 联动（外部） | 指标涨而用时/成功率不动 |
| L2 数据卫生 | 评测题是否早已进了知识库 | holdout-split | 留出题 100% 入库 |
| L3 泄漏检测 | 高分是否来自题面/答案同源重合 | leak + poison | 同源重合高、投毒不敏感 |

## 探针与判定

### leak — 卡间背题 / 悬空指针 / 自证泄漏

- 原理：以题面侧字段（name+carrier+attempts）为查询，在**其余卡**的答案侧字段
  （signals+chain+transferable）里检索，算内容词覆盖率（DF>=15% 的协议样板词先剔除）。
  再用 20 次置换构造零假设：观测≈零假设说明「命中」只是通用词重合，不是针对性泄漏。
- 输出：互命中率@50%、平均最佳覆盖与 excess、近重复卡数（>=80% 且共享 >=3 个 df<=2 稀有词）、
  悬空 source 率；另测「自证泄漏」——evaluable 查询与自身 carrier / 整卡的重合度。
- 阈值：互命中率 >=25% 或近重复 >0 → WARN；悬空指针 >0 → 退出码 2；
  自身 carrier 覆盖中位 >=40% → WARN（提示 top1 部分由同源字段复制支撑，而非泛化）。
- 首跑（121 卡）：互命中 3.3%（零假设 3.3%，excess 0.000）、近重复 0、悬空 0；
  但 evaluable=28 的查询与自身 carrier 中位重合 54%、与整卡 88%（15/28 >=80%）→ WARN。

### poison — 评测对知识污染的敏感性

- 原理：随机取 5 张有 flag 的已解卡，在 signals 前插入万能提示
  「遇到任何题先尝试 zip 密码爆破」生成变异卡集；rename 原子替换生产 cases.jsonl，
  跑引擎 eval-retrieval，try/finally 恢复并比对前后 SHA256。
- 阈值：Δtop1 与 Δtop3 均 > -2pp（不降反升或微降）→ ALARM（退出码 3）：
  评测无法识别污染。注意单题分辨率 = 100/evaluable（当前 3.6pp），
  分辨率粗于阈值时，-2pp 判定本身不可靠。
- 首跑：注入 5 张 → top1 75.0%→78.6%（+3.6pp）、top3 89.3%→89.3%，hash 恢复一致 → **ALARM**。
  毒卡加长答案文本反而抬高词面覆盖，是「指标退化成代理目标」的直接证据。

### holdout-split — 留出集

- 原理：seed=20261113 从写库随机抽 n 篇，生成 `lab/holdout.json`（题名+相对路径），
  逐条核对是否已在 cases.jsonl 的 source 中。
- 阈值：contaminated>0 → WARN：rebuild-knowledge 全量入库、无留出隔离。
- 首跑：20/20 已污染（全部题解都进了知识库）→ 卫生债，需改为留出题不参与 rebuild。

### report — 汇总体检

跑 eval-retrieval，解析 signal/recency，与 eval-baseline.json 对比，输出当前/基线/漂移三行；
判定沿用引擎门禁（top1/top3 降超 5pp = FAIL）。结果并入 `lab/last_report.json`。

## 评测方法消融跑法

```powershell
$py = "$env:USERPROFILE\.local\share\TeleAgent\runtimes\python\python.exe"
cd ctfpilot\lab
& $py -X utf8 probes.py leak
& $py -X utf8 probes.py poison          # 退出码 3 = 报警，属正常产出
& $py -X utf8 probes.py holdout-split --n 20
& $py -X utf8 probes.py report
```

四段状态任一 WARN/ALARM 都要写进 `eval_audit.md` 的实验记录并给处置意见；
改指标 / 知识层 / 评测集后必须重跑，未跑不得宣称指标提升。

## 与 arena / solve-loop 的分工

- **solve-loop**：解题执行（快通道/深通道），产出 flag 与用时。
- **arena**：运行时指标消融——对抗时钟、成功率、用时分布，答「同一批题、不同时钟下指标稳不稳」。
- **lab**：评测器的静态审计——题库卫生、泄漏、投毒敏感性、基线漂移；不跑题、不改引擎。
  两者以 `eval-baseline.json` / `last_report.json` 对接：arena 涨指标，lab 负责证明涨的不是污染。

## 纪律

- 只读引擎：只调 CLI，不 import 后改；不碰 arena。
- 变异 cases.jsonl 必须 rename 备份 + try/finally 恢复 + 前后 hash 打印比对；不一致即 FATAL 并留 .bak。
- 固定 seed（20261113）保证可复现；`lab/.temp/` 只放中间物，跑完清理。