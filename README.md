## Install all skills

```shell
npx skills add ekil1100/skills -g
```

---

**loop-review-fix** - review changes and fix it until no issue report.

```shell
npx skills add https://github.com/ekil1100/skills --skill loop-review-fix -y -g
```

**commit-push** - create semantic commit messages, commit selected changes, and push the current branch.

```shell
npx skills add https://github.com/ekil1100/skills --skill commit-push -y -g
```

**html-plan** - render an existing plan file into a self-contained HTML view, preserving the source file's structure instead of forcing fixed sections.

```shell
npx skills add https://github.com/ekil1100/skills --skill html-plan -y -g
```

**squash-pr** - squash the current branch into one Conventional Commit and generate PR-form "修改原因/修改描述" text from an issue link.

```shell
npx skills add https://github.com/ekil1100/skills --skill squash-pr -y -g
```

**oh-pr** - end-to-end OpenHarmony / GitCode PR workflow: squash local changes, create a new issue, then open a PR via the `oh-gc` CLI. Depends on `squash-pr`.

```shell
npx skills add https://github.com/ekil1100/skills --skill oh-pr -y -g
```

**d8-binary** - download, install, or update the prebuilt V8 `d8` developer shell from the official public GCS bucket.

```shell
npx skills add https://github.com/ekil1100/skills --skill d8-binary -y -g
```
