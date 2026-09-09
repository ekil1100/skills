---
name: arkts-runtime-build
description: "Build or compile the standalone OpenHarmony ArkTS ets_runtime repository on SSH host work when local WSL compilation is too slow. Also use it to execute build or test commands supplied by an applicable development skill on work, including commands that require ARM64/QEMU. Switch the remote checkout to master for ark sync, align it to the exact local commit through an already configured shared Git remote, mirror reviewed local source changes with rsync, then execute the requested command without a local fallback."
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
2. 在 `work` 映射的远端 `ets_runtime` 仓库中，先保护未提交改动，再切换 `master` 并同步：
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
       git stash push --include-untracked -m "arkts-runtime-build pre-sync $(date -u +%Y%m%dT%H%M%SZ)"
       git log -1 --format='stash: %H %s' refs/stash
   fi
   test -z "$(git status --porcelain --untracked-files=all)"
   git switch master
   test "$(git branch --show-current)" = master
   ark sync
   REMOTE
   ```

   `master` 仍是同步所需分支。暂存并确认干净后，若普通 `git switch master` 或 `ark sync` 仍失败，停止并报告；只有用户明确要求时才使用 `ark sync force`。
3. After `ark sync`, read the remote `git branch --show-current`, `git rev-parse HEAD`, and `git status --short`. Require `master` at this checkpoint.
4. Align the remote `ets_runtime` checkout to the recorded local commit before mirroring source:
   - If the synchronized remote `HEAD` already equals the local `HEAD`, keep `master` checked out.
   - Otherwise, select a non-symbolic local remote-tracking ref that points exactly at the local `HEAD` and whose remote is already configured with the same URL on `work`.
   - On `work`, fetch that named branch. Require the recorded local `HEAD` to exist as a commit and be reachable from the resulting `FETCH_HEAD` with `git merge-base --is-ancestor <local-head> FETCH_HEAD`, then run `git switch --detach <local-head>`. The shared branch may have advanced since the local fetch; checking reachability keeps that safe while detached HEAD preserves the synchronized `master` ref.
   - 切换前若远端又出现未提交改动，按步骤 2 的暂存规则处理并确认干净，再正常切换；只复用暂存部分，不重复同步。没有匹配的共享 ref、fetch 失败、拉取历史不含本地 commit，或暂存后普通 detached 切换仍失败时，停止并报告。不要自动 push 本地 commit 或修改 remote URL。
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

## Boundaries

- `ark sync` updates base repositories on remote `master`; commit alignment then selects the exact local `ets_runtime` base; `rsync` finally updates its working tree.
- The independent-repository entry point is `../../ark.py` through the user's `ark` wrapper. Never substitute the full-repository `../../build.sh` command.
- Require `master` for the sync checkpoint. If the local base differs, build or test from a detached exact local `HEAD` without moving the remote `master` ref.
- Never force a switch or automatically run reset, pull, rebase, clean, push, or remote-URL changes to repair state.
- Never mirror local source until local and remote `HEAD` match exactly.
- Never sync `.git`, local agent metadata, or Git-ignored outputs, and never use `--delete-excluded`.
- The applicable development skill determines what to build or test and how success is judged; this skill only prepares `work` and executes that command exactly.
- Never silently fall back to a WSL-local build or test.
- Do not automatically restart after an SSH disconnect; first determine whether the previous sync, build, or test is still running.
- Leave build and test outputs on `work` unless the user explicitly requests retrieval.

报告远端映射目录、同步分支、最终 checkout 状态、本地与远端 commit ID、镜像摘要、`ark sync` 结果、实际构建或测试命令、适用时的 QEMU 检查、最终退出码及产物路径。若创建了 stash，同时报告原分支/commit、stash 完整 commit ID 和说明，注明尚未恢复。遇到 SSH、stash、分支切换、同步、commit 对齐、镜像、构建、测试或环境错误时，指出首个可处理的问题。
