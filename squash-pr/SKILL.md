---
name: squash-pr
description: 当用户给出 issue 链接,要求把当前分支 squash 成一个提交并生成 PR 表单的"修改原因/修改描述"时使用此 skill(例如"squash commits 并生成 PR 描述"、"/squash-pr <issue链接>"、"准备提 PR")。它会把分支上全部提交合并为一个语义化 commit,在提交信息结尾附上 Issue/Co-Authored-By/Signed-off-by 尾部,并输出可直接粘贴到 PR 表单的两段中文说明。
---

# Squash PR

把当前分支自 merge-base 以来的全部提交 squash 成一个符合 Conventional Commits 的提交,并产出 PR 表单需要的两段说明。输入是 issue 链接(从参数中获取;缺失时先询问用户)。

## 前置检查

1. `git status --short`:工作区必须干净,否则停下来让用户先处理。
2. 当前分支不能是 master/main。
3. 确定基线:`git merge-base master HEAD`,master 不存在时改用 main。
4. 读取全部待合并提交:`git log --oneline <base>..HEAD` 和 `git log --format='%B' <base>..HEAD`,以及 `git diff <base>..HEAD --stat`,理解整个分支做了什么——squash 后的提交信息要覆盖全部实质变更,而不是只抄第一个提交。

## Squash 流程

1. **备份**:`git branch backup/<分支名>-presquash`(已存在则加数字后缀)。原始历史必须可恢复。
2. 若分支只有一个提交,跳过 reset,改用 `git commit --amend` 只修信息。
3. `git reset --soft <merge-base>`,然后用整理好的信息一次性提交。
4. **提交信息格式**:
   - 标题:`type(scope): summary`(英文)。type 按变更主体选:性能优化用 `perf`、缺陷修复用 `fix`、结构调整用 `refactor`、新功能用 `feat`;scope 用主要改动模块名,与仓库已有提交的 scope 写法保持一致(先看 `git log --oneline -20 master` 的惯例)。
   - **标题全长不超过 49 个字符**(含 `type(scope): ` 前缀)。写完用 `git log -1 --format='%s' | awk '{print length}'` 自查;超长时把专有类名换成更短的通用说法(如 "replace HashTrieMap with ChainedHashMap" → "switch to chained hash map"),具体名称留给正文第一句交代。
   - 正文:英文,先一段概述整体变更(替换/新增了什么、核心机制),再以列表补充次要变更(改名、伴生修复、测试更新等)。已知测试结果时附 `Tested:` 行。
   - 结尾尾部(顺序固定,三行如下;仅 `Signed-off-by` 的署名取自 `git config user.name` / `user.email`,取不到或形如 noreply 时询问用户):

     ```
     Issue: <issue链接>
     Co-Authored-By: Agent
     Signed-off-by: <user.name> <user.email>
     ```

     其中 `<issue链接>`、`<user.name>`、`<user.email>` 是**占位符**,须替换为真实值;`Co-Authored-By: Agent` 是**字面值**,照原样写入,不要替换成具体人名、也不要补邮箱(它没有尖括号,故意区别于占位符)。
   - 仓库钩子若自动追加 Change-Id,保留即可。
5. **验证**:`git diff backup/<分支名>-presquash HEAD` 必须为空(squash 只改历史不改内容);不为空立即停下报告,不要继续。
6. 不要 push。最后告知用户备份分支名及确认无误后的删除命令。

## 输出两段 PR 说明

squash 完成后,基于整个分支的 diff(不是逐条提交)用中文输出以下两段,供用户直接粘贴到 PR 表单:

**修改原因**(目的、解决的问题,例如:修复xx场景崩溃问题)
- 说清动机:解决什么问题/优化什么路径,原方案的缺陷(性能、复杂度、缺陷),为什么选这个方案。
- 有伴生修复(顺手修掉的 bug)时单独点出。
- 一段连贯文字,不要罗列实现细节。

**修改描述**(做了什么、变更了什么,例如:xx函数入口增加判空)
- 编号列表,逐条写具体改动:删了什么文件/新增什么文件、核心数据结构与并发协议、对外接口/命名变更、伴生修复、测试与 fuzzer 更新。
- 每条写"变更了什么",不重复"为什么"。
- 末尾附测试结果(套件名 + 通过数)。

两段都要与 squash 后的提交信息一致——提交信息是英文事实来源,这两段是它的中文 PR 视图。
