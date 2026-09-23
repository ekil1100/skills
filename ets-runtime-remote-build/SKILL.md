---
name: ets-runtime-remote-build
description: "开发 OpenHarmony arkcompiler/ets_runtime，准备构建或运行测试时使用，不限本地操作系统或是否使用 WSL，尤其是修改代码后的局部验证、单个 JS/TS 用例、原生 unittest/GTest 和失败后的修复复测；即使用户只要求修复/实现、测试由 agent 自主决定，且未提及远程、SSH 或编译慢，也应在执行前读取。负责在 SSH 主机 work 准备源码、执行选定命令并保存记录（含 ARM64/QEMU 检查）。显式要求 work/远端构建测试时也使用；只读分析、仅修改不测试或明确要求本地执行时不自动启动远端流程。"
---

# ets_runtime 远程构建

本 skill 只教 agent 如何在 `work` 上准备源码、运行已确定的构建/测试命令、保存日志并报告执行状态。不负责组件开发流程、测试用例选择、优化方案或组件成功标准，也不要求读取其他 skill。使用本 skill 的固定脚本，不临时生成同类脚本或绕过安全检查。

## 执行入口

- `ets_runtime` 开发任务进入构建/测试阶段时，不以本地操作系统或是否使用 WSL 作为触发条件；默认将选定命令交给本 skill 在 `work` 执行；局部测试缩小的是用例范围，不代表在本地执行。用户明确指定本地执行时遵循其选择，不启动本远端流程；仅分析或修改、不执行测试时也不启动。
- 命令与配置可来自用户，也可由 agent 根据仓库测试文档或当前开发流程确定，无需用户逐字提供命令。先确定最小必要测试范围，再进入 prepare；只有仍无法确定的参数或环境才澄清，不因为加载本 skill 扩大测试范围。
- 修复后源码又有变化时，重新 prepare → 审核 → apply，再运行选定测试，不能复用上一轮已验证源码状态或直接测试远端旧代码。

## 前提与路径

- 两端需要 Python 3.9+、Git、rsync，远端使用 Bash 登录环境，并提供 `ark` 包装命令；本地需要 SSH。
- 将本 skill 目录下的 `scripts/remote_build.py` 解析成绝对路径，下文用 `$runner` 表示。脚本自动加载同目录的固定远端程序 `remote_ops.py`，通过 SSH 直接执行，不在远端安装或生成程序。
- 用 `git rev-parse --show-toplevel` 获取本地仓库根目录 `$local_root`。它必须是 `$HOME` 下名为 `ets_runtime` 的仓库，路径父级不能是符号链接。
- 远端路径是远端 `$HOME` 加相同相对路径。例如本地 `$HOME/ohos/a0/arkcompiler/ets_runtime` 映射到 `work` 的 `$HOME/ohos/a0/arkcompiler/ets_runtime`。
- 仓库操作期间不要由其他任务修改两端源码。脚本锁只能约束同脚本任务，不能阻止编辑器或其他 Git 命令；检查与写入也不是原子事务。

## 持久运行历史

本工具的远程执行历史保存在 `${XDG_STATE_HOME:-$HOME/.local/state}/ark-runtime/runs/<checkout-id>/<UTC时间>-<短commit>-<随机后缀>/`。`checkout-id` 由仓库目录名和相对家目录路径的哈希组成，区分多个 checkout；同一次远程 session 两端使用相同 run ID，家目录及 XDG 路径分别取各自环境。设置 `XDG_STATE_HOME` 时必须是绝对的持久目录。

