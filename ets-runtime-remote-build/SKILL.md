---
name: ets-runtime-remote-build
description: "在 SSH 主机 work 构建独立 OpenHarmony ArkTS ets_runtime，或执行开发 skill 提供的构建/测试命令（含 ARM64/QEMU）。适用于本地 WSL 编译过慢或需要远程构建测试的场景。"
---

# ets_runtime 远程构建

在 SSH 主机 `work` 上构建 `ets_runtime`，或执行适用开发 skill 提供的、依赖构建的测试命令。本 skill 负责远端环境准备与命令执行；开发 skill 负责组件专用命令及验证标准。`work` 的 `PATH` 中提供 `qemu-aarch64-static`，用于 ARM64 跨架构测试。

无需新增辅助脚本；逐项执行检查和命令，使提交对齐、源码变更及删除操作保持可检视。

## 路径映射

使用 `git rev-parse --show-toplevel` 确定本地仓库根目录并保存为 `local_root`，确认它是位于本地 `$HOME` 下的 `ets_runtime` 源码目录。取相对于本地家目录的路径，映射到远端家目录下的相同位置。

示例：

- 本地：`$HOME/ohos/a0/arkcompiler/ets_runtime`
- 远端：`$HOME/ohos/a0/arkcompiler/ets_runtime`

对所有推导出的路径、引用和参数进行安全的 shell 引用与转义。

## 执行顺序

1. 记录本地状态：
   - 用 `git rev-parse HEAD` 记录精确的提交基线，保存为 `local_head`。
   - 用 `git status --short` 记录已跟踪文件的改动和未跟踪文件。
   - 用 `git for-each-ref --format='%(refname:short)' --points-at HEAD refs/remotes/` 找出恰好指向本地 `HEAD` 的远端跟踪引用；忽略符号引用形式的 `HEAD` 别名。
2. 在 `work` 映射的远端 `ets_runtime` 仓库中记录状态并保护未提交改动；此步骤不切换 `master`、不执行 `ark sync`：
   - 记录原分支（或 detached HEAD）、commit 和 `git status --short`。
   - 工作区有已暂存、未暂存或未跟踪文件时，**直接执行 `git stash push --include-untracked`，无需再次询问**；干净时跳过。使用带时间戳的说明，记录新 stash 的完整 commit ID。忽略文件（包括构建产物）留在原处，不使用 `--all`。
   - stash 成功且 `git status --porcelain --untracked-files=all` 为空后才继续；stash 失败或仍有残留改动时停止并报告，不通过 reset/clean/强制切换修复。
   - 保留 stash 供用户恢复；无论后续成功或失败，都不自动 `pop`、`apply` 或 `drop`，避免将旧工作重新混入构建源码。此规则只暂存远端改动，本地改动保留用于后续镜像。

   ```bash
   ssh work 'bash -l -s' <<'REMOTE'
   set -e
   cd "$HOME/ohos/a0/arkcompiler/ets_runtime"
   git branch --show-current
   git rev-parse HEAD
   git status --short
   if test -n "$(git status --porcelain --untracked-files=all)"; then
       git stash push --include-untracked -m "ets-runtime-remote-build pre-build $(date -u +%Y%m%dT%H%M%SZ)"
       git log -1 --format='stash: %H %s' refs/stash
   fi
   test -z "$(git status --porcelain --untracked-files=all)"
   REMOTE
   ```

