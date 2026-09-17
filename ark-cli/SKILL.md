---
name: ark-cli
description: 使用 ark CLI 配置 OpenHarmony 开发环境、构建 ETS Runtime、编译和运行 JS/TS、执行 PGO/AOT/JIT、调试及查看日志。用户提到 ark 命令、ark.toml，或要求用 ark 构建、运行用例、切换环境、同步仓库、清理产物时使用。ArkSteed 编译器内部开发另配合 arksteed；SSH 远端构建另配合 ets-runtime-remote-build。
---

# ark-cli

使用当前环境中的 `ark` CLI 完成任务，不依赖工具源码仓库或特定安装方式。

## 1. 确认工具和目标环境

在用户项目或用例目录运行只读检查；当前目录决定查找哪个 `ark.toml`，保持在任务目录调用工具：

```bash
command -v ark
ark help
ark env
ark
```

完成条件：明确二进制位置、OHOS 源码根目录、仓库类型、平台、构建模式，以及输入文件。用户未指定的值可以沿用现有配置，但执行前说明；若指向错误目录或缺少必需输入，先解决再运行。

- 用 `ark help` 和当前版本的 CLI 文档核对命令；文档与行为不一致时，以实际执行结果为准，并说明差异。
- PATH 中缺少 `ark` 时，向用户确认可执行文件位置或安装方式；已知绝对路径时可直接调用。不要假定存在源码 checkout，也不要自行选择安装渠道。
- OHOS 的 es2abc、VM、GN/Ninja 等由开发环境提供；安装了 `ark` 不代表这些工具链已就绪。

## 2. 固定环境，避免隐式切换

配置查找：当前目录 → 父目录 → `~/.config/ark/config.toml`。值的优先级：**shell 的 OHOS_* > ark.toml > 内置默认**。用 `ark env` 的来源标签核对，不能只看配置文件。

Agent 的多次 shell 调用通常不共享环境。优先在同一次调用内显式设置环境并执行任务，例如下例中的路径、平台和模式要替换为已确认的值：

```bash
export OHOS_PATH="/path/to/ohos-checkout"
export OHOS_REPO=standalone
export OHOS_PLAT=x64
export OHOS_MODE=debug
ark env
ark t.js all
```

直接设置 `OHOS_PATH` 时使用绝对路径。需要跨调用沿用时，每次显式设置，或在用户授权下修改项目配置。

用户需要交互式切换时：

```bash
eval "$(ark init)"
ark env mode release
ark env plat arm64
ark env repo standalone
ark env path a1
ark env
```

- `ark init` 输出 shell wrapper，不设置 OHOS_*；`ark init env` 才补默认环境变量，可能让 shell 值盖过 TOML。
- 直接调用二进制的 `ark env mode/path/...` 只打印 shell 语句。wrapper 才会让它们在当前 shell 生效；自动化优先用显式 `export`，不要假定打印即切换成功。
- 使用明确的 mode/plat/repo 值，避免无参数循环切换。路径带空格或特殊字符时用带引号的 `export OHOS_PATH="..."`。
- `ark init config [path]` 在**当前目录**创建 `ark.toml`，拒绝覆盖已有文件；`path` 是配置里的 OHOS 路径，不是配置输出目录。它使用内置默认值，不复制当前 shell 环境。
- 需要由 TOML 控制时，只在当前任务 shell 中移除相应 OHOS_* 覆盖，再检查来源；不要擅自改用户的 shell 启动文件。

## 3. 选择最小命令

### 编译与运行

以下使用已有的 `t.js` 作示例。显式传文件可避免意外执行 TOML 的默认 `file`。

| 目标 | 命令 | 前置条件 / 结果 |
|---|---|---|
| 编译字节码 | `ark t.js abc` | 生成 `t.abc` |
| 编译后解释执行 | `ark t.js all` | `abc → asm`，默认汇编解释器 |
| 编译后用 C++ 解释器执行 | `ark t.js all -c` | 不执行 PGO/AOT |
| JIT 执行 | `ark t.js abc && ark t.js jit` | `jit` 本身不先编译字节码 |
| 完整 AOT 流水线 | `ark t.js all --aot` | `abc → pgo → aot → asm`，运行新 AOT 产物 |
| 运行已有 AOT 产物 | `ark t.js asm --aot` | 先确认 `.abc/.an/.ai` 对应当前用例 |
| 单独采集 PGO / 编译 AOT | `ark t.js pgo` / `ark t.js aot` | 先有 `.abc`；默认 AOT 还使用 `.ap` |
| 反汇编 / 查看 profile | `ark t.js dasm` / `ark t.js profdump` | 分别读取 `.abc` / `.ap`，输出 `.pa` / `.dp` |

每次调用只指定一个阶段，多阶段用 `&&` 串联或使用 `all`。`all` 不自动删除旧 `.ap/.an/.ai`，普通 `asm` 也不因为它们存在就加载 AOT。`--only` 优先于 `--aot`，不要把冲突开关混在一起。

目录输入会映射为 `@<dir>/fileInfo.txt`，先检查清单存在；与命令同名的目录用 `./build` 这类明确路径。

### 构建

| 目标 | 命令 |
|---|---|
| 当前环境构建 | `ark build` |
| 启用 ArkSteed 构建 | `ark build arksteed` |
| 下载预编译依赖 | `ark build pre` |
| 生成编译数据库 | `ark clangd`（即 `ark build clangd`） |
| full 仓库增量 / 单测目标 / 全量 | `ark build fast` / `ark build ut` / `ark build all` |

standalone 的普通构建调用 `python ark.py <plat>.<mode>`；full 使用 `build.sh`。当前 standalone 分支没有为 `fast/ut/all` 单独选择目标，不能把 `ark build ut` 成功宣称为运行了单测。`[build].args` 只用于 full 类构建。

构建参数优先用完整单 token，例如 `ark build arksteed --gn-args=use_thin_lto=false`；需要分开的参数值时先核对解析器，避免值被丢弃。构建可能把 `compile_commands.json` 复制到调用目录，执行前留意已有文件。

涉及额外 VM 参数、ARM64、调试器、日志、仓库维护或清理时，读取 [references/operations.md](references/operations.md) 的相应章节。只做本地构建不需要先 `ark sync`。

## 4. 验收与报告

检查退出码、实际执行命令和任务要求的产物 / 测试输出。构建成功、进程退出成功、测试断言通过是不同结论；fake 工具链测试只能证明命令编排。

简洁报告：使用的 OHOS 路径及 repo/plat/mode、执行命令、结果和产物或日志位置；失败时给出首个关键错误及尚缺条件。真实工具链不可用时明确“仅核对命令，未完成真实构建/运行”，不要用空文件代替验证。
