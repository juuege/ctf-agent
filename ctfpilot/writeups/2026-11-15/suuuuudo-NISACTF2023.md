# suuuuudo writeup（NISACTF 2023，部署审计/sudo 提权）
- 题型: misc（部署审计，docker-compose 配置包）
- 题面特征: 纯配置仓库无二进制（Dockerfile+sudoers+sshd_config+entrypoint 共 9KB），sudoers 含 `ctf ALL=(ALL) NOPASSWD: /usr/bin/base64`，entrypoint 写 flag 后 `chmod 720`（root 属主）
- 解题路径: 读 sudoers 找单命令授权 → Cmnd 无参数限定=任意参数可用 → `sudo base64 /home/ctf/flag | base64 -d`（root 任意文件读原语）→ flag
- 有效技术: 受限 sudo 白名单命令滥用（GTFOBins base64=sudo 文件读条目）；前提=flag root:root 720 使 ctf 无读权、sshd 口令登录 ctf:ctf 是入口（端口 9999→22）
- 失败路径: 引擎 auto 0.2s NOT SOLVED（无 docker 部署审计检测器，raw-echo 门控正确拦截 compose 明文 flag）；无被否决假设
- 适用限制: 归档静态 flag 取自 docker-compose GZCTF_FLAG（与 capabilities 仓同值，部署版动态注入）；未连在线靶机，利用链 WSL1 三重复放验证（visudo -cf OK、正例读出伪 flag、直接读 EPERM、sudo cat 被拒）
- 未测试: 真实 docker 在线部署；sudoers 参数黑名单/通配变体；base64 -d 覆写文件原语
- 标签: 识别信号=sudoers NOPASSWD 单命令白名单+flag 720 root 属主+ssh 服务暴露；陷阱类型=白名单命令看似无害（base64 不可执行命令但等价任意读）；可迁移模式=sudoers 审计先对照 GTFOBins 原语表，Cmnd 无参数限定即任意参数滥用；变体预测=授权 less/man/awk/vi/xxd 同型读原语或 shell 逃逸，NOPASSWD 任意写命令可覆写 /etc/sudoers 提权
- 可固化性: 是；特征检测条件=目录含 docker-compose.yml 且 sudoers 存在 NOPASSWD 单命令授权；判定逻辑=解析授权命令对照 GTFOBins 读/写原语表命中即输出利用链（fixations 频次 2 未达 ≥3，暂只落库不固化）

> AI生成
