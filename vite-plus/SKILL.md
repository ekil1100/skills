---
name: vite-plus
description: 使用 Vite+（vp CLI）创建应用、库或工作区，将现有项目迁移到 Vite+，或升级已有 Vite+ 项目。用户要求“用 Vite+ 新建项目”“迁移到 Vite+”“升级 Vite+”或“改用 vp 工具链”时使用；仅使用普通 Vite、Vitest 或修改前端功能不代表要迁移工具链。
---

# Vite+

目标：使用统一的 `vp` 工具链，同时保留应用、测试、库产物和消费方行为。先识别意图，只执行匹配流程。

## 1. 读取文档与检查现场

先读取官方入口，了解当前命令与配置：

- https://viteplus.dev/llms-full.txt
- https://viteplus.dev/guide

全文过长时按目录读取目标流程涉及的章节，再打开对应专页。版本相关行为以目标版本的文档和 CLI 帮助为准；预览版同时查阅对应 PR。必要文档无法取得时，说明缺失依据，不猜测迁移参数。

检查 `git status` 和已有 diff，记录无关改动。确认目标目录、工作区根目录、包管理器、锁文件、脚本、配置、CI、运行时及现有工具版本。保留用户改动；避免全库格式化、重置或覆盖已有文件。未经要求不提交或推送，也不安装或升级全局工具。

根据用户意图及文件证据选择：

| 场景 | 流程 |
| --- | --- |
| 创建新应用、库、工作区，或在已有工作区增加新包 | 读取 [新建项目](references/create.md) |
| 现有项目尚未使用 Vite+ | 读取 [迁移与升级](references/migrate.md)，执行首次迁移分支 |
| 现有项目已使用 Vite+ | 读取 [迁移与升级](references/migrate.md)，执行升级分支 |

目录非空不等于需要迁移。无法确定目标、框架或是否要改变已有项目时，只询问影响操作的缺失信息。

## 2. 确定目标 CLI

记录精确目标版本、包管理器和 Node.js 运行时。用户指定版本优先；否则查证当前稳定版本，不将下列 `1.0.0` 示例视为永久默认值。预览版必须从对应 PR 获取精确版本及必要说明。

读取 https://viteplus.dev/guide/upgrade 和 https://viteplus.dev/guide/vitest-v5 的运行时兼容说明。检查本机实际 Node 版本，以及项目、CI 和容器的运行时约束；区分库对外声明的 Node 支持范围和开发测试运行时。

### 已有全局 CLI

先确认来源及版本。需要改变全局版本时取得用户同意，按升级指南选择目标版本，运行 `vp toolchain --global` 核对，再读取 `vp help` 和目标子命令帮助。全局与项目本地版本相互独立。

### 没有全局 CLI

优先使用包管理器临时获取目标 CLI，不先修改项目依赖。以下二选一，替换为目标精确版本：

```sh
pnpm dlx --package=vite-plus@1.0.0 vp help
npx --package=vite-plus@1.0.0 vp help
```

后续将末尾 `help` 换成 `help create`、`create …`、`help migrate` 或 `migrate --no-interactive`。帮助与执行使用同一个目标版本。

预览版将版本替换为 PR 给出的版本，并在包管理器参数中、`vp` 命令之前加入 `--registry=https://registry-bridge.viteplus.dev`。

只有用户选择全局安装时，才运行对应安装器：

```sh
curl -fsSL https://vite.plus | bash
```

```powershell
irm https://vite.plus/ps1 | iex
```

安装后打开新终端，再按升级指南选择目标版本并核对 `vp toolchain --global`；不要假设当前终端的 PATH 已更新。

## 3. 共同验证与交付

执行所选流程后：

1. 有全局 CLI 时运行 `vp install`；否则使用项目包管理器安装，再通过更新后的本地 CLI 验证，如 `pnpm exec vp check` 或 `npm exec -- vp check`。以下 `vp` 命令均按此方式调用。
2. 运行 `vp check`、`vp test`，以及已有的浏览器、覆盖率、基准测试。自动化环境根据帮助选择单次运行参数。记录测试集合、跳过情况和覆盖范围，不能用减少测试或弱化断言换取通过。
3. 应用运行 `vp build`，库运行 `vp pack`；混合工作区两者都运行，覆盖相关包。库还需检查消费方能否解析产物导入与类型声明。保留项目脚本中额外的构建步骤，不以裸内置命令替代其全部行为。
4. 测试不存在、环境缺失、依赖安装失败或命令无法执行时，明确标为未验证，不声称通过。
5. 迁移或升级取得通过基线后，读取 [兼容配置清理](references/compatibility-cleanup.md)，按其规则逐项处理；新项目不进入清理流程。

交付报告包含：所选流程及版本、修改文件和行为、验证命令与结果、未解决的迁移报告项、保留的兼容配置及原因。解释 `vp dev` 启动内置开发服务器，`vp run <task>` 执行项目脚本或任务；`vp test` 与 `vp run test`、`vp dev` 与 `vp run dev` 不等价。`packageManager` 字段决定 `vp install`、`vp add`、`vp remove` 使用的包管理器。
