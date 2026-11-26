# web 技能卡

## 决策顺序
1. 有源码（.php/.py/.sql）先静态读：定位变量拼接点与过滤函数。
2. 有 .sql 文件 = 大概率 SQL 注入（union / 报错 / 时间盲注）。
3. 无网环境（默认）只做"代码审读 + 本地 payload 构造"，输出 payload 与利用步骤。

## 常见打法
- SQL 注入：union select + information_schema 模板；宽字节 `%bf%27`；
  updatexml 报错；`sqlmap` 离线不可用时手写注入脚本。
- SSTI：`{{7*7}}` 回显 49 → jinja2；`<%= 7*7 %>` → erb。
- 反序列化：PHP __wakeup / GC 序列化、Python pickle payload、Java Commons。
- SSRF：gopher/dict/file 协议；题目内网地址通常在题面。
- 文件上传：`\x00` 截断、双后缀、.htaccess 覆盖。
- XSLeak / 缓存投毒：带回调特征的关键头。
- JWT：`alg:none`、密钥爆破、kid 注入。
- 前端题：先看 JS 里的验证函数与隐藏字段。

## 输出要求
每步先打印可验证的中间结果（回显长度、状态码），再继续；注入成功判据 = flag 正则。
