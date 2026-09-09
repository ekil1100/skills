---
name: arksteed
description: Develop, optimize, debug, test, and review the CFG-based ArkSteed JIT compiler in arkcompiler/ets_runtime. Use whenever a task mentions ArkSteed or ark_steed, touches ecmascript/arksteed, or requests bytecode lowering, CFG/BB/Vertex work, PGO/IC optimization, register allocation, code generation, deoptimization, safepoints, GC barriers, or tests specifically for ArkSteed.
compatibility: Requires an arkcompiler/ets_runtime checkout and its normal Ark build toolchain; ARM64 cross-testing requires QEMU.
---

# ArkSteed

ArkSteed is a CFG-based JIT: `Graph` contains `BB`s, and blocks contain `Vertex` operations. It is separate from the normal Circuit/Gate + PassManager compiler pipeline.

## Scope rule

Keep ArkSteed optimizations inside:

```text
ecmascript/arksteed/**
```

Keep tests under its test directory. Read shared compiler/runtime code to understand semantics and ABI, but do not modify it merely for convenience.

Only edit outside ArkSteed when a local CFG, lowering, metadata, or codegen solution is insufficient. Explain the reason before making such an edit. Build registration and genuinely shared ABI/runtime defects are valid exceptions; easier reuse is not.

## Workflow

### 1. Find the current implementation

ArkSteed changes quickly. Do not rely on a fixed file map. Search by the task's bytecode, operation, Vertex, stub, assertion, or behavior:

```bash
rg -n '<term>' ecmascript/arksteed
rg -l '<term>' ecmascript/arksteed/test | sort
rg -n '<exact-symbol>' ecmascript | grep -v '^ecmascript/arksteed/'
git log -n 10 --oneline -- ecmascript/arksteed
```

Use current code, nearby tests, and recent commits as the source of truth. If a remembered symbol is absent, find its current equivalent instead of recreating it.

### 2. Trace only the affected path

Follow as much of this chain as the task requires:

```text
bytecode → CFG construction/optimization → register allocation
         → codegen → deopt/safepoint/GC/runtime behavior
```

Before editing, identify the current symbols involved and the intended writable scope.

### 3. Implement the smallest complete change

- Prefer an ArkSteed-local CFG transformation, Vertex, analysis, lowering, or assembler sequence.
- When a design comparison would help, use V8 Maglev as a reference. Prefer an already-provided local V8 checkout; otherwise ask the user for its path once. If unavailable, continue with `gh` or `curl` against `github.com/v8/v8`. Adapt the design to ArkSteed's CFG, runtime, GC, and deopt contracts instead of copying it directly.
- Preserve a correct generic fallback for unsupported or stale feedback.
- Keep operation effects and value representations truthful.
- Follow current ArkSteed allocation, ownership, and lifetime conventions.
- Check both x64 and ARM64 for platform-neutral codegen or ABI changes.
- Do not add ArkSteed optimizations to the normal compiler pipeline.

### 4. Format modified C/C++ code

Before building or finishing an ArkSteed code change, format every modified C/C++ line with OpenHarmony's pinned formatter and the source tree's `.clang-format`. From the `ets_runtime` root, preview the affected range first:

```bash
../../prebuilts/clang/ohos/linux-x86_64/llvm/bin/git-clang-format \
  --binary ../../prebuilts/clang/ohos/linux-x86_64/llvm/bin/clang-format \
  --style=file --diffstat HEAD -- <modified-cpp-files>
```

Then apply formatting only to the task's changed lines:

```bash
../../prebuilts/clang/ohos/linux-x86_64/llvm/bin/git-clang-format \
  --binary ../../prebuilts/clang/ohos/linux-x86_64/llvm/bin/clang-format \
  --style=file -f HEAD -- <modified-cpp-files>
```

- Use a fixed comparison point that isolates the task; `HEAD` is the normal choice for working-tree changes.
- Pass an explicit list of modified `.c`, `.cc`, `.cpp`, `.cxx`, `.h`, or `.hpp` files. Do not format unrelated dirty files.
- Do not use an unqualified `clang-format` from `PATH`; it may be a different version from the OpenHarmony prebuilt.
- Do not blindly run `clang-format -i` across legacy files. It can reformat thousands of untouched lines and obscure the functional review.
- Preview continuation macros such as `arksteed_opcode_list.h`: if one changed entry expands formatting to the entire macro, preserve the existing macro layout and align the new entry with its neighbors unless broad reformatting was explicitly requested.
- `clang-format` is for C/C++ sources; use the language-appropriate formatter for Python, JavaScript, TypeScript, or documentation when needed.

