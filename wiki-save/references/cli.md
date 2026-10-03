# Wiki 保存脚本

## 职责

`../scripts/wiki_save.py` 使用 Python 标准库，运行时需要 Python 3.10+ 和可用的 Obsidian CLI。`vault_io.js` 是随脚本分发的固定原生 API 调用模板，正文作为 base64 数据传入。Wiki 正文始终通过 CLI 读写；本地文件仅用于候选和恢复快照。

脚本提供目标选择、精确路径读取、安全新建、快照比较修订和全文回读。查重、证据判断、脱敏、页面保护、获准差异、链接目标、锁、索引、日志和机器状态由 skill 按目标库规则处理。先完成这些发布前检查，再执行写入命令。脚本成功表示单页正文验证成功，整批发布仍需完成 skill 的验收。

## 命令

以下路径相对本 skill 根目录，执行时先转为绝对路径。完整参数以 `python3 scripts/wiki_save.py <command> --help` 为准。

```bash
# Resolve once and pin this vault for the current save operation.
python3 scripts/wiki_save.py resolve
python3 scripts/wiki_save.py resolve --vault "test-vault"

# Read an existing note through CLI into a new local recovery snapshot.
python3 scripts/wiki_save.py snapshot --vault "lib" --path "wiki/任务队列.md" --out "/tmp/queue.snapshot.json"

# Publish a new note. Its parent folder must already exist.
python3 scripts/wiki_save.py create --vault "lib" --path "wiki/任务队列.md" --content "/tmp/queue.candidate.md"

# Publish an authorized revision based on snapshot.content.
python3 scripts/wiki_save.py update --snapshot "/tmp/queue.snapshot.json" --content "/tmp/queue.candidate.md"
```

默认选择已注册名称中的首个匹配项：`lib → wiki → library`。显式 `--vault` 优先；指定值未注册则停止。仅检查选中名称的重名歧义，其他名称重复不影响选择。选中名称重复、访问失败或路径核对失败即停止，保留该目标。`update` 默认使用快照中的 vault；传入 `--vault` 时要求与快照一致。

只接受 NFC 形式、可见的 vault 相对 `.md` 路径；隐藏目录、绝对路径、反斜杠、控制字符、空路径段和以点开头的路径段会被拒绝。Python 和 JavaScript 均在进入笔记读写操作前拒绝非 NFC 路径，以及含 U+00A0（不换行空格）或 U+202F（窄不换行空格）的路径。这两个字符本身是 NFC，但 Obsidian 会将其转换为 U+0020（普通空格）。遇到这些诊断时，先确认并显式提供目标的空格归一化、NFC 路径，再执行命令。脚本保持传入路径原样，避免原生新建 API 自动归一化后写到不同路径；其他保持原样的 Unicode 空白仍可使用。机器记录按库协议另行维护。

## 候选与快照

- 候选是 UTF-8 文件，保留完整 frontmatter、正文、BOM、换行和字面反斜杠，正文 Unicode 形式保持原样。快照、无变更比较与写后回读经 CLI 调用 `vault.adapter.read(file.path)`，与 `vault.process` 回调的原始文本一致。使用文件写入工具准备数据，避免 shell 插值处理正文。
- 修订快照包含 `version`、`vault`、`vault_path`、`path`、`content`。先读取该 `content`，再形成仅含获准差异的候选。保持快照原样，以便并发比较与恢复。
- 快照采用独占创建和 `0600` 权限，已有文件会触发错误。遵循库内恢复副本规则，选择新的本地路径；快照可能包含原始私有正文，避免提交或公开。
- “只读”“先预览”时只在会话内展示候选，不生成本地候选或快照。`resolve` 可用于只读环境检查。
- 已完整保存的内容在查重阶段结束；无需调用修订命令。

## 结果与故障

命令成功时输出 JSON，失败时返回非零退出码和英文错误。写入结果的 `verified: true` 表示精确路径与全文回读通过；正文含代码、引号、美元符号、中文和字面 `\n` 时采用同一流程。

快照冲突、新建重名、目标身份不符、API 缺失都会停止。写后回读失败可能已有内容落盘；CLI 中断或缺少机器结果时，写入状态也可能未知。保留候选与快照，通过 CLI 检查精确页面，不自动重试或回滚。脚本提供单页并发检查，不提供跨页事务，也不代替库内单写入锁。

如果 CLI 提示安装器过旧且调用失败，使用 Obsidian 官方安装器更新并启动应用，再检查 `obsidian help` 与 `obsidian help eval`。CLI 不可用时保留会话候选，由用户恢复环境。

## 验证脚本

