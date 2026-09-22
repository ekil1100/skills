# ArkSteed 全量功能测试（按需执行）

**仅当用户明确要求全量功能测试时，才执行本文的测试流程；其他构建/聚焦测试任务只查阅输出接口检查，不因此扩大测试范围。** 提 PR、准备合入或 PR 模板包含自测试项，都不会自动触发测试；用户未要求时，不以未执行测试阻塞提 PR。仓库路径和命令均相对 **ets_runtime checkout 根目录**。

先读取当前 checkout 的 `ecmascript/arksteed/test/README.md`、`ecmascript/arksteed/unittests/README.md` 与 `BUILD.gn`。**功能测试包含原生 unittest、内部 JS/TS 和 external 三部分**，原生单测使用独立入口，不由 JS/TS runner 代跑。配置顺序为 **x64 Debug → x64 Release → ARM64 Release → ARM64 Debug**，每个配置均包含这三部分；用户限定配置子集时保持该相对顺序，只运行指定范围。**当前配置全部通过后才进入下一配置；任一用例失败或超时，当前配置结束后立即报告并返回，后续配置不再运行。** 本文只涉及功能测试，不自动启动性能测试。聚焦用例通过、只构建成功或仅检查对象文件，均不能报告为全量功能测试通过。

## 输出接口检查