3. 记录相关远端依赖仓库的 commit 和工作区状态，沿用现有依赖版本。默认跳过全仓同步；只有构建失败且确认代码未同步时，才进入[条件同步流程](#构建失败后的条件同步)。
4. 镜像源码前，将远端 `ets_runtime` 对齐到已记录的本地提交：
   - 若远端 `HEAD` 已等于记录的本地 `HEAD`，保留当前分支或 detached 状态，无需切换。
   - 否则，选择一个恰好指向本地 `HEAD` 的非符号远端跟踪引用，并确认其对应仓库 URL 已配置在 `work` 上。
   - 在 `work` fetch 该命名分支，确认记录的本地 `HEAD` 是有效 commit，且 `git merge-base --is-ancestor <local-head> FETCH_HEAD` 成功，再执行 `git switch --detach <local-head>`。共享分支可以已经向前推进；检查可达性并 detached 到精确 commit，避免移动远端现有分支。此处按需 fetch 目标分支不是 `ark sync`，仍需保留。
   - 切换前若远端又出现未提交改动，按步骤 2 暂存并确认干净，再正常切换。没有匹配的共享 ref、fetch 失败、拉取历史不含本地 commit，或暂存后普通 detached 切换仍失败时，停止并报告。不要自动 push 本地 commit 或修改 remote URL。
5. 再次检查远端源码目录状态，执行 `git rev-parse HEAD` 和 `git status --short`。两端 `HEAD` 都必须与记录的 `local_head` 完全一致；不一致时报告提交 ID 并停止，不能继续使用旧基线生成清单。
6. 由 Git 生成同步文件清单，再预览源码镜像。使用 NUL 分隔，支持文件名中的空格、换行等字符；清单生成或排序失败时停止，不使用不完整结果：

   ```bash
   sync_list="$(mktemp)" || exit 1
   if ! (
       cd "$local_root" &&
       git ls-tree -r --name-only -z "$local_head" &&
       git ls-files --cached --others --exclude-standard -z
   ) > "$sync_list"; then
       rm -f -- "$sync_list"
       printf 'Failed to generate the sync file list.\n' >&2
       exit 1
   fi
   LC_ALL=C sort -zu -o "$sync_list" "$sync_list" || exit 1
   ```

   - `git ls-files --others --exclude-standard` 由 Git 处理各级 `.gitignore`、`.git/info/exclude` 和全局忽略规则（包括 `core.excludesFile` 及默认配置），也保留 Git 的否定规则语义；不将这些文件直接当作 rsync 过滤规则。
   - 合并基线树和当前索引，是为了保留已跟踪文件的更新、新增以及已暂存/未暂存删除。已跟踪源码不因匹配新的忽略模式而漏同步。
   - 清单仅用于当前仓库。预览前核对目录项、子模块和路径父级的符号链接；遇到目录项、子模块或可能越出仓库的路径时停止报告，不通过递归目录来绕过文件清单。

   ```bash
   rsync -lpc --itemize-changes --dry-run \
     --from0 --files-from="$sync_list" --delete-missing-args \
     --exclude='/.git' --exclude='/.git/***' \
     --exclude='/.agent' --exclude='/.agent/***' \
     --exclude='/.agents' --exclude='/.agents/***' \
     --exclude='/.pi' --exclude='/.pi/***' \
     --exclude='/.claude' --exclude='/.claude/***' \
     --exclude='/CLAUDE.local.md' --exclude='/CLAUDE.local.md/***' \
     "$local_root/" \
     "work:~/ohos/a0/arkcompiler/ets_runtime/"
   ```

   仅同步清单列出的文件、符号链接及权限，父目录由 rsync 按需建立；不加 `-r`，避免绕过清单递归复制忽略文件。`--delete-missing-args` 只删除清单中本地已不存在的对应目标；不加 `--delete`、`--delete-delay`、`--delete-excluded` 或 `--force`，避免清理远端独有的产物和笔记。文件清单中的路径是显式条目，排除规则须同时匹配根项和全部子项；即使元数据文件被 Git 跟踪，也要保护它们不被传输或删除。两端 rsync 需支持上述选项，缺失时停止，不退回目录级同步。

7. 对照本地 `git status --short`，逐项检视预览结果：
   - 本地修改、新增、重命名和删除属于预期差异；每条删除都必须对应已确认的本地删除。
   - `.git`、`.agent`、`.agents`、`.pi`、`.claude` 和 `CLAUDE.local.md` 不应出现在传输或删除项中。
   - 对将覆盖或删除的既有远端路径，核对其 Git 忽略状态（可用 `git check-ignore --stdin -z`）；若涉及远端忽略的文件，停止并报告冲突，不覆盖产物。两端的本地/全局忽略配置可能不同，不能只检查发送端。
   - 本地状态无法解释的改动或删除可能是远端新出现的工作。若对应远端未提交改动，按步骤 2 的规则暂存，重新确认远端 HEAD 等于记录的本地 HEAD，再重新预览。暂存后仍无法解释的差异应停止并报告，不直接覆盖。
8. 只有确认预览安全后，才移除 `--dry-run`，使用同一份已审核清单、其余参数完全相同的 `rsync` 命令执行镜像。任一步失败都停止，不进入构建。
9. 按步骤 6 重新生成另一份清单，用 `cmp` 确认它与已审核清单一致，并再次确认两端 `HEAD` 未变化；清单或基线变化时停止并重新核对。随后用原清单再次执行预览，确认没有剩余差异；仍有差异时停止，不进入编译。完成或中止后清理本次创建的临时清单，不删除其他文件。
10. 在新的 SSH 登录 shell 中执行请求的构建，以及用户明确要求的、依赖构建的测试：
    - 普通构建：`ark build`
    - 选择构建目标：`ark build pre|fast|ut|clangd|all`
    - 构建模式：先执行 `ark env mode debug|release|fastverify`，再执行 `ark build`。所选模式会保留在远端环境中。
    - 组件专用构建或测试：在远端 `ets_runtime` 根目录执行适用开发 skill 定义的原始命令与参数。本 skill 不重复维护、替换或自行编造组件专用命令。
    - QEMU：执行需要 ARM64 模拟的命令前，在远端登录 shell 中用 `command -v qemu-aarch64-static` 检查工具是否可用。缺失时停止并报告；可用时由组件测试运行器管理 QEMU、sysroot 和库路径参数。

    多个构建或测试请求顺序执行，避免共享产物被并发修改。

普通构建示例：

```bash
ssh work "bash -lc 'cd \"\$HOME/ohos/a0/arkcompiler/ets_runtime\" && ark build'"
```

请求的命令变体不明确时，查看 `ark help`。持续输出执行日志，并等待 SSH 命令结束。

## 构建失败后的条件同步

构建失败不等于需要同步。先保存失败命令、日志及当前版本，检查是否确实缺少待测代码要求的上游提交或配套代码。普通源码错误、配置/资源问题、测试失败，以及“较新依赖与较旧待测分支不兼容”都不能单独触发 `ark sync`；后者应先核对兼容版本，而不是继续追新。

只有已发生构建失败、且有版本或源码证据确认原因是代码未同步时，才执行：

1. 保留本次记录的本地 commit 作为测试基线。对需要同步的远端仓库按步骤 2 的规则自动 stash，记录原版本和 stash ID，并确认工作区干净。
2. 从远端映射的 `ets_runtime` 目录切换 `master`，确认当前分支后运行 `ark sync`：

   ```bash
   ssh work "bash -lc 'cd \"\$HOME/ohos/a0/arkcompiler/ets_runtime\" && git switch master && test \"\$(git branch --show-current)\" = master && ark sync'"
   ```

   `master` 仅是条件同步时的要求。普通切换或同步失败时停止并报告；只有用户明确要求时才使用 `ark sync force`。

3. 同步成功后记录远端分支、HEAD、状态及变化的依赖版本，确认同步检查点仍在 `master`。重新执行步骤 4–9，对齐原本地 commit 并再次镜像源码，不能直接把同步后的最新 runtime 当作待测版本。
4. 用原命令重新构建，再执行请求的测试；依赖已变化，不能跳过构建或使用旧产物宣称通过。若仍失败，报告新的首个错误，不无依据地循环同步。

## 边界

- 独立仓库的构建入口是 `../../ark.py`，通过用户的 `ark` 包装命令调用；不替换为全量仓库的 `../../build.sh`。
- 不通过强制切换，或自动执行 reset、pull、rebase、clean、push、修改远端仓库 URL 来修复状态。
- 本地与远端 `HEAD` 完全一致前，不镜像源码。
- 用 Git 文件清单同步，不递归整个仓库；显式保护 `.git`、`.agent`、`.agents`、`.pi`、`.claude`、`CLAUDE.local.md`，不覆盖或删除 Git 忽略的产物。只允许清单中已确认的删除，不使用目录级删除或 `--delete-excluded`。
- 开发 skill 决定构建/测试内容及成功标准；本 skill 只准备 `work` 环境并准确执行相应命令。
- 不静默回退到 WSL 本地构建或测试。
- SSH 断连后不自动重启任务；先确认此前的同步、构建或测试是否仍在运行。
- 构建和测试产物保留在 `work`，仅在用户明确要求时取回。

## 结果报告

- 列出远端映射目录、最终源码目录状态、本地与远端提交 ID、相关依赖版本、镜像摘要、实际构建或测试命令、适用时的 QEMU 检查、最终退出码及产物路径。
- 默认注明跳过 `ark sync`；若执行了同步，补充触发证据、同步分支和结果。
- 若创建了 stash，报告原分支/提交、stash 完整提交 ID 和说明，注明尚未恢复。
- 遇到 SSH、stash、分支切换、同步、提交对齐、镜像、构建、测试或环境错误时，指出首个可处理的问题。
