# 中断记录器修复验证

## 结论

修复已纳入 commit `2a097f0`：解决 SIGINT 取消后子进程继续执行、输出管道阻塞，以及合并 stdout/stderr 时异常提示阻塞三个问题。经过 3 / 50 轮修复与独立复核，限定范围内无剩余发现。未连接真实 SSH 主机，未修改 runtime 或其他外部仓库。

## 修改范围

- `arksteed/scripts/run_with_history.py`
- `arksteed/tests/test_run_with_history.py`
- `ets-runtime-remote-build/scripts/remote_ops.py`
- `ets-runtime-remote-build/tests/test_history.py`

本报告仅记录该提交中的中断修复；两个 skill 仍无相互引用或导入。

## 实现

- 每条命令使用独立进程组；中断时先发送 TERM，最多等待 5 秒，再以 KILL 清理残留进程组。
- 清理期间忽略重复 SIGINT，等待命令结束并收完输出后再填写结束时间和实际退出码。
- 使用独立日志线程及完成事件，避免主进程已经退出时，SIGINT 打断线程 join 导致误判日志线程已结束。
- 原始日志优先写入持久文件；控制台转发改为非阻塞、尽力展示，避免无人读取管道时取消永久卡住。管道拥塞时以持久日志为完整证据，不承诺控制台副本完整。
- 初始提示、完成提示、异常提示均避免阻塞本地记录器收尾；远端正常处理 KeyboardInterrupt，避免阻塞式 traceback。

## 测试结果

共 63 项测试通过，新增 9 项真实信号/管道回归；新增测试均先确认失败，再验证修复。

| 检查 | 结果 | 日志 |
|---|---|---|
| 本地记录器完整测试 | 11 项通过 | [local-complete.log](local-complete.log) |
| 远端历史与故障注入测试 | 11 项通过 | [remote-history-final.log](remote-history-final.log) |
| 假 SSH / 真实 rsync 集成测试 | 41 项通过 | [remote-integration-final.log](remote-integration-final.log) |
| 差异检查、Python AST、skill 独立性与代码命名 | 通过 | 工具执行输出 |

新增覆盖：正常处理 TERM 的子进程及清理日志、忽略 TERM 的子进程、主进程先退出、无人读取的展示管道、本地 stdout/stderr 合并后无人读取。

## 失败证据与复核记录

- 初始回归失败：[local-red.log](local-red.log)、[remote-red.log](remote-red.log)。
- 展示阻塞回归失败：[local-backpressure-red.log](local-backpressure-red.log)、[remote-backpressure-red.log](remote-backpressure-red.log)。
- 合并管道回归失败：[local-merged-red.log](local-merged-red.log)。

这些是修复前的原始失败输出，栈中的行号对应当时版本；最终通过结果见上表。一次性复现脚本、隔离 Git 仓库、运行夹具及重复的中间日志已清理，长期复验使用已提交的回归测试，不依赖旧 PID、机器绝对路径或已删除夹具。

## 复验命令

从仓库根目录执行，输出应直接保存到新建的持久验证目录，不覆盖本报告中的历史日志：

```bash
python3 -B -m unittest discover -s arksteed/tests -v
python3 -B -m unittest discover -s ets-runtime-remote-build/tests -p test_history.py -v
python3 -B -m unittest discover -s ets-runtime-remote-build/tests -p test_remote_build.py -v
```

只复验中断缺陷时，在前两条命令中追加 `-k interrupt`，分别覆盖 5 项和 4 项信号/管道回归。

## 验证边界

仅使用隔离夹具、真实本地进程/信号和假 SSH；未执行真实远端构建或 SSH 中断测试。没有扩展承诺到 SIGKILL、任意脱离进程组的 daemon 或机器掉电场景。