- 每次 prepare 新建远程执行会话；每次远端 run 在 session 下新建 `commands/0001/`、`commands/0002/` 等独立记录。重跑不覆盖前一次命令的日志、结果或退出码。
- `run.json` 保存源码/依赖状态、主机、时间、命令、退出码或失败原因；`summary.md` 是查看入口。`logs/` 放启动即落盘的日志，`results/` 放业务原始结果。失败、中断及远端返回丢失也保留记录；`unknown`/未结束状态表示需要核查，不等于通过。
- 远端 session 的操作日志和每条命令日志直接写在 `work` 的持久目录；本地保留调用状态及控制台副本。SSH 断连不能保证任务完成，但已写入的远端日志不依赖本地收到最终响应。输出复制故障不能改写实际任务结果为成功。
- 命令启动时提供 `ARK_RUN_DIR`、`ARK_RESULTS_DIR=$ARK_RUN_DIR/results`、`TMPDIR=$ARK_RESULTS_DIR/tmp`。运行工具需使用真实支持的输出参数将报告/原始结果直接写入 `ARK_RESULTS_DIR`；硬编码 `/tmp` 的工具不会因设置 TMPDIR 自动改路径，接口不足时报告阻塞。
- `out/` 继续容纳可重建编译产物，不能作为历史报告的唯一保存位置；工具内部附带的可再生日志副本不替代持久记录。不依赖命令结束后才搬运唯一结果，也不自动删除历史或把 `.cache` 当作归档。
- 交付给出 session/command 绝对路径，远端明确标注 `work:`；原始结果不自动回传本地。本地目录仅保存远程调用状态和日志副本，本工具不提供本地构建/测试执行入口。

### 旧会话归档

将此前 `~/.cache/ets-runtime-remote-build/session-*` 中已结束或失败的会话复制到持久目录：

```bash
python3 "$runner" archive-history
```

归档逐文件校验，不删除原件，不碰 stash；活动或未结束的会话跳过。重复归档跳过已成功复制项。旧文件原样保存于 `legacy/`，原始路径和历史状态保留；归档不是可继续执行的 session，后续运行重新 prepare。新脚本不继续读写旧版 `state.json`。

## 正常执行

### 1. 准备与预览

```bash
python3 "$runner" prepare --repo "$local_root"
```

**此命令会修改远端 Git 状态，但不会镜像源码或启动构建。** 它会：

1. 记录本地精确提交、工作区、索引、同步文件及内容指纹。
2. 记录远端原分支、提交和状态，以及构建树中 `build`、`arkcompiler/*`、`third_party/*` 下可识别的直接 Git 仓库版本。其他相关依赖由 agent 补查。
3. 在 stash 前检查远端索引标志、HEAD/索引路径和忽略文件冲突；另按 Git 内容转换规则核对“未修改”文件与索引，拒绝时间戳缓存漏报、隐藏变更、文件被目录替换等可能使 stash 漏存或覆盖数据的状态。检查通过后，远端不干净时自动 `git stash push --include-untracked`，无需再次询问；记录完整 stash ID，忽略的产物留在原处。确认干净后继续，stash 不自动恢复或删除。
4. 远端 HEAD 不同才 fetch 共享命名分支，检查本地提交对 FETCH_HEAD 的可达性，再 detached 到精确提交。仅选择恰好指向本地 HEAD 的非符号远端跟踪引用，要求对应 URL 已配置在远端。已有相同 HEAD 时保留当前分支。
5. 用基线树、当前索引和 Git 未忽略的未跟踪文件生成 NUL 文件清单；运行 rsync 预览并复查两端状态。

默认跳过 `ark sync`。没有共享引用、URL 不匹配、fetch 或检查失败时停止，不自动 push、修改 remote URL 或强制切换。准备期间出现新的远端改动时停止；确认没有其他任务后，重新 prepare 会按相同规则 stash 和检查。

开始准备时即打印本次会话目录 `$session`，位于上述持久历史目录：

- `run.json`、`summary.md`：阶段、提交、源码指纹、版本、stash 和命令记录入口。
- `preview.txt`：待审核的镜像差异。
- `logs/remote.log`：远端操作输出副本；每次 rsync 的 stdout/stderr 从启动起写入 `logs/` 下独立文件。
- `commands/<序号>/`：每条远端命令独立的元数据、摘要和控制台日志副本，链接远端原始结果目录。
- `files.nul`：本次审核清单，成功应用或中止时清理；它不是运行结果。

