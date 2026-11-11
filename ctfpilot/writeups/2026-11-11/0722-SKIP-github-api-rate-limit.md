# CTF 20min 循环任务 · 跳过记录（20261111-0722）

## 状态
- 本轮未选题、未解题，按边界规则跳过。

## 原因
- `api.github.com` 未认证配额耗尽（core remaining 0/60，HTTP 403 rate limit exceeded），重试 1 次仍失败。
- 重置时间：需再等约 16 分钟（reset=1794354011），超出本轮 15 分钟解题时间盒，等待不经济。
- 降级路径 `github.com` 网页（本地 urllib 30s 超时、平台 webfetch 60s 超时）两次尝试均失败，与记忆"github.com 网页可直连"不符，疑为当前网络波动。

## 已做处理
- 已读 `ctf-20min\done.json`（36 题已做，CCB 仓库 17 题）。
- 已确认 `.temp\list_repo.py` 为本轮临时脚本，无污染。
- `done.json` 未更新（无新完成题目）。

## 下轮建议
- 优先直接用 `api.github.com` contents API 列 `CTF-Archives/2024-CCB-CISCN-Quals/attachments`（届时限流应已重置）；CCB 用完后按规则探测 CTF-Archives 组织其他赛事仓库（2023-ISCC、2023-longjiancup、2024-ShuXinCup-sjaq、2025-LongjianCup-Finals、2026-NCO-Quals、2026-NCO-Final、2026-ICSC-ICS-Champ 已部分使用，其余为候选）。
- 若再遇 403，先查 `/rate_limit`，reset < 5 分钟可等待；否则跳过。

## 元认知一行
API 配额是小时级硬限，连续多轮循环任务会必然打满——固化候选：把 done.json 里 CCB 剩余题名单缓存到本地（上轮已列过一次目录），下轮 API 不可用时可直接从本地缓存选题，把"网络可用性"从选题路径里解耦。

> AI生成