按主文档的[运行历史与输出目录](../SKILL.md#运行历史与输出目录)准备持久记录，检查**本次实际执行端的工具**，不将某台机器或某个版本的支持/阻塞状态写成通用结论。本节规定需要核对的能力，不要求工具实现某个固定名称的参数。

| 入口 | 核对位置 | 需要确认的能力 |
|---|---|---|
| 内部 JS/TS | 当前 `ecmascript/arksteed/test/README.md`、runner 的 CLI 和产物目录创建/写入代码 | abc、IR/反汇编、stdout/stderr、元数据和汇总各自写到哪里；现有参数、配置或环境变量是否能将它们直接写入本轮持久目录。 |
| external | 当前 external checkout 的 README、runner CLI 和产物写入代码 | 独立核对输出能力，不套用内部 runner 的参数；`-R` 用于定位构建树，不等同于结果目录。 |
| 原生 unittest | 当前 `ecmascript/arksteed/unittests/BUILD.gn` → `test/test_helper.gni` 的 action → 实际执行脚本 | 完整 GTest 原始输出是否直接持久保存，或完整透出至记录器；成功、失败、超时路径均需核对。仅有成功摘要或共享 ZIP 副本不够。 |
| 构建 | 当前 `ark.py` 及其日志调用链 | 完整构建控制台能否由记录器保存；工具在 `out` 中保留附带副本不构成阻塞。 |

按检查结果处理：

1. **支持持久输出**：使用已核实的接口配置独立的本轮目录，再执行请求的测试。只需完整控制台即可保留全部原始结果时，可直接由记录器保存；另有原始产物时仍需核对其路径。
2. **尚未核实**：继续只读检查 README、CLI 和源码，不能把“没找到参数”直接判成不支持，也不启动测试来猜接口。
3. **不能满足保存要求**：记录缺失能力、源码依据、已保存的控制台路径和 `BLOCKED`，停止该测试入口；普通构建可独立执行。将工具改造作为建议提出，只有用户明确授权具体仓库修改后才处理。

记录器能保存控制台并提供 `ARK_RESULTS_DIR`、`TMPDIR`，不能改变工具硬编码的路径，也不能保存 action 未透出的输出。不要编造 CLI/GN 参数、靠结束后搬运唯一副本或修改全局软链接绕过检查。

目录存在或 `ninja: no work to do` 均不是本轮测试通过的证据；核对本轮实际执行和原始结果，不把增量缓存当作新验证。具体接口、源码版本和实际支持状态记在当次报告，不绑定本指南到本地补丁。

输出布局按已有接口的能力设置：优先在 `$ARK_RESULTS_DIR` 下按配置及 `internal/`、`external/`、`unittest/` 分目录；执行环境已有独立持久目录时记录实际路径。配置汇总写 `$ARK_RESULTS_DIR/config-summary.tsv`。工具原生元数据、汇总与外层运行记录分开保存。

## 1. 准备并确认 external 用例

external 默认使用用户指定的仓库：**https://gitcode.com/xing-yunhao-huawei/arksteed_external_tests**。克隆地址为 `https://gitcode.com/xing-yunhao-huawei/arksteed_external_tests.git`；用户或项目明确指定的其他来源/版本优先。

1. 准备独立 checkout，默认放在 `$ark_root/arksteed_external_tests/`，与 `arkcompiler/` 同级；`ark_root` 是包含 `arkcompiler/`、`out/`、`ark.py` 的目录。从 ets_runtime 根目录可用 `ark_root="$(cd ../.. && pwd)"` 取得。目录不存在时克隆；已存在时先核对 origin、HEAD 和未提交修改，不覆盖或自动更新。未指定版本时首次克隆使用默认分支当前 HEAD，记录完整 commit 并固定为本轮测试版本。external 不包含在 ets_runtime 仓库中，需单独核对来源和版本。
2. 阅读该版本的 `README.md` 和 `run_arksteed_external_tests.py`。当前仓库含应用预热、三方库和 weekly 用例，既有单文件形式，也有 `fileInfo.txt` 多文件形式，使用独立 runner；**不要求 `expected_output.txt`，不能套用内部 runner 的收集规则，也不能通过 `-I external` 代替运行**。内部 runner 只负责 ets_runtime 自带用例。
3. 核对 external runner 实际收集的全部用例，记录数量及被跳过/排除的目录；运行时显式传 `-R "$ark_root"`、平台与模式。当前 external runner 以编译成功且 VM 退出码为 0 判定通过，FAIL/TIMEOUT 分别统计；这是回归/稳定性验证，不是性能无回退证明。
4. external 缺失、无法获取、格式不兼容、收集到零个用例或未完整执行时，记录为未验证/阻塞。仅内部用例通过时，不能勾选模板中“含 external”的通过项。无需为兼容内部 runner 修改 external 用例或补造预期输出。

## 2. 执行全量功能测试

每个配置分别运行原生 unittest、内部 JS/TS 与 external 三套测试，全部执行且通过后，才能报告该配置的全量功能测试通过。**只执行用户要求的配置**；单独请求 ARM64 Release 全量功能测试时不自动扩大到 Debug 或其他架构，未运行项标为未验证。

输出接口检查通过后，采用 **配置间串行、配置内先构建并完成 unittest，再并行运行内部与 external** 的编排：

1. 核对三套测试使用相同源码、依赖、架构、模式和 GN 配置，先完成该配置的构建（包括 stub 和跨架构所需的 host `es2abc`）。构建失败同样停止后续配置并报告，本配置三套测试均记为未执行，不使用旧产物继续跑。
2. 使用 `arksteed_host_unittest` 构建并运行本配置的全部 ArkSteed 原生单测；仅构建可执行文件不算运行通过。保留 `ets_runtime_enable_ark_steed=true`，ARM64/QEMU 配置还需 `run_with_qemu=true`，其余 GN 参数与前一步保持一致。该入口会增量构建单测依赖，不与 JS/TS runner 并发写构建产物。若单测命令失败或受阻，记录真实退出码，停止本配置剩余阶段及后续配置，不继续使用可能不完整的产物。
3. unittest 成功后，两套 JS/TS runner 都显式加 `-F`，同时启动；测试期间只读共享构建产物，各自通过已核实的输出接口写独立的持久结果子目录。不要将两个默认带构建的 runner 直接放到后台并行。
4. 两套 runner 均显式设置 `-j`，按下述资源评估分配本轮总 worker 预算，不固定为 `2+2`，也不直接取 `nproc`。内部默认 1 个 worker，external 的源码默认值为 `os.cpu_count()`；实际运行可能显式覆盖，先检查进程命令行或 `run.meta.json`，不能把默认值当成本次并发数。
5. 分别保存日志并 `wait` 两个 PID，收集各自真实退出码；一套失败不提前终止另一套，也不加 `-s` 在当前套件内提前停止。三套全部通过后才进入下一配置；任一 runner 非零退出（含失败、超时或环境异常）则输出当前配置报告并返回非零，后续配置标为“未执行：前序配置失败”。这是配置级停止，不是首个失败用例即取消同配置其他测试。功能并发耗时不作为性能基线。

### 2.1 按执行端实际资源确定 `-j`

在**真正启动 runner 的机器、容器和 CPU affinity 下**评估，不使用本地 WSL 或宿主整机规格代替远端容器限额。并发数在每次启动前决定，单次 runner 内固定；这不是随 CPU 忙闲实时伸缩的调度器。

- **CPU 上限与余量**：读取 affinity/cpuset 与 cgroup CPU quota，按可见祖先中最严格的限制取上限；v2 看 `cpu.max`，v1 看 `cpu.cfs_quota_us / cpu.cfs_period_us`。通过 `/proc/self/cgroup` 和 `/proc/self/mountinfo` 定位实际挂载，不假定 cgroup v2。`nproc`、`os.cpu_count()` 可能只反映可见逻辑 CPU；quota 是上限而非独占保证，还需采样 CPU 使用、节流与其他任务占用，预留交互和系统余量。
- **内存约束**：结合宿主 `MemAvailable`、cgroup 的内存上限与当前使用量（v2 `memory.max/current`，v1 `memory.limit_in_bytes/usage_in_bytes`），按较紧的可用空间减去安全余量。结合代表性用例的峰值 RSS、Python 日志缓存及 QEMU 开销估算每 worker 成本，不只看某个轻用例或当前瞬时 RSS。
- **共同分配预算**：`-j` 是同时执行的用例数，不是 CPU 线程数；单个 VM 还可能启动 JIT/GC 线程。设两套 worker 数为 `j_i/j_e`，其单 worker CPU、内存估计为 `c_i/c_e`、`m_i/m_e`，同时满足 `j_i*c_i + j_e*c_e ≤ CPU预算` 和 `j_i*m_i + j_e*m_e ≤ 内存预算`。再结合待跑用例数、历史耗时与 I/O/日志压力分配，不能让两套各自独占同一份整机预算，也不要求机械平分。资源不足以同时运行两套时改为套件串行；没有余量时等待，不强行启动。
- **观测后调整**：已有同配置数据时据此选值；估计不足时，在已授权用例范围内先观察代表性运行，再为后续批次提高或降低并发。出现内存压力或明显节流时为下一次运行降低预算；若已发生失败或超时，按配置级停止规则报告，不能通过调整并发自动重跑或继续后续配置。架构、模式或共享负载变化后重新评估，不能直接将 x64 的分配套到 ARM64/QEMU。
- **区分上限与实际活跃数**：查看剩余队列和活动子进程。进入少数长用例的尾部阶段后，即使 `-j` 很大也不会占满 CPU，提高 `-j` 不能拆分单个用例；不要为调并发中断、重启或重复提交正在跑的任务。

记录评估时刻、CPU/内存限额、负载及保留余量、单 worker 成本依据、最终两套 `-j` 和取舍。将本轮得出的正整数分别设置为执行 shell 的 `ARKSTEED_INTERNAL_JOBS`、`ARKSTEED_EXTERNAL_JOBS`；它们是本轮测量后的输入，不提供固定默认值。无法确认限额或成本时明确记录不确定性，不声称已找到最优并发。

### 2.2 执行与退出码收集要求

以下仅列出 x64 Debug 的三个测试入口及选择参数，**未包含因工具版本而异的输出配置，不是可直接执行的持久化测试脚本**。先通过输出接口检查，将真实支持的输出配置加入所选命令，再交给记录器执行，并按下述规则编排和收集退出码。只按用户授权替换平台/模式，本 skill 不规定主机连接或源码部署方式。

```text
# Native unit test target
(cd ../.. && python3 ark.py x64.debug arksteed_host_unittest \
  --gn-args=ets_runtime_enable_ark_steed=true)

# Internal and external case selection after a matching successful build
python3 ecmascript/arksteed/test/run_arksteed_tests.py \
  -p x64 -m debug -F -j "$ARKSTEED_INTERNAL_JOBS"
python3 "$ark_root/arksteed_external_tests/run_arksteed_external_tests.py" \
  -R "$ark_root" -p x64 -m debug -F -j "$ARKSTEED_EXTERNAL_JOBS"
```

逐项落实：

- 每次只推进已授权配置，保持 x64 Debug → x64 Release → ARM64 Release → ARM64 Debug 的相对顺序；ARM64 跨架构先确认 QEMU。资源预算改变时在下个配置开始前重新评估。
- 从当前 runner 文档和源码确认构建入口及依赖，包括 stub、host `es2abc` 与 external 所需产物；只复用现有且已核实的入口，不为执行本指南另改外部构建/测试脚本。
- 构建和 unittest 分阶段执行，失败即收集本阶段真实退出码，剩余阶段记为 `NOT_RUN`，后续配置记为 `SKIP_AFTER_<config>`。没有本轮实际执行证据的增量缓存成功，不算单测通过。
- unittest 成功后，以 `-F -j "$ARKSTEED_INTERNAL_JOBS"` 和 `-F -j "$ARKSTEED_EXTERNAL_JOBS"` 并行两套 runner；external 另带 `-R "$ark_root"`。各自 stdout/stderr 从启动起重定向到本轮 `$ARK_RUN_DIR/logs/` 下不同的配置/套件文件，原始产物通过核实后的 runner 接口直写 `results/` 子目录。全量不加 `-s`。
- 分别 `wait` 两个 PID 并立即保存各自退出码；一套失败仍等待另一套，不用后一个 `wait` 覆盖前一个结果。当前配置结束后再决定是否进入下一配置；复合命令任一阶段失败最终返回非零。
- `$ARK_RESULTS_DIR/config-summary.tsv` 使用 `config / build_exit / unittest_exit / internal_exit / external_exit / result` 六列（tab 分隔）；阻塞时记录 `BLOCKED` 和未执行原因，不填写虚构退出码或统计。
- 记录源码/依赖版本、完整 external commit、命令、worker 数、收集/执行/通过/失败/超时/未执行数量及持久日志路径；GTest 统计必须有原始输出，不用目标数代替用例数。

只有已确认存在匹配本轮源码、依赖及配置的成功构建记录时，才可省去该配置的构建步骤，不能仅凭产物存在跳过构建。构建或测试失败不触发自动改源码、切换依赖或修改配置。已有任务在使用同一 checkout/产物时先核对状态；中断后确认两个 runner 及其子进程均已退出，后续授权复跑创建新历史，不覆盖失败记录。

只有三套全部约定用例执行且通过，才能将对应配置标记为通过。若实际使用了 `-s` 并提前停止、临时排除失败用例或只复跑部分目录，均不能当作全量通过；失败项可以注明基线复现情况，但不能改判为通过。

## 3. 测试报告

输出接口检查受阻时立即报告阻塞与持久记录路径，不执行后续配置。正常结束或因配置失败停止后，立即根据配置级 `config-summary.tsv`、unittest 的 GTest/GN action 日志、两套 runner 的 `summary.md` / `run.meta.json` 和日志输出中文报告，不等待或补跑后续配置。报告包含：

1. 按约定顺序列出用户要求的全部配置，标明通过、失败或未执行；未执行项注明“前序配置失败”及触发配置，或实际环境阻塞原因。
2. 说明触发停止的配置与阶段（构建、unittest、内部或 external），分别列出三套测试的实际退出码或未执行原因；一套测试失败不能掩盖其他套的结果。
3. 列出已执行范围的收集/执行/通过/失败/超时数量，以及失败用例、原因和日志位置；runner 异常退出导致报告缺失时注明缺失，不补造统计。
4. 附源码与依赖版本、实际命令、worker 分配依据、配置汇总和三套原始报告或日志的持久路径，注明实际执行主机，并提供运行 ID 与记录目录。已有 `.agents/` 累计报告只链接这些不可变记录。将复跑或修复建议作为后续事项，不自动继续未执行配置。

明确区分通过、失败和未执行/受阻。环境不可用、缺少 external 或测试失败不能改判为通过或不涉及；前序配置通过不代表整套矩阵通过，只有约定范围内的全部用例执行且通过，才报告本次全量功能测试通过。
