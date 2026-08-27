---
name: oh-pr
description: 'End-to-end OpenHarmony / GitCode PR workflow: squash local changes into one Conventional Commit, create a new GitCode issue, then open a PR with the repo''s own PR template body via the `oh-gc` CLI. Use whenever the user wants to "提 PR / 提个 MR / merge request" on a GitCode (gitcode.com) or OpenHarmony repo, create a new issue and PR, squash commits for submission, or anything involving `oh-gc pr create` / `oh-gc issue create`. Trigger it even when the user just says "提 PR" or "新建分支并提 PR" without naming the tooling — this skill owns the full squash→issue→PR pipeline for these repos.'
---

# oh-pr

OpenHarmony / GitCode 仓库的端到端提 PR 流程：把本地改动 squash 成一个语义化提交，创建一个新 issue（拿到链接回填到提交 trailer），再用 `oh-gc` CLI 按**仓库自带的 PR 模板**创建 PR。

适用仓库：托管在 gitcode.com 的 OpenHarmony 仓库（`openharmony/<repo>`）及其个人 fork。前置 CLI：`oh-gc`（`@oh-gc/cli`）。依赖 `squash-pr` 的提交规范（标题 ≤49 字符、Conventional Commits、`Issue`/`Co-Authored-By: Agent`/`Signed-off-by` 尾部）。

## 为什么是这个顺序

issue 必须在 squash 提交**之前**创建，因为提交 trailer 里的 `Issue:` 行需要真实链接。所以流程是：**理解改动 → 建 issue → squash 提交（带 issue 链接）→ 推 fork → 建 PR（用仓库模板）**。如果你先 squash 再建 issue，提交里就少了 trailer，得 amend 一次，多一步且容易忘。

## 前置检查

1. `git status --short`：工作区有改动（或已在 feature 分支上有提交）。当前在 `master`/`main` 上时，本 skill 会新建分支。
2. 确定 **upstream remote** 与 **fork remote**：
   - `git remote -v` 看两条 push URL。URL 里命名空间是 `openharmony` 的那条是 upstream（PR 目标）；命名空间是个人账号（如 `ekil`）的那条是 fork（推送 + PR head 来源）。
   - 若只有一个 remote 且就是 upstream，需要先在 GitCode 网页 fork 一次；本 skill 不自动 fork。
3. `oh-gc auth status`：未登录则用 `scripts/gitcode_login.sh`（复用 `~/.git-credentials` 里的 GitCode PAT，通过 stdin 喂给 `oh-gc auth login`——它的 `--token` flag 不可靠，仍会提示输入）。
4. 确认仓库存在 PR 模板：`ls .gitee/PULL_REQUEST_TEMPLATE*.md`（OpenHarmony 仓库普遍有）。有就用它填 PR body；没有再退化成自写的两段（修改原因/修改描述）。

## 流程

### 1. 理解改动，草拟 issue 标题与正文

`git diff`（或 `git diff <merge-base>..HEAD` 若已有提交）通读全部变更。issue 正文要写清「期望行为/要加什么」，标题写一句目标。issue 是给社区看的动机说明，PR 描述是给 reviewer 看的变更清单，两者侧重不同、不要互相照抄。

### 2. 创建新 issue（套用 GitCode issue 模板）

**关键坑**：GitCode 网页端创建 issue 时会自动套用一个平台级 Bug 报告模板（仓库里没有 `.gitee/ISSUE_TEMPLATE` 文件），但 `oh-gc issue create --body` 传入纯文本**不会**自动套模板——必须手动把模板章节写进 `--body`。否则 issue 看起来像裸文本，与社区其他 issue 格式不一致，容易被退回。

先获取模板内容：`oh-gc issue list --repo <upstream-owner>/<repo> --state open --limit 1` 拿一个最近 issue 编号，再 `oh-gc issue view --repo <upstream-owner>/<repo> <编号>` 看它的 body——GitCode 平台模板的章节结构即从已有 issue 的正文里抄。OpenHarmony 仓库的 issue 模板通常包含以下章节（以实际 issue 为准）：

```
感谢对OpenHarmony社区的支持与关注，欢迎反馈缺陷。

### 发生了什么问题？
<描述当前问题/现状>
### 期望行为是什么？
<描述期望行为/要加什么>
### 如何复现该缺陷
<复现路径，代码审查类可写「查看 xx 文件 xx 函数」>
### 其他补充信息
<对应 PR 链接等补充>
### 版本或分支信息
- [x] master
- [ ] Release 7.0
- [ ] Beta 7.0
- [ ] Release 6.1
- [ ] Release 6.0
- [ ] Release 5.1.0
- [ ] Release 5.0.3
- [ ] Release 4.1
- [ ] Other
```

把填好的 body 写到临时文件再创建 issue（body 较长时用文件避免 shell 转义问题）：

```bash
oh-gc issue create \
  --repo <upstream-owner>/<repo> \
  --title "<issue 标题，中文>" \
  --body "$(cat /tmp/issue-body.md)"
```

