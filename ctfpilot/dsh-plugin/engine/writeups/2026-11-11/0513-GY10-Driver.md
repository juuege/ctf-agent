# CTF 循环任务报告：GY10-Driver（NOT SOLVED）

- **时间**：2026-11-11 05:13 ｜ **题库**：CTF-Archives/2026-ICSC-ICS-Champ ｜ **分类**：Reverse / Windows 内核驱动
- **结果**：❌ NOT SOLVED ｜ **耗时**：解题约 15 分钟（超盒收尾）+ 题解学习约 4 分钟
- **flag**：未取得

## 题目

第十届工业信息安全技能大赛工控安全锦标赛 Reverse-GY10-Driver。附件 Driver.sys（2.7MB）。README 题意：应用层程序丢失，需分析驱动并自写用户态程序与驱动通信获取 flag。

## 样本特征

| 特征 | 值 | 判定 |
|---|---|---|
| 架构 | x64，subsystem native，imagebase=0x1 | WDF 驱动（导入 WdfLdrQueryInterface） |
| 段表 | .text/.rdata/.data/.pdata/INIT rawsize 全为 0 | 代码数据被搬走 |
| 乱码段 | `.vd!` / `` .J`6 `` / `.6KW`（2.8MB） | 壳特征 |
| 熵 | `.6KW` 段 7.95，明文 API 名散布加密流 | VMProtect 风格虚拟化壳 |
| 入口 | RVA 0x2a67fd 落在加密段内 | 运行时解密 |

## 尝试路径（全部未出 flag）

1. 快通道 solve --mode auto：6.4s，12 attempts，未解（预期，逆向题）。
2. PE 段表侦察：发现乱码段 + rawsize=0 + 高熵大段 → 判虚拟化壳。
3. 单/双字节、3-16 字节周期 XOR 明文攻击 `flag{`：全 miss。
4. 常量 XOR/加法变换、zip/7z/xz/gzip 魔数扫描：仅 2 字节 gzip 巧合，无果。
5. 明文 API 扫描：26 个内核 API 名散布加密段，确认部分加密而非全段加密。

## 题解学习（无公开题解，吸收同族路径）

2026-ICSC 赛事 3 轮检索无公开 writeup。吸收同类驱动题标准路径：
- **HITCON2022 checker**：用户程序打开符号链接 `\\.\xxx` → DeviceIoControl（控制码 0x222080）→ 驱动校验返回。控制码多在 0x222000-0x222400（CTL_CODE FILE_DEVICE_UNKNOWN）。
- **腾讯游戏安全 ShadowGateSys 复盘**：驱动把"移动结果"反馈回用户态，注意区分正常返回与隐藏泄露通道。
- 本题正确路径（静态推导）：还原壳 → 定位 IRP_MJ_DEVICE_CONTROL dispatch 或 WDF EvtIoDeviceControl → 提取 IOCTL code 与输入校验 → 写用户态客户端通信拿 flag。

## 失败原因

1. **盒超**：VMProtect 虚拟化壳静态还原远超 15 分钟盒。
2. **环境限制**：宿主为 OpenStack 云主机，无驱动加载环境（无测试模式/无嵌套虚拟化），动态路线物理不可行——此类题在本环境天然受限。

## 每题一得

乱码段名 + 正常段 rawsize=0 + 单一高熵大段 + 入口落加密段 = 虚拟化壳的 PE 结构级指纹，开题 1 分钟内即可判定，不必烧侦察时间；驱动通信题在无驱动加载环境下开题即判环境限制。

## 固化候选

PE 段表壳指纹检测器（fastpath）：自动判壳并输出 payload 段文件偏移范围 → 下轮评估写入引擎。

## 标签（七维）

reverse / kernel-driver / WDF / VMProtect-virtualization / driver-communication-IOCTL / PE-section-anomaly-fingerprint / environment-blocked

## 下轮候选

GY10-VCL-FW（fw_check 逆向解 firmware.enc，纯静态可做）；GY10-反应釜（梯形图计算，无环境需求）。

> AI生成