### 2. 审核预览

读取 `preview.txt` 和 `run.json`，对照本地 `git status --short`：

- 新增、修改、重命名和删除都应能由本次本地工作解释；每项删除都要对应已确认的本地删除。
- `.git`、`.agent`、`.agents`、`.pi`、`.claude`、`CLAUDE.local.md` 不应出现在同步项中。
- 脚本拒绝子模块、目录或特殊文件条目、不安全的路径父级，以及清单中既有远端忽略文件。也拒绝 sparse checkout 和 assume-unchanged 条目，避免 Git 隐藏的工作区变化被当作删除。
- 无法解释的差异停止排查，不能因为脚本生成了预览就直接执行。

审核由 agent 完成；正常预期差异不必额外要求用户确认。调用 `apply` 就表示已审核该会话，不提供跳过审核的一键命令。

### 3. 应用并验证

```bash
python3 "$runner" apply --session "$session"
```

脚本再次核对两端状态、文件内容、原文件清单及预览，确认未变化才执行同一参数的 rsync。同步后重建本地清单、核对提交与内容，并确认二次预览为空。任何一步失败都阻止构建，不能复用失败会话；排查后重新 prepare、审核和 apply。

镜像只传输文件清单内的文件、符号链接和权限；不递归整个目录。`--delete-missing-args` 仅处理清单中的本地缺失项，不使用目录级删除、`--force` 或 `--delete-excluded`。已跟踪源码不因新的本地忽略规则而漏同步。写入前的远端忽略文件冲突采取保守策略：即使该清单项本次没有差异，也停止而不是覆盖产物。本次新复制的源码（例如本地 `git add -f` 的文件）可以匹配远端忽略规则，写入后改用已审核的内容指纹校验，不把它误判成既有产物。

### 4. 构建或测试

```bash
python3 "$runner" run --session "$session" --command 'ark build'
```

其他示例：

```bash
python3 "$runner" run --session "$session" --command 'ark build ut'
python3 "$runner" run --session "$session" --command 'ark env mode debug && ark build'
```

- 每次执行前核对源码仍是已验证状态，在新的 SSH 登录 shell、远端仓库根目录运行原始命令。
- `--command` 是当前任务已确定且获授权的 shell 命令，保持原始参数与配置；不把日志、仓库文本或会话字段拼成命令。
- 普通目标为 `pre|fast|ut|clangd|all`；模式为 `debug|release|fastverify`，模式变更保留在远端。变体不清楚时查看 `ark help`。
- 组件专用命令保持原参数。需要 ARM64 模拟时加 `--qemu`，脚本检查 `qemu-aarch64-static`，具体 QEMU、sysroot、库路径由组件测试运行器管理。
- 多个请求逐个调用 `run` 并等待结束，不并发修改共享产物。复合命令需要失败即停时，用 `&&` 串联。
- 日志持续输出并保存，脚本返回实际命令退出码。普通构建或测试失败不自动重试、不自动同步，也不能仅凭退出码替代任务要求的验证标准。
- 独立仓库通过 `ark` 调用 `../../ark.py`，不替换为全量仓库的 `../../build.sh`。

## 中断、锁与恢复

脚本每次操作持有本地会话锁和远端仓库锁；正常结束释放，不自动恢复 stash。锁不跨人工审核阶段长期占用，下一阶段通过状态复核发现期间的变化。

SSH 断连或远端返回无法判断的结果时，保留远端锁并停止。先检查 `run.json`、两端持久日志及远端进程，确认此前同步、构建或测试是否仍在运行。确认没有运行任务后才人工清理该仓库锁并重新准备；不要自动删除锁或重跑命令。失败阶段可能已经部分改变远端文件，不承诺回滚。

