# 2022-mtgxs-web-easypickle 与 2022-xhlj-web-real_ez_node：归档 web 动态flag容器题哑弹复盘

- 题目：2022-mtgxs-web-easypickle 与 2022-xhlj-web-real_ez_node 归档动态flag容器哑弹复盘（web）
- 题型：web
- 结果：NOT SOLVED
- flag：无
- 载体：easypickle = app.py（pickle 反序列化 web 服务源码）+ Dockerfile + docker-compose.yml + docker-entrypoint.sh + README，端口 8888；real_ez_node = node web 服务源码 14 文件 + 部署件
- 识别信号：三查法。(1)结构查：附件含 web 服务端源码（.py/.js/app 入口）而非纯数据附件；(2)部署查：docker-entrypoint.sh 或 start.sh 含 `echo $FLAG > /flag` 类注入语言且随后 `export FLAG=not_flag` 抹掉环境值，README 写明"动态flag / $FLAG 环境变量 / CTFd"，DockerHub 镜像 randark/* 系列为同族再host；(3)形态查：全附件无可推导 flag 形态。形态查两个变体：变体A（real_ez_node）附件连 flag 形态串都不存在；变体B（easypickle）docker-compose.yml 存在明文模板旗串 flag{uuid} 但属部署模板默认值，原样回显（raw-echo）非推导可得，read+strings 命中后被 require_derived 判 raw-echo 撤回不计解。三查全过即判动态 flag 容器哑弹，真旗只存在于运行时容器，离线必败。
- 核心技术链：triage 判 web → fastpath 全检测器跑一遍留形态查证据（变体B 的 compose 模板旗串被 strings 命中、provenance 撤回并回写 semantic:raw-echo 语义行）→ 部署件逐行核对 $FLAG 注入语言 → 结论 runtime-flag-only 哑弹 → 直接出 NOT SOLVED 定论，不再烧深通道与模型通道预算
- 有效技术：web 哑弹三查（结构查/部署查/形态查）；部署模板旗串与附件藏真旗的区分（原文回显=非解，需推导恢复=可解）；provenance 撤回语义判读；strings 全附件形态扫描
- 失败原因：离线环境无 docker 运行时且远程靶机早已下线，$FLAG 注入的容器内真旗物理不可达；变体B 的 compose 旗串即使照抄也只是部署模板原文回显，引擎反作弊规则按 raw-echo 撤回，任何检测器都救不了
- 可迁移模式：web 归档容器题先做哑弹三查，命中即秒判 NOT SOLVED，省真实墙钟与模型预算（对照 dfjk-Misc 模型通道 1440s/180K token 的燃烧）；对抗时钟把 unsolved 记满 timeout（arena 公式 mean_solved×rate + timeout×(1−rate)），提前止损只省真实时间不改善时钟，唯一真实路径是多解题或解得更快
- 陷阱类型：designer-views 反例——README 同样声称"动态flag"但真旗藏在 index.js 里（js 转义串，推导可得），模型通道解出并 account-model 入账；故形态查是硬前提：只有当 fastpath 的命中全部为 raw-echo 模板值（或零命中）时才可判哑弹；若源码中存在需转义解码、计算或变量拼装才能恢复的旗形态，必须先走推导路线而不是秒判哑弹。另一坑：把 compose 模板旗串当"附件藏旗"照抄上报=作弊回显，必被 provenance 守卫撤回，还会污染基准真相
- 变体预测：变体A=附件零 flag 形态（real_ez_node，引擎 60.7s NOT SOLVED，WAL 后 19.8s）；变体B=部署件含明文模板旗串（easypickle 0.7s/83 次；note 题 compose 的 shadowflag 占位同款）；远程地址写在 README 而非附件内；部分归档可用 docker-compose 本地起靶机（当前环境无 docker 未测）；静态附件型 web 题（无服务源码）不适用本卡
- 适用限制：仅判离线不可解；若附件含真实可推导 flag（转义/拼装/计算形态）、或远程靶机可达、或本地能起容器复现，则不适用；不能外推到纯静态附件题与非容器题
- 未测试：docker-compose 本地起靶机路线；$FLAG 运行时值与附件模板旗串的一致性验证
- 可固化性：web 容器利用属交互族，不可固化为 fastpath 检测器（违反 memory 不等于 capability 边界）；以知识卡固化哑弹三查法；三查中形态查可部分脚本化（fastpath 无命中信号已有，raw-echo 撤回语义行已落库）

## 题目

CTF-Archives 归档拉取 2022 美团高校赛 easypickle（pickle 反序列化 web 题，app.py 加部署三件套；README 声明动态 flag 走 $FLAG 环境变量，entrypoint `echo $FLAG > /flag` 后 `export FLAG=not_flag`；compose 里的明文模板旗串被引擎 read+strings 命中、provenance 判 raw-echo 撤回，0.7s/83 次诚实 NOT SOLVED）与 2022 西湖论剑 real_ez_node（node web 服务源码，flag 为 $FLAG 运行时注入容器，附件绝无任何 flag 形态，引擎 NOT SOLVED）。两题同族：动态 flag 容器题，真旗只存在于运行时容器，离线附件物理不可恢复，应按三查法秒判哑弹并如实计 NOT SOLVED。

## 一得

web 归档容器题的哑弹判据可以完全结构化：结构查（服务端源码在附件）、部署查（$FLAG 注入语言）、形态查（无可推导旗形态）。designer-views 反例证明形态查是硬前提——README 的话不能信，附件里是否存在"可推导"的旗形态才是判据，原文回显的部署模板串不算。这是 rsi-024 pwn 哑弹卡的同构迁移，让模型通道把 web 类确认时间从分钟级压到秒级，避免 dfjk 式预算燃烧。

> AI生成