Run the preview command again after formatting. It should report that clang-format would not modify any files. Also run `git diff --check` before finishing.

### 5. 开发阶段构建与聚焦验证

本节用于开发阶段的快速反馈，不能代替第 6 节的合入前验收。以下仓库路径和命令均相对 **ets_runtime checkout 根目录**，不是 skill 目录。

单独构建 x64 debug：

```bash
(cd ../.. && python3 ark.py x64.debug \
  --gn-args=ets_runtime_enable_ark_steed=true)
```

runner 默认自动构建并启用 ArkSteed，因此无需先重复执行单独构建。按本次改动选择聚焦用例；`-I` 是相对测试根目录的路径，例如 `jittest/createemptyarray_inline_allocation`：

```bash
# Focused x64 debug validation
python3 ecmascript/arksteed/test/run_arksteed_tests.py \
  -p x64 -m debug -I '<case-or-directory>' -s -v

# Architecture-sensitive changes also need ARM64 validation
python3 ecmascript/arksteed/test/run_arksteed_tests.py \
  -p arm64 -m debug -I '<case-or-directory>' -s -v
```

仅当已有与当前源码、依赖、架构、模式及 GN 配置匹配的成功构建时，才追加 `-F`。rebase、`repo sync` 或依赖变化后，首次验证重新构建，不能用旧产物宣称新版本通过。

非 ARM64 主机需要 `qemu-aarch64-static` 或 `qemu-aarch64`。ARM64 runner 会配置 `run_with_qemu=true`、构建 host `es2abc`、设置 sysroot/库路径并调用 QEMU。

在 SSH 主机 `work` 执行本文任何构建或测试命令时，使用 `arkts-runtime-build` skill 对齐 checkout、镜像源码并检查执行环境；本 skill 提供 ArkSteed 命令与验收标准。external 用例被 Git 忽略，须单独核对实际执行主机上的用例来源和版本，不能假定源码同步已包含它们。

优化测试应证明方法确实编译、编译前后行为一致、CFG/代码形态符合预期，以及回退或 deopt 行为。分配与寄存器相关改动补充压力和强制 GC 覆盖；代码生成注释只能证明生成了某条路径，不能代替运行时命中证据。

默认验证受支持的普通 GC 路径。**CMC 不作为默认合入门槛**；未运行或环境不支持时单独注明、不阻塞。仅当用户明确要求，或仓库和执行环境已确认支持时，才增加 CMC 专项。

### 6. 合入前验收：按 PR 模板逐项完成

准备 PR 或合入前，先读取 ets_runtime checkout 中的 `.gitee/PULL_REQUEST_TEMPLATE.zh-CN.md` 与 `ecmascript/arksteed/test/README.md`。以当前模板为准；当前要求是 **x64 / ARM64 × Debug / Release 四套功能测试，每套都包含 external，以及 Release JIT 性能无回退**。聚焦用例通过、只构建成功或仅检查对象文件，都不等于完成合入验收。

#### 6.1 准备并确认 external 用例

external 是独立 Git 仓库提供的额外用例，默认放在 `ecmascript/arksteed/test/external/`，该目录被 `.gitignore` 忽略。它不是固定指代 Test262 或某套公开测试。

1. 从用户、维护方或项目配置取得约定的仓库 URL 与版本，记录实际 commit。仓库未提供固定地址时先询问，不猜测地址或替换成其他测试集；已有目录先核对来源、版本和本地修改，避免覆盖用户工作。
2. 在实际测试主机准备好该目录，并按当前 runner 格式检查：每个用例目录恰有一份 JS/TS 源码和 `expected_output.txt`，运行参数/结构约束遵循 README 的注解协议。旧版 runner 的 `--external-repo`、`--external-dir`、`--skip-external` 参数不适用于当前 runner。
3. 确认约定用例均可被收集。当前 runner 遍历整个测试根目录，external 准备好后会被全量运行纳入；可用 `-I external` 单独定位问题，但该筛选不能代替完整验收。
4. external 缺失、无法获取、不兼容或收集到零个用例时，记录为未验证/阻塞。仅内部用例通过时，不能勾选模板中“含 external”的已通过项。

