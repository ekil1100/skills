# ArkSteed 全量功能测试（按需执行）

**仅当用户明确要求全量功能测试时，才阅读并执行本文。** 提 PR、准备合入或 PR 模板包含自测试项，都不会自动触发测试；用户未要求时，不以未执行测试阻塞提 PR。仓库路径和命令均相对 **ets_runtime checkout 根目录**。

先读取当前 checkout 的 `ecmascript/arksteed/test/README.md`、`ecmascript/arksteed/unittests/README.md` 与 `BUILD.gn`。**功能测试包含原生 unittest、内部 JS/TS 和 external 三部分**，原生单测使用独立入口，不由 JS/TS runner 代跑。配置顺序为 **x64 Debug → x64 Release → ARM64 Release → ARM64 Debug**，每个配置均包含这三部分；用户限定配置子集时保持该相对顺序，只运行指定范围。**当前配置全部通过后才进入下一配置；任一用例失败或超时，当前配置结束后立即报告并返回，后续配置不再运行。** 本文只涉及功能测试，不自动启动性能测试。聚焦用例通过、只构建成功或仅检查对象文件，均不能报告为全量功能测试通过。

## 1. 准备并确认 external 用例

external 默认使用用户指定的仓库：**https://gitcode.com/xing-yunhao-huawei/arksteed_external_tests**。克隆地址为 `https://gitcode.com/xing-yunhao-huawei/arksteed_external_tests.git`；用户或项目明确指定的其他来源/版本优先。

1. 准备独立 checkout，默认放在 `$ark_root/arksteed_external_tests/`，与 `arkcompiler/` 同级；`ark_root` 是包含 `arkcompiler/`、`out/`、`ark.py` 的目录。从 ets_runtime 根目录可用 `ark_root="$(cd ../.. && pwd)"` 取得。目录不存在时克隆；已存在时先核对 origin、HEAD 和未提交修改，不覆盖或自动更新。未指定版本时首次克隆使用默认分支当前 HEAD，记录完整 commit 并固定为本轮测试版本。external 不包含在 ets_runtime 仓库中，需单独核对来源和版本。
2. 阅读该版本的 `README.md` 和 `run_arksteed_external_tests.py`。当前仓库含应用预热、三方库和 weekly 用例，既有单文件形式，也有 `fileInfo.txt` 多文件形式，使用独立 runner；**不要求 `expected_output.txt`，不能套用内部 runner 的收集规则，也不能通过 `-I external` 代替运行**。内部 runner 只负责 ets_runtime 自带用例。
3. 核对 external runner 实际收集的全部用例，记录数量及被跳过/排除的目录；运行时显式传 `-R "$ark_root"`、平台与模式。当前 external runner 以编译成功且 VM 退出码为 0 判定通过，FAIL/TIMEOUT 分别统计；这是回归/稳定性验证，不是性能无回退证明。
4. external 缺失、无法获取、格式不兼容、收集到零个用例或未完整执行时，记录为未验证/阻塞。仅内部用例通过时，不能勾选模板中“含 external”的通过项。无需为兼容内部 runner 修改 external 用例或补造预期输出。

## 2. 执行全量功能测试

每个配置分别运行原生 unittest、内部 JS/TS 与 external 三套测试，全部执行且通过后，才能报告该配置的全量功能测试通过。**只执行用户要求的配置**；单独请求 ARM64 Release 全量功能测试时不自动扩大到 Debug 或其他架构，未运行项标为未验证。

采用 **配置间串行、配置内先构建并完成 unittest，再并行运行内部与 external** 的编排：

1. 核对三套测试使用相同源码、依赖、架构、模式和 GN 配置，先完成该配置的构建（包括 stub 和跨架构所需的 host `es2abc`）。构建失败同样停止后续配置并报告，本配置三套测试均记为未执行，不使用旧产物继续跑。
2. 使用 `arksteed_host_unittest` 构建并运行本配置的全部 ArkSteed 原生单测；仅构建可执行文件不算运行通过。保留 `ets_runtime_enable_ark_steed=true`，ARM64/QEMU 配置还需 `run_with_qemu=true`，其余 GN 参数与前一步保持一致。该入口会增量构建单测依赖，不与 JS/TS runner 并发写构建产物。若单测命令失败或受阻，记录真实退出码，停止本配置剩余阶段及后续配置，不继续使用可能不完整的产物。
3. unittest 成功后，两套 JS/TS runner 都显式加 `-F`，同时启动；测试期间只读共享构建产物，各自写独立的 `/tmp/arksteed-*` 与 `/tmp/arksteed-external-*` 目录。不要将两个默认带构建的 runner 直接放到后台并行。
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

### 2.2 构建与原生单测完成后，并行执行两套 JS/TS 测试

