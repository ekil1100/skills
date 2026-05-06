---
name: commit-push
description: 当用户要求提交、推送、commit、push、生成语义化 commit message、提交当前改动并推送到远端，或说“commit msg 语义化”“帮我提交并推送”时使用此 skill。它要求先检查 git 状态和 diff，基于实际改动生成 Conventional Commits 风格的语义化提交信息，谨慎暂存相关文件，提交后按需推送当前分支。
---

# Commit Push

使用这个 skill 处理从“看改动”到“语义化提交并推送”的完整 Git 工作流。

目标是让提交记录准确反映真实改动，同时避免把无关文件、用户未确认的临时文件或敏感内容带进提交。

## 触发意图

当用户表达以下任一意图时使用：

- 生成语义化 commit message。
- 提交当前改动。
- 提交并推送当前分支。
- 修正、改写或选择 Conventional Commits 风格的提交信息。
- 中文表达如“commit msg 语义化”“帮我 commit push”“提交并推送”。

如果用户只要一条 commit message，不要执行 `git add`、`git commit` 或 `git push`。

## 提交前检查

先收集最小必要上下文：

1. 运行 `git status --short --branch` 查看分支、暂存区、未暂存和未跟踪文件。
2. 运行 `git diff --stat` 和 `git diff --cached --stat` 判断改动范围。
3. 对将要提交的文件阅读关键 diff：
   - 未暂存改动用 `git diff -- <path>`。
   - 已暂存改动用 `git diff --cached -- <path>`。
4. 如果存在未跟踪文件，先判断它们是否明显属于本次任务；不确定时询问用户。
5. 如果发现疑似密钥、令牌、凭据、私有证书、大型生成文件或无关产物，停止并提醒用户确认。

不要还原用户已有改动。不要用 `git reset --hard`、`git checkout --` 等破坏性命令清理工作区，除非用户明确要求。

## 选择提交范围

根据用户意图选择提交范围：

- 用户明确说“全部提交”时，可以暂存所有相关改动，但仍要排除明显敏感或不该提交的文件。
- 用户只提到某个功能、文件或目录时，只暂存相关路径。
- 工作区同时包含多组无关改动时，优先只提交与当前请求匹配的一组；如果无法可靠区分，先向用户确认。
- 如果已有暂存内容，尊重它。确认暂存内容和未暂存内容是否属于同一提交；不要无意覆盖用户的 staged selection。

暂存后再次运行 `git diff --cached --stat`，确认提交内容和预期一致。

## 语义化 Commit Message

默认使用 Conventional Commits：

```text
<type>(<scope>): <subject>
```

可用 type：

- `feat`: 新功能或新增能力。
- `fix`: 修复 bug 或错误行为。
- `docs`: 文档、说明、注释类更新。
- `style`: 不改变行为的格式、排版、样式调整。
- `refactor`: 不新增功能也不修 bug 的代码重构。
- `perf`: 性能优化。
- `test`: 新增或修改测试。
- `build`: 构建系统、依赖、打包配置。
- `ci`: CI/CD 配置。
- `chore`: 维护性改动、脚本、元数据。
- `revert`: 回滚提交。

选择 scope：

- 优先使用受影响模块、包、目录、功能名或 skill 名。
- 如果改动横跨多个区域且没有清晰 scope，可以省略 scope。
- scope 使用小写短词，例如 `auth`、`api`、`skill`、`commit-push`。

写 subject：

- 使用英文时用祈使句、小写开头，不加句号，例如 `feat(auth): add password reset flow`。
- 使用中文时保持简短清晰，不加句号，例如 `docs(commit-push): 添加语义化提交流程`。
- 避免泛泛而谈的 `update files`、`fix bug`、`misc changes`。
- 主题应描述用户能理解的行为变化，而不是只列文件名。

需要正文时才添加 body：

- 改动原因、迁移影响、破坏性变化或重要取舍值得说明时，添加简短正文。
- 破坏性变化使用 `BREAKING CHANGE:` 标注。
- 关联 issue 可在 footer 中写 `Refs #123` 或 `Closes #123`。

## 执行提交

当用户要求实际提交时：

1. 暂存选定文件。
2. 用生成的语义化消息执行 `git commit -m "<message>"`。
3. 如果 commit 失败，读取错误并处理：
   - pre-commit hook 格式化了文件：重新检查 diff，必要时重新暂存并再次提交。
   - 测试或 lint 失败：报告失败原因；不要绕过 hook，除非用户明确要求。
   - 没有改动可提交：说明当前没有可提交内容。
4. 提交成功后运行 `git status --short --branch` 确认工作区状态。

如果用户只要求“生成 commit msg”，最终只输出建议的 message，并简要说明它基于哪些改动。

## 推送

当用户明确要求 push 或 commit-push 时：

1. 确认当前分支名和 upstream。
2. 如果当前分支已有 upstream，运行 `git push`。
3. 如果没有 upstream，运行 `git push -u origin <branch>`，除非仓库不是用 `origin` 作为默认远端；这种情况先检查 `git remote -v`。
4. 如果推送被拒绝：
   - 先运行 `git status --short --branch` 和必要的远端信息检查。
   - 不要自动 rebase、merge 或 force push，除非用户明确授权。
   - 向用户说明被拒绝的原因和下一步选择。

不要执行 `git push --force`。只有用户明确要求并确认风险时，才考虑 `--force-with-lease`。

## 最终回复

实际提交后，最终回复包含：

- 提交 hash 的短号。
- 使用的 commit message。
- 是否已推送，以及目标远端/分支。
- 若有未提交剩余改动，简要说明。
- 已运行的检查或无法运行的原因。

只生成消息时，最终回复直接给出推荐消息：

```text
type(scope): subject
```

如果有 2-3 个合理候选，可以按推荐顺序列出，并说明首选原因。