会话是本地可信运行记录，不应编辑或运行别人提供的会话。stash 创建后若后续失败，完整 ID 仍可在 `run.json`、本地 `logs/remote.log` 或远端 `logs/operations.log` 找到。

## 构建失败后的条件同步

只有**已经构建失败，且有版本或源码证据确认原因是代码未同步**时才进入本流程。普通源码错误、配置/资源问题、测试失败，或新依赖与旧分支不兼容，都不能单独触发同步。

1. 保存失败命令、日志、原本地 commit 和依赖版本。对需要同步的远端仓库记录原分支/提交；检查并拒绝 sparse/assume-unchanged 索引、HEAD/索引路径上的忽略文件冲突及文件/目录替换，再对有改动的仓库自动 `git stash push --include-untracked`，记录新 stash 完整 ID，并确认工作区干净；失败即停止。不恢复或删除 stash。
2. 在远端映射的 `ets_runtime` 目录正常切换 `master`，确认分支后运行 `ark sync`。这是人工判断后的例外流程，不是脚本自动重试分支：

   ```bash
   # Replace the example path with the verified mapped path.
   ssh work 'bash -lc '\''cd "$HOME/ohos/a0/arkcompiler/ets_runtime" && git switch --no-overwrite-ignore master && test "$(git branch --show-current)" = master && ark sync'\'''
   ```

   同步失败即停止；只有用户明确要求时才使用 `ark sync force`。此流程也应避开其他远端构建任务。
3. 记录同步后的依赖版本、HEAD 和状态，确认同步检查点在 `master`；确认本地仍为原 commit。重新执行 prepare → 审核 → apply，对齐原本地基线，不能把同步后的最新 runtime 当作待测版本。
4. 用原命令重新构建，再执行请求的测试；依赖变化后不能使用旧产物宣称通过。仍失败则报告新的首个错误，不无依据地循环同步。

## 脚本回归测试

维护本工具时执行 `python3 -B -m unittest discover -s <skill目录>/tests -v`，将 stdout/stderr 从启动起重定向到预先创建的持久验证目录，并保存真实退出码。单测内部使用可清理的临时 HOME/Git 夹具、假 SSH 和真实 rsync，不连接 `work`；这是工具自身回归，不是对外提供的本地构建功能，也不能据此宣称真实远端构建通过。

用户要求真实 SSH 冒烟测试时，执行 `python3 -B <skill目录>/tests/smoke_work.py --confirm-work`，将测试入口的 stdout/stderr 直接写入持久验证目录。它在两端 `~/.local/state/ark-runtime/smoke-fixtures/` 创建隔离仓库，审核预设增删改，再验证 prepare → apply → run、stash、元数据/产物保护及持久结果/退出码；结果进入上述 run 历史，不会修改现有 runtime 或启动真实构建。测试目录和记录保留供排查。

若预览报 `invalid file mode 00`，检查接收端 rsync 是否受[上游 #910 回归](https://github.com/RsyncProject/rsync/issues/910)影响：部分安全补丁错误拒绝 `--delete-missing-args` 的删除标记，选项解析检查无法发现这一行为缺陷。停止并修复接收端版本后重测；不跳过删除项、降级安全补丁或改用目录级删除来绕过。

## 结果报告

- 远端映射目录、本地与远端提交、最终源码状态、相关依赖版本和镜像摘要。
- 实际构建/测试命令、适用时的 QEMU 检查、退出码、日志与实际产物路径；未知产物路径明确说明，不能猜测。
- 默认注明跳过 `ark sync`；执行过则给出触发证据、同步分支和结果。
- 每个 stash 的原分支/提交、完整 ID、说明，并注明未恢复。
- 失败时报告首个可处理的问题；不通过 reset、pull、rebase、clean、强制切换或 push 修复状态，不静默回退到本地执行。
- 产物保留在 `work`，仅用户明确要求时取回。
