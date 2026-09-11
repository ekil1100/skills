# ArkSteed JIT 性能测试（JIT-Bench，按需执行）

性能测试使用独立项目 **[JIT-Bench](https://gitcode.com/chaoxx/JIT-Bench)**。只执行任务要求的引擎、架构、用例和对比范围；全量功能测试、提 PR 或编写性能测试说明不会自动授权性能测量。JIT-Bench 的正确性套件也不能替代本 skill 的内部/external 全量功能测试。

本文依据 JIT-Bench commit `02307560c9eab7dccc1d47432135d8bc1fc76721` 的 README、运行指南、度量方法、tier 配置和 CLI/compare 源码核对。执行时先阅读所用 checkout 的 `README.md`、`docs/TESTING.md`、`docs/METHODOLOGY.md`、`config/tiers.json`，用 `./jb-py --help` 核对接口；用例数量和支持范围以该版本的 `list`、spec 和实现为准，不沿用旧文档中的数量。

## 1. 准备独立 checkout 与 Release 引擎

- 默认将 JIT-Bench 放在 `$ark_root/JIT-Bench/`，与 `arkcompiler/` 同级。目录不存在时从 `https://gitcode.com/chaoxx/JIT-Bench.git` 克隆；已存在时先核对 origin、HEAD 和未提交修改，不覆盖或自动更新。用户指定的路径与版本优先；记录完整 commit，本轮前后对比保持 benchmark 版本不变。
- JIT-Bench 只需 Python 3，不依赖第三方 Python 包；它使用**已构建**的引擎，不代替 ArkSteed 构建。确认产物与当前源码、架构、Release 模式及 `ets_runtime_enable_ark_steed=true` 配置匹配，尤其是实际加载的 `libark_jsoptimizer.so` 和 `libark_jsruntime.so`。只更新 `ark_js_vm` 不保证优化器库已更新。特殊用例的附加依赖以 spec 和 `run` 预检为准，例如声明 `arkAotProfile` 的用例还需要 `ark_aot_compiler`。
- 正式性能数据来自同一台机器上的原生执行。Debug、QEMU、强制 GC 或额外诊断配置只能用于对应的诊断实验，不作为默认 Release 性能基线；不自动运行功能测试的四配置矩阵。

以下准备命令从 **ets_runtime checkout 根目录**开始；此后所有 `./jb-py` 命令均在 **JIT-Bench checkout 根目录**执行：

```bash
# Start at the ets_runtime checkout root; prepare JIT-Bench first.
ark_root="$(cd ../.. && pwd)"
cd "$ark_root/JIT-Bench"
git remote get-url origin
git rev-parse HEAD
git status --short
./jb-py env --ark-root "$ark_root" --ark-mode x64.release
```

检查并设置 `env` 输出中的环境变量，再执行预检。`--ark-root` 指包含 `ark.py`、`arkcompiler/`、`out/` 的根目录，不是 `ets_runtime/`；`env` 输出未做 shell 转义，路径含空格或 shell 元字符时逐项用引号设置，不能直接 `eval`。非标准产物目录按上游运行指南配置 `JITBENCH_ARK_JSVM`、`JITBENCH_ES2ABC` 和 `JITBENCH_ARK_LIB_PATHS`；后者应包含优化器库所在目录，避免加载旧库。持久配置使用被 Git 忽略的 `config/engines.local.json`。

```bash
./jb-py doctor --engine ark-jsvm
```

`doctor` 通过只代表预检通过；共享库完整性和目标函数确实进入 JIT 仍需实际用例及 tier evidence 确认。预检失败时报告阻塞，不擅自更换引擎、关闭验证或修改运行时代码。

## 2. 按优化影响面选择范围，先预览再采样

先阅读本次 diff、优化项对比报告及相关实现，判断优化了哪些**字节码或模块**，再选择性能用例：

1. **局部影响**：将受影响的字节码、操作或模块映射到 JIT-Bench 用例，阅读 spec 与热点源码，必要时用单独的诊断运行核对字节码或路径命中。选择能覆盖优化路径及相关回退场景的用例，不只按用例名称猜测关联性。
2. **广泛影响或范围不确定**：涉及通用 CFG 优化、寄存器分配、代码生成、公共 PGO/IC 或运行时路径等，且无法用聚焦集合可靠覆盖时，回退测试目标引擎和架构下的**整个 performance 测试套**。找不到相关用例不等于没有影响，应记录覆盖缺口；用户明确要求全套时直接选择全套。
3. **固定范围并记录依据**：采样前列出“优化对象 → 相关用例 → 覆盖理由”，说明采用聚焦集合或回退全套的原因；四组数据沿用同一目标用例清单，不按测试结果删选用例。用户显式限定引擎、架构或用例时以其限制为准，说明未覆盖范围，不擅自突破限制。

范围确定后，先用 `list` 确认匹配项，再给实际运行命令追加 `--plan` 查看用例、进程数及预算。`list` 和 `--plan` 不启动引擎，不能作为测试通过证据。

```bash
./jb-py list --engine ark-jsvm --suite performance \
  --case performance/micro/loop_sum

./jb-py run --engine ark-jsvm --suite performance \
  --case performance/micro/loop_sum \
  --tier interpreter,optimized --repetitions 7 \
  --out ark-loop-sum --plan
```

确认计划符合授权范围后，移除 `--plan` 执行，其余参数保持一致。把示例用例替换为本次优化对应的热点，不用一个 micro 用例代表整个 JIT 性能。

- `--case` 精确匹配完整 ID；`--match` 是 ID 前缀，例如 `performance/micro/`。批量性能命令显式保留 `--suite performance`，避免误入正确性模式。
- 不带其他过滤器的 `--suite performance` 包含 `kind=performance|both`，可能运行 `correctness/` 下的双用途用例。影响面要求回退全套或用户要求全量性能测试时，去掉聚焦过滤器，使用 `--suite performance`，不改用还会执行正确性套件的 `--suite all`。`--profile cross-engine` 选择跨引擎负载并排除消融和 Ark no-OSR 专项，不等于全套；核对两侧共有用例与引擎白名单。全套中的 Ark 专用用例仍保留 Ark 测量，对侧不支持的 CSV 单元格标为 `N/A`，不能只跑共有子集就宣称全套完成。
- `--repetitions` 是独立进程次数，不是进程内循环次数。冒烟可用 3 次，正式回归至少 7 次；正式 Micro 基线另按第 5 节协议执行。先固定采样协议，不按结果好坏挑选样本。
- A/B 和跨引擎测量保持同机同核、相同驱动、预热、工作量和采样参数；固定各引擎的实际 flags，仅改变实验设计中的变量，记录 governor、频率、温度、供电与后台负载。需要绑核时使用环境确认可用的 CPU，例如在实际命令前加 `taskset -c "$cpu"`。容器/远程环境另核对引擎实际 cpuset，宿主 affinity 不能替代该证据。
- 与构建及其他压测错开执行，不自动终止其他任务或更改系统电源策略。正式数据不额外开启 trace、heap verify 等诊断开销；诊断采样单独标记。

## 3. 核对 tier 与驱动口径

| 配置 | 可以回答的问题与限制 |
|---|---|
| Ark `interpreter,optimized` | 解释器与强制编译后稳态性能；`optimized` 启用 ArkSteed JIT + LiteCG、阈值 1，预热后在同一测量进程中请求编译并检查机器码 |
| Ark `interpreter,default` | 默认 hotness threshold 下的端到端表现；`default` 不强制编译，evidence 为 `unverified`，不能据此声称目标已进入 JIT |
| V8 `interpreter,optimized` | Ignition-only 与 Maglev 封顶；V8 同名 tier 只是角色对齐，不表示与 Ark 实现或成本等价 |
| `--no-osr` | 改变 workload 驱动，用 `loopCalls(hot)` 反复调用热点；不是单纯给引擎设置禁用 OSR 的 flag，也不是所有用例都支持 |

- Ark `optimized` 的有效证据是计时前 `ArkTools.arkSteedIsCompiled(target)` 确认目标持有 ArkSteed 机器码；`arkSteedCompileSync()` 接受请求、速度变快或日志出现 compiled 都不能单独替代。保留 LiteCG 配置，它影响 heap constant/反馈特化能力，不只是额外 tier 开关。
- 检查每个 tier 的 `performanceValidation`、`tierEvidence`、原始进程样本、错误和超时。`[INVALID TIER]`、未配置 oracle、缺失指标不能当成有效优化收益。`--no-verify-tier` 仅用于诊断；对 Ark optimized，它还会取消强制编译，改变实验口径。
- 研究调用计数触发或无 OSR 场景时才选择 `--no-osr`，先检查 spec 是否导出 `hot` 及是否设置 `noOsrIterations`。未导出 `hot` 的用例会回退默认 driver；不能宣称整套都采用了 loop-calls。两侧保持相同模式，不混比 direct 与 no-osr 数据。
- 动态 `--no-osr` 返回 loop-calls accumulator，跳过原 `run` 的业务 `validate`；跨 tier 的 `valueRepr` 一致仅证明该 hot 路径的可观察结果一致，不能代替 direct 模式业务 oracle。spec 显式设置 `performance.driver=loop-calls` 时则需要专用 `validate()`，与动态切换区别对待。

## 4. 版本回归与 ArkSteed/V8 对比

默认采集第 5 节 CSV 所需的四组数据：Ark 解释器（`itp`）、V8 Maglev、ArkSteed 优化前（`before`）和优化后（`after`）。用户明确限定引擎或版本时只执行指定范围；缺失基线或环境受阻时如实记录，按 CSV 规则保留 `N/A`，不伪造或混用不可比的历史数据。

**版本回归**：在修改前后分别使用匹配源码的产物，运行同一组选定命令，仅将结果标签改为 `before`、`after`；保留两份产物的版本和哈希。不要为采集基线覆盖脏工作区或混用前后运行库。

```bash
./jb-py compare \
  --a 'results/before-performance-<timestamp>.json' \
  --b 'results/after-performance-<timestamp>.json' \
  --out results/before-vs-after.md
```

将占位路径替换为实际的单个 JSON 文件，不使用可能展开为多份历史结果的通配符。当前 `compare` 会对同引擎 binary SHA-256 变化给出不可用于 A/B 结论的警告，对 Ark runtime 库变化给出不能只归因于 optimizer 的警告；保留并解释这些限制，不能仅凭比较表有数字就宣称回归验证通过。

**跨引擎对比**：上述默认四组数据包含 Maglev；用户明确限定为 Ark-only 时跳过 V8，并说明缺失列。设置 `JITBENCH_D8` 为已确认的 d8 路径，通过 `./jb-py doctor --engine v8-d8 --probe-ignition-only` 核对实际 binary 支持当前 Ignition-only flags 且保留 RegExp native code。下面是同时比较两侧解释器加速比的跨引擎 profile 示例；仅采集 CSV 的 Maglev 基线时，V8 的 `--tier` 使用 `optimized` 即可。聚焦任务继续追加相同的 `--case` 或 `--match`，两条实际命令也先追加 `--plan` 检查范围：

```bash
./jb-py run --engine ark-jsvm --suite performance --profile cross-engine \
  --tier interpreter,optimized --repetitions 7 --out ark-cross
./jb-py run --engine v8-d8 --suite performance --profile cross-engine \
  --tier interpreter,optimized --repetitions 7 --out v8-cross
./jb-py compare \
  --a 'results/ark-cross-performance-<timestamp>.json' \
  --b 'results/v8-cross-performance-<timestamp>.json' \
  --out results/ark-vs-v8.md
```

按需对两侧同时增加 `--no-osr`；产物名会带 `-noosr`，以 runner 实际输出路径为准。相同 ID 还需 `workloadHash` 一致。报告绝对耗时；采集了两侧解释器数据时，另报告各引擎相对自身解释器的加速比，避免将解释器差异混同为 JIT 代码质量差异。跨引擎收益达成率是 `(Ark interpreter / Ark optimized) / (V8 interpreter / V8 optimized)`，不是 Ark/V8 绝对速度比。

## 5. 结果判读与交付

- 原始结果为 `results/<label>-performance-<timestamp>.json` 和同名 Markdown；JSON 保存进程样本和指纹，比较使用 JSON，不从 Markdown 或控制台四舍五入值重新计算。
- `medianMs` 是独立进程 median 的 median，越低越好；JIT 加速比为 `interpreter / optimized`，越高越好。`compare` 的耗时比为 **B/A**：小于 1 表示 B 更快；bootstrap 95% CI 完全低于/高于 1 才判方向，覆盖 1 是不可判，不是已证明无回退。CI 不消除系统性偏差。
- 逐项报告高波动、失败、超时、缺失验证、无效 tier、排除原因和环境警告。生成报告或退出码 0 不等于性能无回退；样本不足、范围未覆盖或条件不可比时明确标为未验证/探索性结果。
- 稳态、冷启动、编译和内存分开解读。Ark 冷启动 wall time 包含 `es2abc`，不能直接等同于 V8 VM 启动耗时；RSS 不是 JS heap。
- **仅在要求正式 Micro 基线时**，按上游 `docs/TESTING.md` 的分批与验收协议运行：核对当前 canonical 用例集合，固定同机同核和采样条件；共享服务器逐 case 紧邻测量 Ark/V8。互斥分批先分别 `merge`，再执行 `baseline-check`。本文核对版本为 43 个 Micro、每 tier 恰好 11 个独立进程：

```bash
./jb-py baseline-check \
  --ark results/ark-micro-43.json --v8 results/v8-micro-43.json \
  --expected-count 43 --repetitions 11 \
  --out results/micro-43-acceptance.md
```

该门禁退出 0 后才能发布正式逐项结果、几何平均及 85% 差距清单；任何排除项、缺失条件或验收失败均不能算正式基线通过。用例集合变化时重新核对协议，而不是降低门禁或排除失败用例。

### CSV 汇总与打印

除 JIT-Bench 原始输出和比较报告外，每次性能测试都额外生成一个 **UTF-8 CSV 文件**，保存为 `results/<label>-summary.csv`。每行对应第 2 节目标清单中的一个完整用例 ID，表头固定为：

```csv
case,itp,maglev,arksteed before,arksteed after,itp vs arksteed,maglev vs arksteed,before vs after
```

前四个数据列填写原始 JSON 中的有效 `medianMs`，统一单位为 **ms**，单元格只写数值：

- `itp`：与 `after` 同版本、同运行库的 Ark `interpreter`，不是 V8 Ignition；若解释器或公共运行时也有改动，在报告中说明其对比较的影响。
- `maglev`：V8 `optimized`（Maglev 封顶）。
- `arksteed before` / `arksteed after`：优化前 / 后的 Ark `optimized`。

后三列均表示 **ArkSteed after 相对对应基线的耗时变化百分比**，不使用加速倍数或收益达成率：

| 列名 | 计算公式 |
|---|---|
| `itp vs arksteed` | `(arksteed after / itp - 1) × 100%` |
| `maglev vs arksteed` | `(arksteed after / maglev - 1) × 100%` |
| `before vs after` | `(arksteed after / arksteed before - 1) × 100%` |

用未四舍五入的原始数值计算，输出保留两位小数并带 `%`；**正值为劣化，负值为优化**，正值显式带 `+`，零写为 `0.00%`。例如基线 100 ms、after 110 ms 为 `+10.00%`，after 90 ms 为 `-10.00%`。百分比仅是点估计，是否能判定方向仍遵循本节的置信区间与可比性要求。

逐用例核对四组数据的 ID、`workloadHash`、驱动、工作量与采样口径。未执行、不支持、失败、超时、无效 tier 或缺失有效指标时，相应耗时及依赖它的比较列填 `N/A`；分母为零或条件不可比时，比较列同样填 `N/A`。在报告中逐项说明原因，保留该用例行，不用 `0` 代替缺失值或删掉失败用例。

交付时给出 CSV 保存路径，并在最终回复中用 `csv` 代码块**完整打印文件内容**，不能只给路径或摘要。即使测试未全部完成，也输出已有结果和 `N/A`，明确未完成范围；CSV 不替代 JIT-Bench 的原始证据和验收门禁，门禁未通过时标为非正式结果。

交付注明 JIT-Bench/Ark/V8 版本与 dirty 状态、实际 binary/运行库哈希、构建配置、机器和测量条件、完整命令与退出码、选择/执行/有效/失败/未执行数量，以及原始 JSON、Markdown、比较报告、CSV 和日志路径。说明用例筛选或回退全套的依据、性能结论适用的范围、证据限制和剩余问题，不把本次性能测试扩展为全量功能测试通过。
