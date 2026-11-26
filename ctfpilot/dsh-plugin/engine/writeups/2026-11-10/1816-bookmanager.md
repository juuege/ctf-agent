# CTF 20min 轮次报告：bookmanager

- 时间：2026-11-10 18:16（北京时间）｜题库：CTF-Archives/2024-CCB-CISCN-Quals
- 题目：BookManager（WEB，500 分，赛事当场 **0 人解出**压轴题）
- 结果：**NOT SOLVED**（无靶机实例，且完整利用链 sink 未锁定；本题无公开题解可学）
- 耗时：约 22 分钟（超出 15 分钟盒，收尾）；快通道 solve --mode auto --write-trace 73.1s / 12 attempts NOT SOLVED（无 hessian/Java 检测器，预期内）

## 题目结构（已完全还原）
- Solon 2.5.12 国产框架 + solon-rpc，jar 内置 fastjson 1.2.83、hessian-lite、snakeyaml、okhttp、snack3
- 入口：`POST` + `Content-Type: application/hessian` → `HessianActionExecutor.changeBody` → `Hessian2Input.readObject()`，任意类反序列化
- 防护：jar 根 `DENY_CLASS` 前缀黑名单（dubbo 同款），`ClassFactory` 校验"类+接口+全部父类"，命中降级为 HashMap
- 黑名单封死全部已知 hessian 链入口：javax.swing.（SwingLazyValue/UIDefaults/AbstractAction）、javax.naming.（全部 JNDI）、javax.management.（BadAttributeValueExpException）、com.sun.org.apache.xalan.internal.（TemplatesImpl）、javax.xml.transform.（Templates 接口，连带杀掉"代理 Templates.getOutputProperties"技巧）、com.sun.rowset.、commons-collections/spring/groovy functors 等

## 已验证攻击面（黑名单×classpath 交集审计）
- `com.alibaba.fastjson.JSONObject` **implements InvocationHandler**（0 参方法→map.get(name)，1 参→map.put）——行为型组件不受"类名黑名单"约束
- `JSONArray/JSONObject.toString()` → JSON.toJSONString 遍历元素 getter
- `com.sun.org.apache.xpath.internal.objects.XString`（equal→other.toString()，未被黑名单覆盖，注意与被封的 `org.apache.xpath.` 是不同前缀）
- 触发器可行性：hessian HashMap put 哈希碰撞 → XString.equals(Proxy) → proxy.hashCode/toString 由 JSONObject(map) 完全可控
- 未封待挖：org.noear.*、org.yaml.snakeyaml（仅 tokens.）、java.lang.reflect.*、sun.reflect.misc.MethodUtil、PriorityQueue

## 未完成部分与下一步
- 最终 RCE sink 未锁定：TemplatesImpl 与 javax.xml.transform.Templates 双封，L3HCTF tellmewhy 的代理链无法直接移植
- 候选方向：①2026-07 披露的"fastjson ≤1.2.83 无 gadget RCE"（POC 未公开，仅公告）②Whwlsfb 的 hessian JDK Method 原生链 ③solon/snakeyaml/okhttp 库内危险 getter 扫描
- 本题公开题解检索已完成（赛事 README、A1natas/Yuki 题解仓库、GitHub 搜索、online_search、SOFARPC/L3HCTF 同族文章），确认**无公开解法**

## 七维标签
web / java-deser / hessian / solon / fastjson-1.2.83 / blacklist-bypass / gadget-hunt / classpath-audit

## 每题一得
反序列化黑名单题先做"**黑名单 × classpath 交集审计**"：黑名单拦的是"被反序列化的类名"，拦不住 fastjson 这种"行为型"组件（JSONObject 当 InvocationHandler）；另：GitHub contents API 下载 >1MB 附件必须加 `Accept: application/vnd.github.raw` 头且需重试（IncompleteRead）。

## 失败路径记录
1. zip 经 contents API 默认 JSON 无 body（>1MB）→ 0 字节；改 raw accept 头后仍 IncompleteRead 一次 → 重试成功
2. 快通道无 Java/hessian 检测器 → 直接转模型探索通道（预期）
3. engine `learn-writeup` triage 二次误判（java 题→pwn/ROP），已登记固化候选；recall 按正文关键词仍可命中

## 学习闭环
writeup 已 learn-writeup 入库（bookmanager_2024CCB）；经验要点含 XString 前缀差异、fastjson InvocationHandler 行为、hessian 触发器无 table 伪造的替代方案（Proxy.hashCode 可控）、变体预测

> AI生成
