# ark 操作边界与进阶用法

以下记录已核对的 CLI 行为与限制。不同版本可能变化，使用时结合 `ark help`、当前版本文档和实际执行结果确认。

## 附加参数与调试

`ark` 不是 VM 参数的透明转发器：编译阶段只处理实现列出的开关，其余参数可能静默忽略。需要自定义 VM / 编译器参数时，写入项目 `ark.toml` 对应阶段，并核对实际启动参数，例如：

```toml
[compile.jit]
args = ["--compiler-enable-jit-fort=true"]
```

使用前通过目标版本 VM 的帮助或源码确认参数存在。保留已有 TOML 内容，编辑既有表而不是重复追加同名表。

- `[compile.all].args` 当前用于 pgo/aot/asm/jit/ctx，不用于 abc；es2abc 参数放 `[compile.abc].args`。
- `--gdb` / `--lldb` 适用于 pgo/aot/asm/jit/profdump/stub/ctx；abc 不会启动调试器。
- `ark t.js aot --log` 在不加调试器时将输出保存至 `t.aot.log`。
- `ark t.js abc --dasm` 输出 `t.pa`；`--out` 保存 `t.abc.log`。两者同时存在时优先 `--dasm`。
- `--dp` 用于 aot/asm 的反优化跟踪；`--opt-code` / `--runtime-stat` 用于 aot/asm。
- `-bc` 会覆盖 aot/asm 的配置参数，不是单纯追加日志选项。
- `--pure` 让 AOT 不使用 PGO profile；不等于为 `all` 跳过 PGO 阶段。当前帮助里虽有 `--no-pgo`，编译实现未处理它，不依赖此开关。
- `ARK_USE_ULIMIT=1` 会通过 Bash 将 VM 栈设为 8192 KB；只在确有需要时设置，单独一条栈大小警告不代表执行失败。

`ark run` 的顶层解析器会分类文件、目录和 flags，不能保证原样传递任意 argv。对带 `--flags`、脚本路径等复杂参数的外部命令，先核对实际传给子进程的参数；必要时在同一个 shell 中执行 `ark export` 输出的环境设置后直接调用工具。eval 前先检查输出，仅对可信且路径无特殊字符的生成内容使用；复杂路径用明确的带引号的环境变量设置。

## ARM64 / QEMU

- 完整 ARM64 环境用 `OHOS_PLAT=arm64`。正常 pgo/aot/asm/jit 经 `qemu-aarch64` 运行，并使用 `$OHOS_PATH/out/arm64.<mode>/common/common/libc` 的 musl。
- `--arm64` 只指定 AOT 的目标 triple，不等于切换整套 OHOS 环境。
- 先检查 ARM64 产物、musl 和 `qemu-aarch64`，再运行；不要仅凭设置平台就宣称可执行。
- standalone 的 `ark build arksteed` 在 arm64 下保留 QEMU 构建参数。`ark build musl` 只支持 standalone，仍沿用当前平台，不自动切成 arm64。
- `--rgdb` 当前在 pgo/asm 中启动 QEMU 并监听 1234，等待调试器；jit 分支未实现该开关。`--cgdb` 在 pgo/asm/jit 中启动 `gdb-multiarch ark_js_vm`，不自动附带阶段参数。
- `--gdb` / `--lldb` 分支会替代普通 QEMU 前缀；ARM64 调试不能照搬本机 x64 用法。

需要在 SSH 远端构建或运行时，先确认目标主机及目录；若已安装 `ets-runtime-remote-build`，可参考其环境与源码对齐流程，并核对其主机配置是否适用。

## 日志与符号定位

| 目标 | 命令 / 边界 |
|---|---|
| 搜工具日志 | `ark log rg error failed`，多个 pattern 按 OR 匹配 |
| 持续跟踪 | `ark log` 会一直等待；agent 一次性排查优先直接读取日志或搜索 |
| test262 失败 | `ark rg 262 [pattern]`；无 pattern 时还会写归档 |
| 单测失败 | `ark rg ut`，还会写归档 |
| 回归测试失败 | `ark rg rt` |
| 地址转符号 | `ark addr 0x1234`，当前固定用 `out/<plat>.release/lib.unstripped/arkcompiler/ets_runtime/libark_jsruntime.so` |

`ark.log` 和失败归档位于运行中二进制的目录，不一定是当前工作目录。测试日志默认从 `$OHOS_PATH/out/<plat>.<mode>/` 读取，先确认实际构建输出布局。

地址定位时必须确认库与崩溃二进制匹配；debug 崩溃不能直接套用 `ark addr` 的 release 库。

`ark 262 aot|asm [file]` 当前使用硬编码 `out/rk3568/clang_x64` 或 `clang_arm64` 布局。注意 `.js` 文件参数可能未转给底层测试命令；不能仅凭命令行中写了文件名，就认定只会运行该用例。需要单用例时优先核实原生测试脚本用法，避免误跑整套。standalone 单测同样使用其原生测试入口，不把 full 命令语义照搬过去。

## 仓库操作

仅在用户要求初始化或同步代码时使用；先检查目标目录及本地改动，说明受影响范围。

- `ark repo init`：默认 standalone manifest，完成后自动同步；`--full` 使用 OpenHarmony manifest。
- `ark sync` / `ark repo sync`：执行 repo 同步。
- `ark sync force` / `ark repo sync force`：追加 `repo sync --force-sync`，不执行额外清理；force 仍可能影响已有 checkout，先确认本地工作安全。使用位置参数 `force`。
- `ark init remote`：修改**当前 Git 仓库**，不是 `OHOS_PATH` 指向的仓库；预设添加 `arksteed`、`arksteed-like`、`like`，具体 URL 查当前版本的 CLI 文档。先确认用户需要这些预设 remote。已存在 URL 不匹配时停止，不自动覆盖；它不联网验证权限。

## 清理

清理前根据当前目录和 `ark env` 的解析结果计算完整目标路径，并告知用户删除范围；只执行明确请求的范围。不能为了让构建通过就扩大清理。

| 命令 | 实际删除范围 |
|---|---|
| `ark t.js rm` | 仅 `t.ap`、`t.ai`、`t.an`，不删 `t.abc` |
| `ark rm` | 当前目录下所有 `.ap/.ai/.an` |
| `ark rm log` | 当前 plat/mode 输出目录的 `unittest.log`、`test262.log`，不是 `ark.log` |
| `ark rm obj` | `$OHOS_PATH/out/<plat>.<mode>/obj` |
| `ark rm out` | 整个 `$OHOS_PATH/out/<plat>.<mode>` |

清理只针对一个用例时优先显式文件命令，不使用全目录清理。