以下是用户明确要求全量功能测试且未限定配置时的四配置示例。执行前完成资源评估并设置上述两个环境变量；本例固定本轮启动的 worker 数。四配置共用预算时按最重配置预留资源；需要不同预算或中途资源条件变化时，改为每次只执行一个配置，重新评估资源，且仅当前配置通过才继续。只测指定配置时从 `configs` 数组中保留所需项，不改变相对顺序。

运行前先确认 ARM64 跨架构所需的 QEMU 已就绪；全量示例不加 `-s`，尽量收集所有用例结果。当前内部 runner 没有独立 build-only CLI，示例复用其 `BuildConfig` / `build_ark()`，不复制易失配的 GN、host 工具链构建逻辑；版本变化时先核对这两个入口与 external 的构建需求，并核对 `run_unittest()` 与 runner 的 GN 参数一致。单测入口或指定配置不可用时报告阻塞，不省略该套测试后宣称全量通过。

```bash
(
    runner=ecmascript/arksteed/test/run_arksteed_tests.py
    ark_root="$(cd ../.. && pwd)" || exit 1
    external_runner="$ark_root/arksteed_external_tests/run_arksteed_external_tests.py"
    if [ ! -f "$runner" ] || [ ! -f "$external_runner" ]; then
        printf 'Missing internal or external runner\n' >&2
        exit 2
    fi
    internal_jobs="${ARKSTEED_INTERNAL_JOBS:?Set the internal worker count from the measured resource budget}"
    external_jobs="${ARKSTEED_EXTERNAL_JOBS:?Set the external worker count from the measured resource budget}"
    for jobs in "$internal_jobs" "$external_jobs"; do
        if ! [[ "$jobs" =~ ^[1-9][0-9]*$ ]]; then
            printf 'Worker counts must be positive integers\n' >&2
            exit 2
        fi
    done
    log_dir="$(mktemp -d /tmp/arksteed-full-functional.XXXXXX)" || exit 1
    report="$log_dir/config-summary.tsv"
    printf 'config\tbuild_exit\tunittest_exit\tinternal_exit\texternal_exit\tresult\n' >"$report"
    configs=(x64:debug x64:release arm64:release arm64:debug)
    attempted_configs=0
    stopped_at=""
    failed_builds=0
    failed_suites=0
    printf 'Logs: %s\n' "$log_dir"
    printf 'Workers: internal=%s external=%s\n' "$internal_jobs" "$external_jobs"

    build_config() {
        PYTHONDONTWRITEBYTECODE=1 python3 - "$runner" "$1" "$2" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).resolve().parent))
from run_arksteed_tests import BuildConfig, build_ark

cfg = BuildConfig(platform=sys.argv[2], mode=sys.argv[3])
raise SystemExit(0 if build_ark(cfg, skip_stub=False) else 2)
PY
    }

    run_unittest() {
        local platform="$1" mode="$2"
        local gn_args="ets_runtime_enable_ark_steed=true"
        if [ "$platform" = arm64 ]; then
            gn_args="$gn_args run_with_qemu=true"
        fi
        (cd "$ark_root" && python3 ark.py "$platform.$mode" arksteed_host_unittest \
            --gn-args="$gn_args")
    }

    for config in "${configs[@]}"; do
        attempted_configs=$((attempted_configs + 1))
        platform="${config%:*}"
        mode="${config#*:}"
        tag="$platform-$mode"

        if build_config "$platform" "$mode" >"$log_dir/$tag-build.log" 2>&1; then
            printf 'PASS build %s (exit 0)\n' "$config"
        else
            status=$?
            printf 'FAIL build %s (exit %d); SKIP unittest/internal/external\n' \
                "$config" "$status" >&2
            failed_builds=$((failed_builds + 1))
            printf '%s\t%s\tNOT_RUN\tNOT_RUN\tNOT_RUN\tBUILD_FAIL\n' \
                "$config" "$status" >>"$report"
            stopped_at="$config"
            break
        fi

        if run_unittest "$platform" "$mode" >"$log_dir/$tag-unittest.log" 2>&1; then
            printf 'PASS unittest %s (exit 0)\n' "$config"
        else
            status=$?
            printf 'FAIL unittest %s (exit %d); SKIP internal/external\n' \
                "$config" "$status" >&2
            failed_suites=$((failed_suites + 1))
            printf '%s\t0\t%s\tNOT_RUN\tNOT_RUN\tUNITTEST_FAIL\n' \
                "$config" "$status" >>"$report"
            stopped_at="$config"
            break
        fi

        python3 -u "$runner" -p "$platform" -m "$mode" -F -j "$internal_jobs" \
            >"$log_dir/$tag-internal.log" 2>&1 &
        internal_pid=$!
        python3 -u "$external_runner" -R "$ark_root" -p "$platform" -m "$mode" \
            -F -j "$external_jobs" >"$log_dir/$tag-external.log" 2>&1 &
        external_pid=$!
        printf 'RUN %s internal PID=%d external PID=%d\n' \
            "$config" "$internal_pid" "$external_pid"

        internal_status=0
        external_status=0
        if wait "$internal_pid"; then
            printf 'PASS internal %s (exit 0)\n' "$config"
        else
            internal_status=$?
            printf 'FAIL internal %s (exit %d)\n' "$config" "$internal_status" >&2
            failed_suites=$((failed_suites + 1))
        fi
        if wait "$external_pid"; then
            printf 'PASS external %s (exit 0)\n' "$config"
        else
            external_status=$?
            printf 'FAIL external %s (exit %d)\n' "$config" "$external_status" >&2
            failed_suites=$((failed_suites + 1))
        fi
        result=PASS
        if [ "$internal_status" -ne 0 ] || [ "$external_status" -ne 0 ]; then
            result=FAIL
            stopped_at="$config"
        fi
        printf '%s\t0\t0\t%s\t%s\t%s\n' \
            "$config" "$internal_status" "$external_status" "$result" >>"$report"
        if [ -n "$stopped_at" ]; then
            break
        fi
    done

    for config in "${configs[@]:$attempted_configs}"; do
        printf '%s\tNOT_RUN\tNOT_RUN\tNOT_RUN\tNOT_RUN\tSKIP_AFTER_%s\n' \
            "$config" "$stopped_at" >>"$report"
    done
    if [ -n "$stopped_at" ]; then
        printf 'STOP after %s; remaining configurations were not run\n' "$stopped_at" >&2
    fi
    printf '\nConfiguration report: %s\n' "$report"
    while IFS= read -r line; do
        printf '%s\n' "$line"
    done <"$report"
    printf 'Failed builds: %d; failed test suites: %d\n' "$failed_builds" "$failed_suites"
    if [ "$failed_builds" -ne 0 ] || [ "$failed_suites" -ne 0 ]; then
        exit 1
    fi
    exit 0
)
```