#### 6.2 执行四套全量功能测试

完成 external 准备后，从 ets_runtime 根目录执行；需要并行时可追加 `-j <N>`：

```bash
runner=ecmascript/arksteed/test/run_arksteed_tests.py

# Full suites, including the prepared external corpus
python3 "$runner" -p x64   -m debug   -s
python3 "$runner" -p x64   -m release -s
python3 "$runner" -p arm64 -m debug   -s
python3 "$runner" -p arm64 -m release -s
```

每个配置保存源码/依赖版本、external commit、实际命令、退出码、收集/执行/通过/失败数量及日志/报告路径，并核对 external 的实际执行数量非零、没有漏跑约定用例。runner 产物位于其报告的 `/tmp/arksteed-<platform>-<mode>-<timestamp>/`。

只有全部约定用例执行且通过，才能将对应配置标记为通过。`-s` 提前停止、临时排除失败用例或只复跑部分目录，都不能当作全量通过；失败项可以注明基线复现情况，但不能改判为已通过。

#### 6.3 Release JIT 性能无回退：用户人工确认

默认由用户手动验证并确认性能，agent 自动化负责功能测试的构建、执行和结果汇总，不自动运行性能套件。功能测试完成后，性能项保持“待用户确认”，不能据此宣称整个合入前验收已完成。

收到明确确认后，记录对应的候选 commit、用户结论及用户提供的基线/报告信息，标记为“用户确认通过”，与 agent 实际执行的测试区分。待合入代码发生变化后，确认其是否仍覆盖当前版本；不能沿用旧版本确认。

仅当用户另行明确委托 agent 执行性能测试时，才按以下要求开展：

- 使用项目约定的 JIT 性能套件、比较基线和判定标准；入口或标准缺失时向维护方确认，不能编造命令、阈值或以功能测试代替性能测试。
- 在同一真实硬件上对比基线与待合入版本，保持依赖、Release 构建配置、输入和运行参数一致；确认测到的是 ArkSteed 编译代码，关闭非必要调试/图/汇编日志，按性能套件要求预热、重复测量并记录波动。
- QEMU 可用于 ARM64 功能正确性验证，不能据此宣称真实硬件性能无回退。局部优化另附针对性 benchmark；不能只凭少了一次 stub 调用或静态代码形态宣称性能通过。
- 记录基线/候选 commit、硬件、配置、命令、原始结果与比较结论。性能未运行或缺少可比基线时，该项保持未验证/阻塞。

纯文档等确实不涉及的改动可按模板逐项注明理由；环境不可用、缺少 external 或测试失败不是“不涉及”。只有所有适用项均有通过证据，才报告合入前验收完成。

## Correctness checklist

Check only the applicable items:

- Bytecode admission agrees with CFG lowering for every admitted form.
- CFG predecessors, successors, Phis, frame state, catch edges, and deopt edges remain valid.
- Reads, writes, calls, allocation, exceptions, and deopt effects are not hidden from analyses.
- PGO/IC assumptions are guarded or dependency-tracked, with a safe fallback.
- Register constraints, clobbers, spills, and tagged/untagged representations are correct.
- Movable heap objects are not retained as unsafe raw pointers during compilation.
- GC-capable calls expose tagged roots through safepoints.
- Heap stores use the required local/shared and generational barriers.
- Deopt metadata agrees with its runtime decoder and ABI.
- Architecture-specific behavior is correct on x64 and ARM64 when affected.

## Review and report

For review, prioritize correctness bugs, missing fallback/deopt paths, GC hazards, ABI mismatches, and unnecessary edits outside ArkSteed.

Finish with:

1. what changed;
2. whether the change stayed inside ArkSteed and why any external edit was needed;
3. 实际执行的格式化、构建、测试命令及结果，以及未执行项和原因；合入前按 PR 模板逐项报告验收状态；
4. remaining architecture, GC, ABI, or performance risk.
