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

**arksteed** - 开发、优化、调试和检视 ArkSteed JIT 编译器，包含源码对照、实现报告、按需功能测试及 JIT-Bench 性能测试。远端构建或测试配合 `ets-runtime-remote-build` 使用。

```shell
npx skills add https://github.com/ekil1100/skills --skill arksteed -y -g
```

**ets-runtime-remote-build** - 在 SSH 主机 `work` 对齐精确提交、自动 stash 并按 Git 文件清单镜像源码，执行构建或测试，支持 ARM64/QEMU 环境检查；默认跳过 `ark sync`。

```shell
npx skills add https://github.com/ekil1100/skills --skill ets-runtime-remote-build -y -g
```
