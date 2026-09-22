# ArkSteed JIT 性能测试（JIT-Bench，按需执行）

性能测试使用独立项目 **[JIT-Bench](https://gitcode.com/chaoxx/JIT-Bench)**。只执行任务要求的引擎、架构、用例和对比范围；全量功能测试、提 PR 或编写性能测试说明不会自动授权性能测量。JIT-Bench 的正确性套件也不能替代本 skill 的内部/external 全量功能测试。

性能口径原依据 JIT-Bench commit `02307560c9eab7dccc1d47432135d8bc1fc76721` 的 README、运行指南、度量方法、tier 配置和 CLI/compare 源码；本次输出接口另按本地 commit `d5f2f08e4126e2878051a68e83a4dcf22be7209c` 的实际实现核对（证据见下）。执行时先阅读所用 checkout 的 `README.md`、`docs/TESTING.md`、`docs/METHODOLOGY.md`、`config/tiers.json`，用 `./jb-py --help` 核对接口；用例数量和支持范围以该版本的 `list`、spec 和实现为准，不沿用旧文档中的数量。

## 持久输出接口与限制

遵循主文档的[运行历史与输出目录](../SKILL.md#运行历史与输出目录)。本 skill 独立给出以下性能测试方法与命令；需要自动记录时，使用本 skill 自带的 `$history_runner`（`scripts/run_with_history.py`）：`python3 "$history_runner" --repo "$runtime_root" --command '<命令体>'`。如果执行环境已经提供持久日志与结果目录，直接执行相同命令体，不要求额外包装。**下面独立的 `./jb-py` shell 块均在 JIT-Bench 根目录、已设置持久结果目录的环境中执行。** 跨轮比较使用原始 JSON 的已确认绝对路径，派生报告写本轮目录，不改写旧记录。

- `run --out` 实际是**文件名前缀**，不是目录参数。传已存在持久目录内的**绝对路径前缀**，如 `--out "$ARK_RESULTS_DIR/ark-loop-sum"`；输出为此前缀加 `-performance[-noosr]-<timestamp>.json/.md`。不要传相对 `results/...`，不要只改 cwd 或设置 `TMPDIR`，也不先写 benchmark checkout 再搬运。
- 绝对前缀可行的原因：CLI 原样传 `label`，报告器拼出带时间戳文件名，再调用 `os.path.join(OUT_DIR, absolute_name)`，绝对路径会取代 `OUT_DIR`。报告器只自动创建默认 `OUT_DIR`，不会为此前缀创建父目录；运行前确保 `$ARK_RESULTS_DIR` 已存在，自建子目录时显式 `mkdir -p`。它仍可能创建 checkout 的空 `results/`，但 JSON/Markdown 直接写指定的持久路径，不把该空目录当历史。
- `compare --out`、`merge --out`、`baseline-check --out` 是各自的输出路径/前缀，均使用 `$ARK_RESULTS_DIR` 下的绝对路径；CSV 同理。四组采样、诊断、比较可分属不同运行记录，交付时逐一链接，不假定共享当前 `ARK_RESULTS_DIR`。
- 原生 `transport=local` 的 Ark 后端使用 Python `tempfile`，因此记录器在进程启动前提供的可写 `TMPDIR=$ARK_RESULTS_DIR/tmp` 对 staging 有效。普通运行会清理 staging，它是编译中间文件而非最终历史；需要保留诊断 staging 时使用已核实的 `--debug`，并与正式性能数据分开。`work` 上 SSH 启动的原生进程仍属此 `local` transport。
- Docker 后端的容器 staging 默认硬编码 `/tmp/jitbench`，宿主 `TMPDIR` 不会改变它；本指南的持久路径示例不覆盖容器运行。未核实容器 `containerWorkDir`、持久挂载和清理行为前停止容器测量，报告阻塞，不自动切换 transport。
- **中断/原始日志限制**：报告器在整轮结束才保存 JSON/Markdown，进程样本此前在内存中；异常退出或强制中断可能只有外层持久日志，没有样本 JSON。引擎 stdout/stderr 在内部通过 PIPE 捕获，最终只保留 `stderrTail` 等摘要，外层 `logs/command.log` 不是完整引擎日志。不得声称失败时所有样本/引擎日志均已保存；若任务要求逐样本抗中断保存或完整引擎日志，则当前阻塞，需要上游新增逐样本增量写盘及子进程日志直写接口。失败记录仍保留并注明缺失，不用 `--debug` 冒充此接口。

源码证据（本次只读核查，未运行引擎）：本地 `/home/like/ohos/a2/JIT-Bench`，commit `d5f2f08e4126e2878051a68e83a4dcf22be7209c`，工作区干净。

| 文件与行号（相对该 checkout） | 核实事实 |
|---|---|
| `harness/cli.py:450–453,471–491,745` | `--plan` 在启动引擎前返回；`--out` 原样传给 `write_report(label=...)`，整轮完成才写报告。 |
| `harness/result.py:14,375–387,416–420` | 默认目录与绝对前缀拼接；JSON/Markdown 首次落盘路径。 |
| `harness/cli.py:668–698,713–720` | compare/merge/baseline-check 输出路径及父目录创建。 |
| `harness/backend_ark.py:201–209,242–285,294–296` | 本机 tempfile、stdout/stderr PIPE、普通 staging 清理；容器独立 staging。 |
| `harness/runner.py:181–197` | 保存 `stderrTail`，不是完整 stdout/stderr。 |
| `/usr/lib/python3.10/tempfile.py:295–317`（本机 Python 标准库） | 临时目录候选先读取 `TMPDIR`；不能推广到硬编码 `/tmp` 的其他 runner。 |

## 1. 准备独立 checkout 与 Release 引擎

- 默认将 JIT-Bench 放在 `$ark_root/JIT-Bench/`，与 `arkcompiler/` 同级。目录不存在时从 `https://gitcode.com/chaoxx/JIT-Bench.git` 克隆；已存在时先核对 origin、HEAD 和未提交修改，不覆盖或自动更新。用户指定的路径与版本优先；记录完整 commit，本轮前后对比保持 benchmark 版本不变。
- JIT-Bench 只需 Python 3，不依赖第三方 Python 包；它使用**已构建**的引擎，不代替 ArkSteed 构建。确认产物与当前源码、架构、Release 模式及 `ets_runtime_enable_ark_steed=true` 配置匹配，尤其是实际加载的 `libark_jsoptimizer.so` 和 `libark_jsruntime.so`。只更新 `ark_js_vm` 不保证优化器库已更新。特殊用例的附加依赖以 spec 和 `run` 预检为准，例如声明 `arkAotProfile` 的用例还需要 `ark_aot_compiler`。
- 正式性能数据来自同一台机器上的原生执行。Debug、QEMU、强制 GC 或额外诊断配置只能用于对应的诊断实验，不作为默认 Release 性能基线；不自动运行功能测试的四配置矩阵。

以下准备命令从 **ets_runtime checkout 根目录**开始；此后所有 `./jb-py` 命令均在 **JIT-Bench checkout 根目录**执行：

```bash
# Start at the ets_runtime checkout root; prepare JIT-Bench first.
runtime_root="$(git rev-parse --show-toplevel)"
: "${history_runner:?Set the absolute path to the bundled run_with_history.py}"
python3 "$history_runner" --repo "$runtime_root" --command '
  set -eu
  ark_root="$(cd ../.. && pwd)"
  cd "$ark_root/JIT-Bench"
  git remote get-url origin
  git rev-parse HEAD
  git status --short
  ./jb-py env --ark-root "$ark_root" --ark-mode x64.release
'
```

检查并设置 `env` 输出中的环境变量，再执行预检。`--ark-root` 指包含 `ark.py`、`arkcompiler/`、`out/` 的根目录，不是 `ets_runtime/`；`env` 输出未做 shell 转义，路径含空格或 shell 元字符时逐项用引号设置，不能直接 `eval`。非标准产物目录按上游运行指南配置 `JITBENCH_ARK_JSVM`、`JITBENCH_ES2ABC` 和 `JITBENCH_ARK_LIB_PATHS`；后者应包含优化器库所在目录，避免加载旧库。持久配置使用被 Git 忽略的 `config/engines.local.json`（这是配置，不是运行历史）。每次记录器命令从 ets_runtime 根目录开始，命令体须先 `cd "$(cd ../.. && pwd)/JIT-Bench"`；所需引擎环境变量在命令体内明确设置，不能依赖前一条命令的 shell 状态。

```bash
./jb-py doctor --engine ark-jsvm
```

`doctor` 通过只代表预检通过；共享库完整性和目标函数确实进入 JIT 仍需实际用例及 tier evidence 确认。预检失败时报告阻塞，不擅自更换引擎、关闭验证或修改运行时代码。

## 2. 默认聚焦选择，冻结清单后采样

**默认执行聚焦集合；只有用户明确要求或确认全量性能测试时，才选择整个 performance 测试套。** “测一下性能”“检查性能回归”、影响面广、筛选无匹配或证据不足，都不构成全量授权。筛选失败时保留限制并报告缺口，不通过移除过滤器继续执行。

按以下顺序确定范围：

1. **锁定输入**：记录用户指定的引擎、架构、用例及 JIT-Bench commit；固定本次优化的 Git 比较点，阅读对应 diff、优化项对比报告及相关实现。分支累计优化包含基线以来的提交和工作区改动，不能仅凭空的工作区 diff 判断无影响；无法确定比较点时先询问。
2. **映射实际变化**：按改变的字节码、操作或执行路径选择，而不是按修改文件所属模块扩大范围。先用 `list` 查候选，再阅读 spec 与热点源码，记录“优化对象 → 完整用例 ID → 热点位置及覆盖理由”。名称、目录或标签只能用于发现候选，不能单独作为命中证据；需要动态证据时另做诊断，不混入正式采样。
3. **形成最小覆盖集合**：用户已指定用例时按指定范围执行，缺口只报告；未指定时，先覆盖每条已识别的优化路径，再补充与本次改动相关的边界或回退场景。多个候选覆盖相同路径时，优先选热点直接、无关工作量少的用例；仍等价时按完整 ID 排序选取。通用 CFG、寄存器分配、代码生成或公共 PGO/IC 改动也按具体改变的机制挑选代表用例，不因模块名称自动升级全套。聚焦集合只支持对应路径的结论，不代表全局无回退。
4. **处理缺口并冻结清单**：部分路径有证据时可运行已覆盖的聚焦集合，逐项列出未覆盖路径；零匹配、全部候选缺少关联证据或影响机制仍不清楚时，停止采样，向用户确认目标用例、补充用例或是否扩大范围。采样前在规划命令的 `$ARK_RESULTS_DIR/selected-cases.md` 保存比较点、benchmark 版本、排序去重后的完整 ID 清单、逐项依据及覆盖缺口；采样记录引用该不可变清单，累计报告链接其路径。四组数据沿用这份清单，不按结果删选用例；复跑沿用原清单，输入变化时重新核对并说明差异。

| 场景 | 范围决策 |
|---|---|
| 指定两个用例，其中一个不存在 | 报告无效 ID，确认修正；保留有效项，不换成全套 |
| 局部字节码优化有直接命中的热点 | 选择直接命中与相关边界用例 |
| 修改寄存器分配，已识别受影响的溢出或调用机制 | 选择覆盖该机制的代表用例，注明非全局验证 |
| 搜不到候选，或仅凭名称无法确认关联 | 停止采样并报告缺口，确认后再继续 |
| 用户要求整个 performance 测试套 | 在其指定的引擎、架构内执行全套 |

范围确定后，先用 `list` 确认匹配项，再给实际运行命令追加 `--plan` 查看用例、进程数及预算。**同一引擎全部分批计划的 ID 并集应等于冻结清单在该引擎支持范围内的集合**；零匹配、多出用例或意外缺项时停止并修正筛选，不启动引擎。引擎不支持的项留在总清单中并记为 `N/A`。`list` 和 `--plan` 不启动引擎，不能作为测试通过证据。

```bash
# Run from the ets_runtime root; engine environment must already be configured.
: "${history_runner:?Set the absolute path to the bundled run_with_history.py}"
runtime_root="$(git rev-parse --show-toplevel)"
python3 "$history_runner" --repo "$runtime_root" --command '
  set -eu
  cd "$(cd ../.. && pwd)/JIT-Bench"
  : "${ARK_RESULTS_DIR:?Missing durable results directory}"
  mkdir -p "$ARK_RESULTS_DIR" "$TMPDIR"
  ./jb-py list --engine ark-jsvm --suite performance \
    --case performance/micro/loop_sum
  ./jb-py run --engine ark-jsvm --suite performance \
    --case performance/micro/loop_sum \
    --tier interpreter,optimized --repetitions 7 \
    --out "$ARK_RESULTS_DIR/ark-loop-sum" --plan
'
```

确认计划符合授权范围后，在新的运行记录中移除 `--plan` 执行，其余采样参数保持一致，输出进入本轮的持久目录。把示例用例替换为本次优化对应的热点，不用一个 micro 用例代表整个 JIT 性能。

- `--case` 精确匹配完整 ID；聚焦执行优先逐 ID 使用 `--case`，多个 ID 的批量写法先核对当前 CLI 是否支持。`--match` 是 ID 前缀，例如 `performance/micro/`，仅在展开集合恰好等于冻结清单时使用。聚焦命令始终保留用例过滤器；批量性能命令显式保留 `--suite performance`，避免误入正确性模式。
- 不带其他过滤器的 `--suite performance` 包含 `kind=performance|both`，可能运行 `correctness/` 下的双用途用例。用户明确授权全套后，才去掉聚焦过滤器，使用 `--suite performance`，不改用还会执行正确性套件的 `--suite all`。`--profile cross-engine` 选择跨引擎负载并排除消融和 Ark no-OSR 专项，不等于全套；核对两侧共有用例与引擎白名单。全套中的 Ark 专用用例仍保留 Ark 测量，对侧不支持的 CSV 单元格标为 `N/A`，不能只跑共有子集就宣称全套完成。
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
  --a "${before_json:?Set the absolute before JSON path}" \
  --b "${after_json:?Set the absolute after JSON path}" \
  --out "${ARK_RESULTS_DIR:?}/before-vs-after.md"
```

将 `before_json` / `after_json` 设置为前后两次持久记录中实际的单个 JSON 文件绝对路径，不使用可能展开为多份历史结果的通配符。当前 `compare` 会对同引擎 binary SHA-256 变化给出不可用于 A/B 结论的警告，对 Ark runtime 库变化给出不能只归因于 optimizer 的警告；保留并解释这些限制，不能仅凭比较表有数字就宣称回归验证通过。

**跨引擎对比**：上述默认四组数据包含 Maglev；用户明确限定为 Ark-only 时跳过 V8，并说明缺失列。设置 `JITBENCH_D8` 为已确认的 d8 路径，通过 `./jb-py doctor --engine v8-d8 --probe-ignition-only` 核对实际 binary 支持当前 Ignition-only flags 且保留 RegExp native code。下面是同时比较两侧解释器加速比的聚焦跨引擎 profile 示例；仅采集 CSV 的 Maglev 基线时，V8 的 `--tier` 使用 `optimized` 即可。将 `selected_case_id` 设置为冻结清单中的实际 ID，逐项运行；先确认 profile 未排除目标用例，两条实际命令也先追加 `--plan` 检查范围：

```bash
./jb-py run --engine ark-jsvm --suite performance --profile cross-engine \
  --case "${selected_case_id:?Set the frozen case ID}" \
  --tier interpreter,optimized --repetitions 7 --out "${ARK_RESULTS_DIR:?}/ark-cross"
```

另一组独立采样命令：

```bash
./jb-py run --engine v8-d8 --suite performance --profile cross-engine \
  --case "${selected_case_id:?Set the frozen case ID}" \
  --tier interpreter,optimized --repetitions 7 --out "${ARK_RESULTS_DIR:?}/v8-cross"
```

另开比较命令，并设置已确认的两份 JSON 绝对路径：

```bash
./jb-py compare \
  --a "${ark_cross_json:?Set the absolute Ark JSON path}" \
  --b "${v8_cross_json:?Set the absolute V8 JSON path}" \
  --out "${ARK_RESULTS_DIR:?}/ark-vs-v8.md"
```

每条采样命令单独记录、成功后再采集下一组；比较前从实际输出确定 `ark_cross_json` / `v8_cross_json`，不能预猜时间戳。按需对两侧同时增加 `--no-osr`；产物名会带 `-noosr`，以 runner 实际输出路径为准。相同 ID 还需 `workloadHash` 一致。报告绝对耗时；采集了两侧解释器数据时，另报告各引擎相对自身解释器的加速比，避免将解释器差异混同为 JIT 代码质量差异。跨引擎收益达成率是 `(Ark interpreter / Ark optimized) / (V8 interpreter / V8 optimized)`，不是 Ark/V8 绝对速度比。

## 5. 结果判读与交付

- 原始结果为对应命令 `$ARK_RESULTS_DIR/<label>-performance-<timestamp>.json` 和同名 Markdown；JSON 保存进程样本和指纹，比较使用 JSON，不从 Markdown 或控制台四舍五入值重新计算。
- `medianMs` 是独立进程 median 的 median，越低越好；JIT 加速比为 `interpreter / optimized`，越高越好。`compare` 的耗时比为 **B/A**：小于 1 表示 B 更快；bootstrap 95% CI 完全低于/高于 1 才判方向，覆盖 1 是不可判，不是已证明无回退。CI 不消除系统性偏差。
- 逐项报告高波动、失败、超时、缺失验证、无效 tier、排除原因和环境警告。生成报告或退出码 0 不等于性能无回退；样本不足、范围未覆盖或条件不可比时明确标为未验证/探索性结果。
- 稳态、冷启动、编译和内存分开解读。Ark 冷启动 wall time 包含 `es2abc`，不能直接等同于 V8 VM 启动耗时；RSS 不是 JS heap。
- **仅在要求正式 Micro 基线时**，按上游 `docs/TESTING.md` 的分批与验收协议运行：核对当前 canonical 用例集合，固定同机同核和采样条件；共享服务器逐 case 紧邻测量 Ark/V8。互斥分批先分别 `merge`，再执行 `baseline-check`。原性能口径核对版本为 43 个 Micro、每 tier 恰好 11 个独立进程；执行当前 checkout 前重核数量：

```bash
./jb-py baseline-check \
  --ark "${ark_merged_json:?Set the absolute merged Ark JSON path}" \
  --v8 "${v8_merged_json:?Set the absolute merged V8 JSON path}" \
  --expected-count 43 --repetitions 11 \
  --out "${ARK_RESULTS_DIR:?}/micro-43-acceptance.md"
```

该门禁退出 0 后才能发布正式逐项结果、几何平均及 85% 差距清单；任何排除项、缺失条件或验收失败均不能算正式基线通过。用例集合变化时重新核对协议，而不是降低门禁或排除失败用例。

### CSV 保存与 Markdown 表格展示

除 JIT-Bench 原始输出和比较报告外，每次性能测试都额外生成一个 **UTF-8 CSV 文件**，直接保存为本轮 `$ARK_RESULTS_DIR/<label>-summary.csv`。每行对应第 2 节目标清单中的一个完整用例 ID，表头固定为：

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

交付时给出 CSV 保存路径，并将同一份汇总数据在最终回复中展示为**完整的 Markdown 表格**：沿用 CSV 的八列表头、行顺序和单元格数值，包含目标清单的全部用例；表格前注明耗时单位为 ms、百分比正值为劣化、负值为优化。直接输出可渲染的 Markdown 表格，不放进代码块，不 raw print CSV，也不能只给路径或摘要。即使测试未全部完成，也展示已有结果和 `N/A`，明确未完成范围；CSV 与展示表格不替代 JIT-Bench 的原始证据和验收门禁，门禁未通过时标为非正式结果。

交付注明 JIT-Bench/Ark/V8 版本与 dirty 状态、实际 binary/运行库哈希、构建配置、机器和测量条件、完整命令与退出码、选择/执行/有效/失败/未执行数量，以及各组运行 ID、记录目录、原始 JSON、Markdown、比较报告、CSV 和日志的持久绝对路径（远端标明 `work:`）。已有 `.agents/` 累计报告链接这些历史，不覆盖原始结果。说明聚焦筛选依据或全套授权、冻结清单与实际执行的差异、性能结论适用的范围、证据限制和剩余问题，不把本次性能测试扩展为全量功能测试通过。
