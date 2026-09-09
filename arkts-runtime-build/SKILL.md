---
name: arkts-runtime-build
description: "在 SSH 主机 work 构建独立 OpenHarmony ArkTS ets_runtime，适用于本地 WSL 编译过慢，以及执行开发 skill 提供的构建或测试命令（含 ARM64/QEMU）。默认复用远端依赖，通过已有共享 Git remote 对齐精确本地 commit，以 rsync 镜像经检查的源码改动后执行，不回退到本地。仅在构建失败且确认原因是代码未同步时执行 ark sync。"
---

# ArkTS Runtime Remote Build

Build `ets_runtime` directly on SSH host `work`, or execute a build-dependent test command there when an applicable development skill supplies it. This skill owns remote preparation and execution; the development skill owns component-specific commands and acceptance criteria. `work` provides `qemu-aarch64-static` on `PATH` for ARM64 cross-testing.

No helper script is needed; perform the checks and commands explicitly so commit alignment, source changes, and deletions remain reviewable.

## Path mapping

Resolve the local repository root with `git rev-parse --show-toplevel` and verify it is an `ets_runtime` checkout below the local `$HOME`. Map the path relative to the local home onto the remote home.

Example:

- Local: `~/ohos/a0/arkcompiler/ets_runtime`
- Remote: `$HOME/ohos/a0/arkcompiler/ets_runtime`

Safely shell-quote every derived path, ref, and argument.

## Required sequence

1. Record the local state:
   - `git rev-parse HEAD` for the exact commit base.
   - `git status --short` for tracked and untracked local changes.
   - `git for-each-ref --format='%(refname:short)' --points-at HEAD refs/remotes/` for remote-tracking refs whose tip is exactly the local `HEAD`. Ignore symbolic `HEAD` aliases.
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
       git stash push --include-untracked -m "arkts-runtime-build pre-build $(date -u +%Y%m%dT%H%M%SZ)"
       git log -1 --format='stash: %H %s' refs/stash
   fi
   test -z "$(git status --porcelain --untracked-files=all)"
   REMOTE
   ```

3. 记录相关远端依赖仓库的 commit 和工作区状态，沿用现有依赖版本。默认跳过全仓同步；只有构建失败且确认代码未同步时，才进入[条件同步流程](#构建失败后的条件同步)。
4. Align the remote `ets_runtime` checkout to the recorded local commit before mirroring source:
   - 若远端 `HEAD` 已等于记录的本地 `HEAD`，保留当前分支或 detached 状态，无需切换。
   - Otherwise, select a non-symbolic local remote-tracking ref that points exactly at the local `HEAD` and whose remote is already configured with the same URL on `work`.
   - 在 `work` fetch 该命名分支，确认记录的本地 `HEAD` 是有效 commit，且 `git merge-base --is-ancestor <local-head> FETCH_HEAD` 成功，再执行 `git switch --detach <local-head>`。共享分支可以已经向前推进；检查可达性并 detached 到精确 commit，避免移动远端现有分支。此处按需 fetch 目标分支不是 `ark sync`，仍需保留。
   - 切换前若远端又出现未提交改动，按步骤 2 暂存并确认干净，再正常切换。没有匹配的共享 ref、fetch 失败、拉取历史不含本地 commit，或暂存后普通 detached 切换仍失败时，停止并报告。不要自动 push 本地 commit 或修改 remote URL。
5. Read the remote checkout state, `git rev-parse HEAD`, and `git status --short` again. Require the remote `HEAD` to equal the recorded local `HEAD` exactly. On mismatch, report both commit IDs and stop.
6. Preview the local-to-remote source mirror:

   ```bash
   rsync -rlpc --delete-delay --itemize-changes --dry-run --prune-empty-dirs \
     --exclude='/.git' \
     --exclude='/.agent' \
     --exclude='/.claude' \
     --exclude='/CLAUDE.local.md' \
     --filter=':- .gitignore' \
     "$local_root/" \
     "work:~/ohos/a0/arkcompiler/ets_runtime/"
   ```

   This mirrors non-ignored source contents and executable bits while protecting the repository-root `.git` whether it is a directory or symbolic link, local agent configuration and work notes, and Git-ignored build outputs. `--prune-empty-dirs` prevents ignored generated test outputs from creating hundreds of empty directories. Do not add `--delete-excluded`.
7. Review every dry-run item against the local `git status --short`:
   - Local modifications, additions, renames, and deletions are expected.
   - Explicitly excluded local-only agent files are expected to be absent.
   - 本地状态无法解释的改动或删除可能是远端新出现的工作。若对应远端未提交改动，按步骤 2 的规则暂存，重新确认远端 HEAD 等于记录的本地 HEAD，再重新预览。暂存后仍无法解释的差异应停止并报告，不直接覆盖。
8. Run the same `rsync` command without `--dry-run` only after the preview is safe.
9. Repeat the dry-run command and require no remaining items. If differences remain, stop without compiling.
10. Run the requested build and any explicitly requested build-dependent test in a new SSH login shell:
    - Normal: `ark build`
    - Selector: `ark build pre|fast|ut|clangd|all`
    - Mode: run `ark env mode debug|release|fastverify` before `ark build`. The selected mode persists remotely.
    - Specialized build or test: use the exact command and arguments defined by the applicable development skill, from the remote `ets_runtime` root. Do not restate, substitute, or improvise component-specific commands in this skill.
    - QEMU: before executing a command that requires ARM64 emulation, verify `command -v qemu-aarch64-static` in the remote login shell. Stop and report the missing dependency if it is unavailable; otherwise let the component runner manage QEMU, sysroot, and library arguments.

    Run multiple requested builds or tests sequentially because they may share build outputs.

For a normal build:

```bash
ssh work "bash -lc 'cd \"\$HOME/ohos/a0/arkcompiler/ets_runtime\" && ark build'"
```

Use `ark help` if a requested variant is unclear. Stream output and wait for the SSH command to finish.

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

## Boundaries

- The independent-repository entry point is `../../ark.py` through the user's `ark` wrapper. Never substitute the full-repository `../../build.sh` command.
- Never force a switch or automatically run reset, pull, rebase, clean, push, or remote-URL changes to repair state.
- Never mirror local source until local and remote `HEAD` match exactly.
- Never sync `.git`, local agent metadata, or Git-ignored outputs, and never use `--delete-excluded`.
- The applicable development skill determines what to build or test and how success is judged; this skill only prepares `work` and executes that command exactly.
- Never silently fall back to a WSL-local build or test.
- Do not automatically restart after an SSH disconnect; first determine whether the previous sync, build, or test is still running.
- Leave build and test outputs on `work` unless the user explicitly requests retrieval.

报告远端映射目录、最终 checkout 状态、本地与远端 commit ID、相关依赖版本、镜像摘要、实际构建或测试命令、适用时的 QEMU 检查、最终退出码及产物路径。默认注明跳过 `ark sync`；若执行了同步，补充触发证据、同步分支和结果。若创建了 stash，同时报告原分支/commit、stash 完整 commit ID 和说明，注明尚未恢复。遇到 SSH、stash、分支切换、同步、commit 对齐、镜像、构建、测试或环境错误时，指出首个可处理的问题。
