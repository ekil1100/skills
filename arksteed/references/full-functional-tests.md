# ArkSteed 全量功能测试（按需执行）

**仅当用户明确要求全量功能测试时，才阅读并执行本文。** 提 PR、准备合入或 PR 模板包含自测试项，都不会自动触发测试；用户未要求时，不以未执行测试阻塞提 PR。仓库路径和命令均相对 **ets_runtime checkout 根目录**。

先读取当前 checkout 的 `ecmascript/arksteed/test/README.md`。按用户指定的配置执行；未限定配置时，执行 **x64/ARM64 × Debug/Release 四套功能测试，每套包含 external**。本文只涉及功能测试，不自动启动性能测试。聚焦用例通过、只构建成功或仅检查对象文件，均不能报告为全量功能测试通过。

## 1. 准备并确认 external 用例

external 默认使用用户指定的仓库：**https://gitcode.com/xing-yunhao-huawei/arksteed_external_tests**。克隆地址为 `https://gitcode.com/xing-yunhao-huawei/arksteed_external_tests.git`；用户或项目明确指定的其他来源/版本优先。

1. 准备独立 checkout，默认放在 `$ark_root/arksteed_external_tests/`，与 `arkcompiler/` 同级；`ark_root` 是包含 `arkcompiler/`、`out/`、`ark.py` 的目录。从 ets_runtime 根目录可用 `ark_root="$(cd ../.. && pwd)"` 取得。目录不存在时克隆；已存在时先核对 origin、HEAD 和未提交修改，不覆盖或自动更新。未指定版本时首次克隆使用默认分支当前 HEAD，记录完整 commit 并固定为本轮测试版本。external 不包含在 ets_runtime 仓库中，需单独核对来源和版本。
2. 阅读该版本的 `README.md` 和 `run_arksteed_external_tests.py`。当前仓库含应用预热、三方库和 weekly 用例，既有单文件形式，也有 `fileInfo.txt` 多文件形式，使用独立 runner；**不要求 `expected_output.txt`，不能套用内部 runner 的收集规则，也不能通过 `-I external` 代替运行**。内部 runner 只负责 ets_runtime 自带用例。
3. 核对 external runner 实际收集的全部用例，记录数量及被跳过/排除的目录；运行时显式传 `-R "$ark_root"`、平台与模式。当前 external runner 以编译成功且 VM 退出码为 0 判定通过，FAIL/TIMEOUT 分别统计；这是回归/稳定性验证，不是性能无回退证明。
4. external 缺失、无法获取、格式不兼容、收集到零个用例或未完整执行时，记录为未验证/阻塞。仅内部用例通过时，不能勾选模板中“含 external”的通过项。无需为兼容内部 runner 修改 external 用例或补造预期输出。

## 2. 执行全量功能测试

每个配置分别运行内部与 external 两套测试，全部执行且通过后，才能报告该配置的全量功能测试通过。**只执行用户要求的配置**；单独请求 ARM64 Release 全量功能测试时不自动扩大到 Debug 或其他架构，未运行项标为未验证。

以下是用户明确要求全量功能测试且未限定配置时的四配置顺序示例。单个 runner 失败后继续执行后续套件，打印各次退出码、累计失败套数，最终只要发生过失败就返回非零；使用子 shell 避免退出调用者会话。全量测试示例不加 `-s`，尽量收集所有用例结果。需要用例并行时给对应 runner 追加 `-j <N>`，不同构建配置仍顺序执行：

```bash
(
    runner=ecmascript/arksteed/test/run_arksteed_tests.py
    ark_root="$(cd ../.. && pwd)" || exit 1
    external_runner="$ark_root/arksteed_external_tests/run_arksteed_external_tests.py"
    failures=0

    for config in x64:debug x64:release arm64:debug arm64:release; do
        platform="${config%:*}"
        mode="${config#*:}"

        if python3 "$runner" -p "$platform" -m "$mode"; then
            printf 'PASS internal %s (exit 0)\n' "$config"
        else
            status=$?
            printf 'FAIL internal %s (exit %d)\n' "$config" "$status" >&2
            failures=$((failures + 1))
        fi

        if python3 "$external_runner" -R "$ark_root" -p "$platform" -m "$mode"; then
            printf 'PASS external %s (exit 0)\n' "$config"
        else
            status=$?
            printf 'FAIL external %s (exit %d)\n' "$config" "$status" >&2
            failures=$((failures + 1))
        fi
    done

    printf 'Failed suites: %d\n' "$failures"
    if [ "$failures" -ne 0 ]; then
        exit 1
    fi
    exit 0
)
```

两套 runner 都有构建入口。只有确认前一套已成功构建且源码、依赖、架构、模式及所需 GN 配置一致时，后一套才追加 `-F` 避免重复构建；不能仅凭产物存在跳过构建。构建或测试失败不触发自动改源码、切换依赖或修改配置。发现已有任务在使用同一 checkout/产物时先核对状态，不重复启动或并发切换配置。

每个配置分别保存内部/external 的源码与依赖版本、完整 external commit、命令、退出码、收集/执行/通过/失败/超时数量及日志路径。内部产物为 `/tmp/arksteed-<platform>-<mode>-<timestamp>/`，external 产物为 `/tmp/arksteed-external-<platform>-<mode>-<timestamp>/`，以 runner 实际报告为准。

只有两套全部约定用例执行且通过，才能将对应配置标记为通过。若实际使用了 `-s` 并提前停止、临时排除失败用例或只复跑部分目录，均不能当作全量通过；失败项可以注明基线复现情况，但不能改判为通过。

## 3. 测试报告

按用户要求的范围逐项报告功能测试结果，附源码版本、实际命令、退出码、用例数量及日志位置。

明确区分通过、失败和未执行/受阻。环境不可用、缺少 external 或测试失败不能改判为通过或不涉及；只有约定范围内的全部用例执行且通过，才报告本次全量功能测试通过。
