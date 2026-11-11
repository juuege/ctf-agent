# lwewl / LWEWL（2024 CCB×CISCN Quals · Crypto · 400pt · 6 队解出）解题报告

- 日期：2026-11-10 21:23 | 仓库：CTF-Archives/2024-CCB-CISCN-Quals | 引擎：快通道不可用（轨迹库被并行会话锁定），全程模型探索通道
- 结果：**半解**。flag2 = `-8819-856a8fe5ada0`（RLWE 部分完整解出并验证）；flag1 = UUID 前缀 18 字符（xxxxxxxx-xxxx-xxxx），结构确定、值未解
- 完整 flag 形如：`flag{<UUID 前 18 字符>-8819-856a8fe5ada0}`（36 位 UUID），无远程靶机无法提交闭合

## 解题路径
1. **下载**：contents API >1MB 不回内容；blobs API base64 响应在本机网络 ~1.6MB 处截断（IncompleteRead×2）；改 `Accept: application/vnd.github.raw` 直取原始 zip 成功
2. **sobj 解析**（约 6 分钟，本题主耗时）：zlib+pickle 自定义 Unpickler stub——`make_integer`=base32 转 int、`Mod(x,q)`→x%q、`make_element` 取 tuple 内 int-list、`unpickle_v1` 取 entries list；全部数值还原为 _data.json
3. **LWE 部分**：发现出题人 bug——`lwe_encrypt` 返回的 b 是**原始整数未 mod q**（≈2^47）。e·p 在 mod 257 下被吸收 → 612×512 mod 257 高斯消元解得 σ=s mod 257 **唯一**（612 方程 0 不一致，铁证正确）。但解密 flag1 需 s mod q 完整值：第二层 t=s div 257∈[0,4080) 的整数系统 `<a_i,t>+e_i=c_i`，mod 257 后 e 系数为 1 不再被吸收 → 真 LWE(n=512)，超出时间盒
4. **RLWE 部分**：b=M_a·s+e (mod f, mod q)，q 为 128 位随机素数而 s 系数是 flag 字节、e∈[-5,5] → 构造 129 维嵌入格 {(s,e,k): M_a·s+e−k·b≡0 (mod q)}，目标范数 ≈600 远小于 λ1(GH)≈2^66，flint `fmpz_mat.lll()` 后台 6 分钟解出，e-range 恰为 [-5,5]，重算 b≡a·s+e (mod f) 全系数验证通过

## 失败路径（教训）
- M_a 构造时"乘 x"系数表误写 `[1,0]`（=常数 1）→ 全列相同、格退化出一堆范数 2 单位向量；旋转矩阵各列须验证非平凡性
- 用 σ 尝试直接解密 flag1 得乱码：绕回 k≈2^27 巨大，mod 257 信息不足以还原 mod q 值
- 熵密杯同型题（LWE 弱噪声）确认 mod p 消噪是此类题标准第一击

## 题解检索
A1natas WP 站 TLS 错误/登录墙；GitHub 搜 LWEWL/lwewl 0 命中；搜索引擎无公开 wp（6 队解出但无公开题解）。已 learn-writeup 入库（lwewl_2024CCB，DB 锁等待 3 次后成功）。

## 七维标签
crypto / lattice / LWE / RLWE / sage-sobj-parsing / mod-p-noise-cancel / lll-embedding / flint / github-raw-media-type / uuid-flag-structure

## 一得
**LWE 类题第一检查点：save() 时公钥是否取模**。未取模则 mod p（明文模数）直接消噪、线性代数解出 s mod p——本题 σ 到手但第二层 t 被 e 卡住（n=512 真 LWE），若有 612→更多样本或更小 p 或许可再降。RLWE 大模数（2^128）+ 小系数 = 巨大 gap，129 维 LLL 足够，flint 纯离线可行——无需 BKZ。
固化候选：①sobj 解析器脚本 ②"公钥未取模→mod p 消噪"检测规则 ③flint 版 RLWE 嵌入格模板。

## 产物
- `ctf-20min\2024-CCB-CISCN-Quals__lwewl\_data.json`（全部还原数据）
- `.temp\lwewl\{parse_sobj,extract_all,lwe_modp,rlwe_lll}.py`（可复用脚本）
- 耗时：约 30 分钟（超 15 分钟盒；主瓶颈=sobj pickle 结构逆向 6 分钟 + 网络下载重试 3 次 + LLL 后台 6 分钟）

## done.json
已更新：加入 CTF-Archives/2024-CCB-CISCN-Quals/lwewl（防重复做）

> AI生成
