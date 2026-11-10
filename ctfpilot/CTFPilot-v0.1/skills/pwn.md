# pwn 技能卡

## 决策顺序
1. 只拿到 ELF：先 magic/静态信息：`file`、`checksec`（若无环境，读 ELF 头判断 NX/PIE）。
2. 偏好静态路线：找 win 函数 / system("/bin/sh") / 后门地址，直接构造栈布局。
3. 动态利用优先用 pwntools 脚本一次成型，失败则降级为纯 struct 脚本。

## 常见打法
- ret2text：找后门函数地址，溢出偏移用 pattern 或逐字符填法。
- ret2libc：本地 libc 与远端 libc 版本可能不同，先用题面给的 libc。
- ROP：先 `pop rdi; ret`，再 system；32 位下栈传参。
- 格式化字符串：泄漏 stack/canary/libc 基址，`%p` 洪水后按偏移定位。
- 堆题：fastbin/tcache 的 uaf、double free、overlap。
- one_gadget：libc 偏移版需要满足约束条件，逐个试。

## 离线约束
本 agent 运行于 Windows，无法直接运行 ELF：产出（a）利用脚本源码（b）偏移计算过程（c）需要的输入串；若题目需要交互服务，输出完整 exp 留待 WSL/靶机执行。