成功输出形如 `Created issue #13501`。记下 issue 编号与 URL（`https://gitcode.com/<upstream-owner>/<repo>/issues/<编号>`）。若 issue 已存在则跳过这一步，直接用已有链接。

若建好后发现 body 没套模板（忘了看已有 issue），用 `oh-gc issue update <编号> --repo <upstream-owner>/<repo> --body "$(cat /tmp/issue-body.md)"` 覆盖更新。

### 3. squash 提交（遵循 squash-pr 规范）

1. 当前在 `master`/`main`：`git checkout -b <分支名>`（分支名用 `docs/`、`fix/`、`perf/` 等前缀 + 简短主题，与仓库已有分支风格一致）。
2. `git add` 相关文件。
3. 单次提交，信息格式（详见 `squash-pr` skill）：
   - 标题 `type(scope): summary`，英文，**≤49 字符**（用 `git log -1 --format='%s' | awk '{print length}'` 自查）。
   - 正文英文：一段概述 + 列表补充次要变更。
   - 尾部三行，顺序固定，`<issue链接>` 替换为第 2 步的真实 URL；`Co-Authored-By: Agent` 是字面值照写、不补邮箱；`Signed-off-by` 取 `git config user.name`/`user.email`：
     ```
     Issue: <issue链接>
     Co-Authored-By: Agent
     Signed-off-by: <user.name> <user.email>
     ```
   - 仓库 commit-msg 钩子若自动追加 `Change-Id` 或 AI 标注行，保留即可。
4. 多提交需 squash 时按 `squash-pr` 的 `git reset --soft <merge-base>` + 备份分支流程，但本 skill 触发时通常改动一次成型，可直接单提交。

### 4. 推送到 fork

```bash
git push <fork-remote> <分支名>
```

upstream 通常受保护不能直接推；推到个人 fork。push 成功后远端会回显一个 merge request 创建链接，**不要用它**——那是 fork 内部 MR，我们要的是 upstream 上的跨 fork PR。

### 5. 用仓库 PR 模板填充 body 并创建 PR

先读模板：`cat .gitee/PULL_REQUEST_TEMPLATE.zh-CN.md`（或仓库实际模板文件）。按其章节填写：关联 issue、修改原因、修改描述、自测试项勾选、L0 用例、变更行数自检等。纯文档/配置类改动，自测试项逐条勾「不涉及，无需验证」并在理由栏写明（如「仅改 AGENTS.md，不涉及运行时代码」）；变更行数低于 200 时勾「不涉及」。

把填好的 body 写到临时文件，再创建 PR：

```bash
oh-gc pr create \
  --repo <upstream-owner>/<repo> \
  --base master \
  --head "<fork-owner>:<分支名>" \
  --title "<与 commit 标题一致>" \
  --body "$(cat /tmp/pr-body.md)" \
  --close-related-issue
```

**关键坑**：fork PR 的 `--head` 必须是 `<fork-owner>:<分支名>` 形式（如 `ekil:docs/agents-checklist`）。只写裸分支名会报 `403 Forbidden`，因为 API 会在 upstream 仓里找该分支而找不到。

`--base` 默认 `main`，OpenHarmony 多数仓库主干是 `master`，需显式传 `--base master`。`--close-related-issue` 让 PR 合并时自动关闭关联 issue。

### 6. 校验

```bash
oh-gc pr view --repo <upstream-owner>/<repo> <PR编号>
```

确认 State=open、Head/base 正确、body 确实用了模板章节、关联 issue 出现。若 body 不对（比如一开始没用模板），用 `oh-gc pr update <编号> --repo <upstream-owner>/<repo> --body "$(cat /tmp/pr-body.md)"` 覆盖更新。

## 常见修正

- **Issue 没套模板**：`oh-gc issue create --body` 不会自动套 GitCode 平台 issue 模板，必须手动把模板章节写进 body。先 `oh-gc issue view` 一个最近 issue 抄模板结构，再填章节。建好后发现没套模板的，用 `oh-gc issue update --body` 覆盖。
- **PR 没用模板**：这是最常见的返工。建 PR 前必须先 `cat` 模板文件、按章节填，而不是自写两段了事。已建好但没用模板的，读模板重填后 `oh-gc pr update` 覆盖。
- **`--head` 裸分支名 → 403**：fork PR 改成 `fork-owner:branch`。
- **oh-gc `--token` 不生效**：stdin 喂 token（见 `scripts/gitcode_login.sh`）。
- **标题超长**：把专有类名换成通用说法，具体名留正文第一句。
- **提交 trailer 缺 Issue 行**：说明第 2 步建 issue 漏了或顺序错了；用 `git commit --amend` 补上 `<issue链接>`。

## 输出

完成后向用户回报：
- 新建的 issue 编号 + URL
- 分支名 + fork 推送位置
- PR 编号 + URL + mergeable 状态

不要在 skill 末尾重复贴 PR 全文——`oh-gc pr view` 已是事实来源，给链接即可。
