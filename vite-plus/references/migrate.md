# 迁移与升级

## 1. 修改前读取

- https://viteplus.dev/guide/migrate
- https://viteplus.dev/guide/migrate-rules
- https://viteplus.dev/guide/vitest-v5
- https://vitest.dev/guide/migration/
- https://github.com/rolldown/tsdown/releases/tag/v0.23.0

读取报告中涉及的进一步文档链接。核对根目录及各包的脚本、Vite/Vitest/tsdown、lint、格式化、任务缓存、钩子、CI 和测试配置，记录迁移前验证结果，便于区分已有失败和新增失败。

## 2. 按现状准备

### 尚未使用 Vite+

对实际使用的工具检查前置条件：Vite 8+、Vitest 4.1+。低于要求时先完成上游升级并验证，通过后才开始 Vite+ 迁移。未使用的工具无需为了满足版本门槛而安装。

保留完成前置升级后的 manifests、锁文件和已安装依赖，供 migrator 识别原 Vitest 版本及 peer 元数据。迁移前不安装 `vite-plus`，也不提前将 Vitest 升到目标 Vite+ 捆绑版本。

### 已使用 Vite+

使用默认升级流程，保留现有设置。除非用户明确要求完整设置，否则不用 `--full`，也不因 CLI 提示而自行扩大范围。

保留原 manifests、锁文件和已安装包；迁移前不手动升级项目的 `vite-plus` 或 Vitest。原依赖无法识别时先恢复可识别的原始安装状态；保护无关修改，不用整库回滚解决。

## 3. 运行目标迁移器

从工作区根目录执行，使 manifests、catalogs、overrides 与锁文件保持一致。使用主文件选定的目标发布版或预览版 CLI，不能使用旧项目的 `node_modules/.bin/vp`。

有已核对版本的全局 CLI：

```sh
vp help
vp help migrate
vp migrate --no-interactive
```

无全局 CLI，以下两组二选一；替换 `1.0.0` 为目标精确版本：

```sh
pnpm dlx --package=vite-plus@1.0.0 vp help migrate
pnpm dlx --package=vite-plus@1.0.0 vp migrate --no-interactive
```

```sh
npx --package=vite-plus@1.0.0 vp help migrate
npx --package=vite-plus@1.0.0 vp migrate --no-interactive
```

预览版从 PR 获取版本，并按主文件规则添加 registry 参数。这些命令临时取得目标迁移器，不先替换项目旧依赖。

保存完整迁移报告：后续重跑可能因源版本已改变，不再重复原来的诊断。

- 解决每个 `BLOCK` 后重新运行迁移。
- 逐条读取每个 `REVIEW` 及手动迁移警告的文档链接，核对具体文件；成功退出并不表示无需跟进。
- 保留生成的 `Vitest v4 compatibility` 和 `tsdown <0.23 compatibility` 设置及注释，直至首轮验证通过。

## 4. 审查结果

对照目标版本迁移规则逐项检查，不做全库字符串替换：

- 配置入口和测试 API 使用受支持的 `vite-plus`、`vite-plus/test*` 入口。Vite 非配置源码、插件包和 Nuxt 测试集成等例外按规则保留上游身份；不要假定 `vite-plus` 导出所有 Vite API。
- 类型增强保留上游模块身份，包括 `declare module 'vitest'`、`'vite'` 和浏览器模块；确需增强 Vite+ 自有 API 的情况另行判断。
- 社区 WebDriverIO provider 使用 `@vitest/browser-webdriverio`，运行时浏览器 API 按指南使用共享入口；核对 provider、框架 peer 和相关 overrides。
- 保留迁移器配置的依赖、aliases、catalogs、overrides/resolutions。在 pnpm 上保留配置好的 `vite` 和 `vitest` 条目；既不盲删，也不向所有包盲加。peer、类型或解析引用等要求保留上游包时照规则保留。
- 将剩余工具配置迁入 `vite.config.ts` 对应块，保留项目特有语义。已有 Vite+ 的默认升级不擅自补跑完整 lint、格式化、钩子、编辑器或代理设置；按需报告另行授权的工作。
- 检查动态配置、共享配置、任务缓存和打包选项的手动警告；保留包装命令、脚本参数、环境变量、额外构建步骤与钩子行为。
- 核对 CI、容器中的运行时和产物路径。迁移器不一定检测它们；也不能为了测试运行时升级而无依据地提高库对消费者声明的 Node 最低版本。

## 5. 验证后再清理

执行主文件的全部适用验证，先获得兼容设置完整保留时的通过基线。失败时修复迁移问题，不弱化测试断言、覆盖率要求或声明检查。

通过后进入 [兼容配置清理](compatibility-cleanup.md)。无法建立基线则保留兼容设置，列出失败及未验证项，不开始清理试验。
