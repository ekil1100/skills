---
name: commit-push
description: 当用户要求 commit、push、提交、推送、生成 commit message，或说“commit msg 语义化”“帮我提交并推送”时使用此 skill。它会检查 diff，生成 Conventional Commits 消息，并按需提交或推送。
---

# Commit Push

用于从查看改动到生成提交消息、提交并按需推送的 Git 工作流。目标是提交内容准确、范围克制，并避免带入无关文件或敏感内容。

如果用户只要求生成 commit message，只输出建议消息，不执行 `git add`、`git commit` 或 `git push`。

## 工作流

1. 检查状态：运行 `git status --short --branch`、`git diff --stat`、`git diff --cached --stat`。
2. 阅读关键 diff：未暂存改动用 `git diff -- <path>`，已暂存改动用 `git diff --cached -- <path>`。
3. 判断提交范围：
   - 用户指定文件、目录或功能时，只处理相关路径。
   - 用户明确说全部提交时，也要排除明显无关、敏感或不应提交的文件。
   - 已有 staged 内容时尊重用户选择，确认它是否应与未暂存改动同属一 commit。
   - 范围无法可靠判断时先询问用户。
4. 暂存前拦截风险：发现疑似密钥、令牌、凭据、私有证书、大型生成文件或无关产物时停止并说明。
5. 暂存选定文件后，运行 `git diff --cached --stat` 确认实际提交内容。
6. 生成 Conventional Commits 消息；用户要求实际提交时再执行 `git commit`。
7. 提交成功后运行 `git status --short --branch` 确认剩余改动。
8. 用户明确要求 push 或 commit-push 时，再推送当前分支。

不要还原用户已有改动。不要用 `git reset --hard`、`git checkout --` 等破坏性命令清理工作区，除非用户明确要求。

## 消息生成

根据实际 diff 生成 Conventional Commits 消息。`scope` 优先用受影响模块、目录、功能名或 skill 名；没有清晰范围时省略。

`subject` 描述行为变化，不加句号，避免 `update files`、`fix bug`、`misc changes` 这类泛称。只有改动原因、迁移影响、破坏性变化或重要取舍需要说明时才加 body/footer。

## 提交与推送

提交失败时按原因处理：

- pre-commit hook 修改了文件：重新检查 diff，必要时重新暂存并再次提交。
- 测试或 lint 失败：报告失败原因，不绕过 hook，除非用户明确要求。
- 没有可提交改动：说明当前状态。

推送规则：

- 当前分支已有 upstream 时运行 `git push`。
- 没有 upstream 时优先检查远端；确认使用 `origin` 后运行 `git push -u origin <branch>`。
- 推送被拒绝时说明原因和下一步选择，不自动 rebase、merge 或 force push。
- 不执行 `git push --force`；只有用户明确要求并确认风险时，才考虑 `--force-with-lease`。

## 最终回复

只生成消息时，直接给出推荐 commit message；如有 2-3 个合理候选，按推荐顺序列出并说明首选原因。

实际提交后，最终回复包含：

- 短 commit hash。
- commit message。
- 是否已推送，以及目标远端/分支。
- 是否还有未提交剩余改动。
- 已运行的检查，或无法运行的原因。