首个失败配置结束后即停止启动后续构建或测试，输出 `config-summary.tsv` 汇总并返回非零；其中 `NOT_RUN` / `SKIP_AFTER_<config>` 表示未执行，不能解读为通过。构建失败时立即汇总，不启动该配置的测试；unittest 命令失败时立即汇总，内部/external 记为未执行，报告注明是单测构建失败、用例失败还是环境阻塞。使用子 shell 避免退出调用者会话。只有已确认存在匹配本轮源码、依赖及配置的成功构建记录时，才可省去该配置的构建步骤，不能仅凭产物存在跳过构建。构建或测试失败不触发自动改源码、切换依赖或修改配置。发现已有任务在使用同一 checkout/产物时先核对状态，不重复启动或并发切换配置；中断后确认两个 runner 及其子进程均已退出，再重新构建或重跑。

每个配置分别保存构建及 unittest/内部/external 的源码与依赖版本、完整 external commit、命令、worker 数、退出码、收集/执行/通过/失败/超时/未执行数量及日志路径；示例的构建、unittest 和两套 runner 控制台日志分开保存在打印出的 `log_dir` 中。unittest 统计以 GTest/GN action 日志为准，缺少统计时注明，不用目标数量代替用例数量；没有本轮运行证据的缓存构建成功不能当作单测通过。内部产物为 `/tmp/arksteed-<platform>-<mode>-<timestamp>/`，external 产物为 `/tmp/arksteed-external-<platform>-<mode>-<timestamp>/`，以 runner 实际报告为准。

只有三套全部约定用例执行且通过，才能将对应配置标记为通过。若实际使用了 `-s` 并提前停止、临时排除失败用例或只复跑部分目录，均不能当作全量通过；失败项可以注明基线复现情况，但不能改判为通过。

## 3. 测试报告

正常结束或因配置失败停止后，立即根据配置级 `config-summary.tsv`、unittest 的 GTest/GN action 日志、两套 runner 的 `summary.md` / `run.meta.json` 和日志输出中文报告，不等待或补跑后续配置。报告包含：

1. 按约定顺序列出用户要求的全部配置，标明通过、失败或未执行；未执行项注明“前序配置失败”及触发配置，或实际环境阻塞原因。
2. 说明触发停止的配置与阶段（构建、unittest、内部或 external），分别列出三套测试的实际退出码或未执行原因；一套测试失败不能掩盖其他套的结果。
3. 列出已执行范围的收集/执行/通过/失败/超时数量，以及失败用例、原因和日志位置；runner 异常退出导致报告缺失时注明缺失，不补造统计。
4. 附源码与依赖版本、实际命令、worker 分配依据、配置汇总和三套原始报告或日志路径。将复跑或修复建议作为后续事项，不自动继续未执行配置。

明确区分通过、失败和未执行/受阻。环境不可用、缺少 external 或测试失败不能改判为通过或不涉及；前序配置通过不代表整套矩阵通过，只有约定范围内的全部用例执行且通过，才报告本次全量功能测试通过。
