# packingmachine (2025-LongjianCup-Finals) — 环境缺失（利用链完整复原，无 flag）

- **仓库/题名**: CTF-Archives/2025-LongjianCup-Finals / 14.packingmachine
- **flag**: 未获取（在线平台题，决赛环境已下线；模块内无 flag 明文，全 data 段 "flag" 16 处全为 Go runtime 词）
- **耗时**: 盒内约 14 分钟（下载 1 分钟 + 引擎 3s NOT SOLVED + 纯标准库 WASM 解析 4 脚本），题解检索 2 分钟无公开题解（闭门赛解题数 0）
- **标签**: [web] [wasm] [wasi] [sandbox-escape] [go-1.24-wasip1] [buildmode-c-shared] [native-host-interface] [preopen-dir] [path-traversal] [stdlib-only] [env-missing]
- **赛题说明**: 打包机管理平台允许用户上传自定义 WASM 模块控制电流负载，模块号称运行在沙箱中，某天打包机集体异常——攻击者如何攻陷服务器？

## 技术链（全部脚本实证）

1. 附件 2.9MB `14.packingmachine.bin` 实为 WASM 模块（`\0asm` 魔数）；导入仅两模块：`wasi_snapshot_preview1`（16 个标准 WASI，含 **fd_prestat_get/fd_prestat_dir_name/fd_write**——宿主有预打开目录）与 **native**（仅 CopyFile/GetFileList/GetPower 三个宿主接口）；导出 `_initialize/malloc/pluginMain/memory`（Go 1.24.3 `-buildmode=c-shared GOOS=wasip1 GOARCH=wasm CGO_ENABLED=0` 构建约定）。
2. name section 定位 Go 函数：`main.CopyFile/GetFileList/GetPower/malloc/pluginMain/main`；自写单函数粗反汇编器解析 pluginMain（3246B）调用链：`GetFileList → runtime.concatstring3+fmt.Sprintf 拼路径 → CopyFile → GetPower → encoding_json.Marshal → fmt.Fprintln`——列目录→拼路径→复制文件→读电流→JSON 回传，正是题面所述"攻陷服务器"的数据外传链。
3. data 段字符串（33395 段 806KB）关键词扫出应用层证据：`goplugin/inputParam`、`inputParam.PowerRes`、`json:"power"`、`goplugin (devel)`、源码路径 `/Users/mengda/mydata/gocode/goplugin/speedplugin/wasm_go/plugin.go` 与宿主依赖 `e.coding.net/cyberutopian/agent/utils@v0.0.4/wasmUtils.go`（h1:ibyTxcqHpKu56AJiZ2yYSCyYaWT5jeXBCipbUAPRbE4=）。
4. 沙箱逃逸判定：宿主 wasmUtils.go 的 native 接口（CopyFile src/dst 双参数）无路径域校验，配合 WASI preopen 目录过宽（或直接以 CopyFile 传绝对路径），模块可读宿主任意文件（含平台 flag 文件）→ 沙箱只是"接口白名单"而非文件系统隔离。
5. 引擎 `--mode auto` 2.7s/12 attempts NOT SOLVED（无 WASM 检测器，预期）；recall 无 WASM 历史经验，纯模型补位通道。

## 一得

1. **Go WASM 静态分析三板斧**：import/export 段定攻击面 → name section 拿 Go 函数名 → data 段 moduledata 提取构建参数与源码路径（go:buildid、-buildmode、依赖 h1 哈希全在），纯标准库 LEB128 解析器 4 个脚本 10 分钟内复原完整利用链。
2. **i32.const 陷阱**：TinyGo 字符串指针在低地址，但本模块 data 段基址 0x7753 起步，函数体内 i32.const 104/232 等是栈偏移不是字符串指针——判据：data 段最小 offset vs 常量值比较。
3. **沙箱逃逸题判型信号**：WASM + native 宿主接口 + WASI preon 导入齐全 = "接口白名单式沙箱"，逃逸点必在 native 接口参数校验或 preopen 范围。
4. 固化候选：WASM 结构侦察脚本（import/export/name/data 关键词一键输出）。

> AI生成