```bash
python3 -m unittest discover -s wiki-save/scripts -v
```

从仓库根目录运行。测试额外需要 Node.js，用内存 API 模型执行实际 JavaScript 模板；覆盖 vault 顺序、选中名称歧义、安全正文传输、BOM 快照与新建/修订/无变更回读、BOM 差异冲突、非 NFC 路径零写入、重名、并发冲突与回读失败。路径模型完整实现下述 `Nl/Dl/Bl` 链，并用已知输入输出验证各转换及组合顺序；U+00A0、U+202F 在文件名和父目录中分别覆盖新建、快照、修订，验证 Python 在求值前停止、JavaScript 返回 `written: false` 且内存写入次数为零。另验证分隔符转换路径零写入，以及普通空格、其他原样 Unicode 空白和 NFC 路径精确保留。测试不调用真实 CLI，也不修改真实 vault。真实 CLI 的读写集成验证应在用户指定的隔离测试 vault 中执行。

### 本地源码核实依据

只读解析 `~/Library/Application Support/obsidian/obsidian-1.13.7.asar` 的 ASAR 头，在内存中读取 `package.json` 和 `app.js`；其中版本字段为 `1.13.7`。未执行应用源码，未调用真实 vault 写入。`app.js` 共 3,876,459 字节，SHA-256 为 `8efbf581e259cabef4f9c9a34814cfe3c02863757377e56b3603933c50e89898`。

以下位置均在压缩源码第 1 行，偏移是 `app.js` 内从 0 开始的 UTF-8 字节偏移，指向相应函数定义：

| 定义 | 字节偏移 | 核实到的行为 |
| --- | ---: | --- |
| 桌面适配器 `read` | 556082 | 返回 `this.fsPromises.readFile(t,"utf8")`，保留 BOM。 |
| 桌面适配器 `process` | 558697 | 以 `readFile(i,"utf8")` 读取，将原始结果 `r` 传给回调 `t(r)`。 |
| `Vault.read` | 1393207 | 读取 `this.adapter.read(e.path)` 后，若首字符为 `65279`，执行 `t=t.substring(1)` 移除 BOM。 |
| `Vault.process` | 1396612 | 将回调直接传给 `this.adapter.process(e.path,t,n)`。 |
| `Vault.create` | 1392049 | 先执行 `i=Nl(e)`，再检查存在性并调用 `this.adapter.write(i,t,n)`。 |
| 路径函数 `Nl` | 551459 | `function Nl(e){return Dl(Bl(e)).normalize("NFC")}`。 |
| 空格匹配常量 `Tl` | 550848 | `var Tl=/\u00A0\|\u202F/g;`，只匹配这两个字符。 |
| 空格函数 `Dl` | 550872 | `function Dl(e){return e.replace(Tl," ")}`，全局替换为 U+0020。 |
| 分隔符函数 `Bl` | 551508 | 合并连续正反斜杠为 `/`，去掉首尾 `/`；结果为空时返回 `/`。 |

已完整读取的路径归一化定义如下，执行顺序为 `Bl → Dl → NFC`：

```javascript
var Tl=/\u00A0|\u202F/g;
function Dl(e){return e.replace(Tl," ")}
function Nl(e){return Dl(Bl(e)).normalize("NFC")}
function Bl(e){return""===(e=e.replace(/([\\/])+/g,"/").replace(/(^\/+|\/+$)/g,""))&&(e="/"),e}
```

前置检查逐项覆盖该链的全部转换：

| 转换 | 对应防护 |
| --- | --- |
| `Bl`：反斜杠转换、连续分隔符合并 | 反斜杠和空路径段检查已覆盖。 |
| `Bl`：首尾斜杠移除、空结果变为根路径 | 首尾空路径段及 `.md` 后缀要求已覆盖，空路径和纯分隔符路径均被拒绝。 |
| `Dl`：U+00A0、U+202F 变为 U+0020 | 两端新增明确字符检查，要求调用方确认目标后重新提供路径。 |
| NFC：规范等价字符归一化 | 两端保留 NFC 等值检查，覆盖 NFD 等会变化的路径。 |

通过这些检查的路径在此链中保持原样；本次核实未发现该链内其他遗漏转换，字符拒绝范围依据 `Tl` 的精确匹配。正文内容保持原始 Unicode、BOM 和换行，路径检查仅作用于路径。

这些实现差异是测试模型的依据：快照须与原生比较回调使用同一文本表示；会被路径归一化改写的输入须在原生新建调用之前拒绝。此证据限定于已检查的桌面版 1.13.7 源码及上述归一化链，内存模型验证与真实 CLI 集成验证分开报告。
