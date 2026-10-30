# 素材审核与发布排期 Agent

小影校招 AI Agent 项目题 **1**。读取素材、审核规则、寻找可用时段，保存本地排期草稿。真实业务决策模型固定为 **qwen3.8-max**。

项目重点是：用户授权范围、保存时发生冲突后的重新决策，以及提交结果未知时的幂等恢复。只生成草稿，不连接真实发布平台；改期或软风险仍待用户确认。

当前证据：58 项独立测试通过；修复后的 14 组场景验收及 15 次关键场景重复实验通过，共记录 145 次真实模型响应。14 组中包含一次零模型调用的重复提交恢复。首轮 10/14 的失败结果与代码快照也保留，详见[评估口径](docs/03-evaluation.md)。

## 评审入口

| 要看什么 | 入口 |
|---|---|
| 一次完整真实运行 | [输入](examples/live/normal.input.json) · [实际输出](examples/live/normal.output.json) · [Trace](examples/live/normal.trace.jsonl) |
| 固定时间冲突与待确认 | [输出](examples/live/time-conflict.output.json) · [Trace](examples/live/time-conflict.trace.jsonl) |
| 保存时新增占用后重新查询 | [输出](examples/live/conflict-on-save.output.json) · [Trace](examples/live/conflict-on-save.trace.jsonl) |
| 保存响应丢失后核对 | [输出](examples/live/save-response-lost.output.json) · [Trace](examples/live/save-response-lost.trace.jsonl) |
| 全部真实模型结果 | [报告](examples/live/report.md) · [机器可读断言与哈希](examples/live/summary.json) |
| 五个关键场景各重复三次 | [报告](examples/live-repeats/report.md) · [逐次断言与哈希](examples/live-repeats/summary.json) |
| 首轮真实失败与改进 | [原始报告](examples/iterations/v1/report.md) · [复盘](docs/05-retrospective.md) |
| 独立边界测试 | [测试代码](tests/test_acceptance.py) · [验收报告](docs/acceptance-report.md) |
| 2–3 小时计划与实际用时 | [PLAN.md](PLAN.md) |

## 环境与安装

Python **3.10+**，仅使用标准库，无第三方依赖。已提供 [requirements.txt](requirements.txt)。

```sh
git clone https://github.com/LN-JIN/material-scheduling-agent.git
cd material-scheduling-agent
python --version
```

若系统命令为 `python3`，将下列 `python` 替换为 `python3`。

## 无密钥运行

离线模式使用明确标记的确定性模拟器，可以检查工具、约束与持久化。**它不能证明真实模型的决策效果。**

```sh
python -m src.main --input examples/inputs/normal.json --mode offline --db .runtime/demo.sqlite --output .runtime/demo.output.json --trace .runtime/demo.trace.jsonl
python -m unittest discover -s tests -v
python -m scripts.evaluate --mode offline --out-dir .runtime/offline-review
python -m scripts.audit --output .runtime/delivery-audit.json
```

第一条命令重复执行会返回同一个草稿 ID。要重新执行全流程，请使用新的数据库路径；不要为绕过重复请求检查而更改原请求内容。

## 真实模型运行

准备自己的千问 API Key。`.env.example` 仅是变量说明，项目不会自动加载 `.env`。

PowerShell：

```powershell
$env:DASHSCOPE_API_KEY = Read-Host '输入自己的千问 API Key' -MaskInput
$env:QWEN_BASE_URL = 'https://maas.qianwenaiapi.com/compatible-mode/v1'
$env:QWEN_MODEL = 'qwen3.8-max'
python -m src.main --input examples/inputs/time-conflict.json --mode live --db .runtime/live-demo.sqlite --output .runtime/live-demo.output.json --trace .runtime/live-demo.trace.jsonl
```

上面的 `-MaskInput` 需要 PowerShell 7；其他终端可使用其环境变量配置方式。Bash（macOS/Linux，可先运行 `bash`）：

```sh
read -rs -p 'Qwen API Key: ' DASHSCOPE_API_KEY
export DASHSCOPE_API_KEY
export QWEN_BASE_URL='https://maas.qianwenaiapi.com/compatible-mode/v1'
export QWEN_MODEL='qwen3.8-max'
python -m src.main --input examples/inputs/time-conflict.json --mode live --db .runtime/live-demo.sqlite --output .runtime/live-demo.output.json --trace .runtime/live-demo.trace.jsonl
```

官方接口依据：[千问首次调用指南](https://platform.qianwenai.com/docs/developer-guides/getting-started/first-api-call)。不同服务或区域的密钥需要匹配其官方地址。本仓库不包含实际密钥，不会把请求头或模型内部思考写入 Trace。

## 复现评估与故障

全套评估通过真实 CLI 启动独立进程，为每个场景建立独立数据库，检查实际输出、JSONL Trace 与数据库快照。

```sh
python -m scripts.evaluate --mode live --out-dir .runtime/live-review
python -m scripts.evaluate --mode live --cases time-conflict,window-authorized,changed-slot,conflict-on-save,save-response-lost --repeat 3 --out-dir .runtime/live-repeat-review
```

live 命令会产生真实模型调用和服务费用；离线命令无模型调用。故障与预期结果不会传入模型，只通过工具实际返回体现。

CLI 可选 `--fault`：

| 故障 | 注入方式 |
|---|---|
| `tool_failure` | 首次工具调用暂时失败，模型可根据 `retryable` 选择重试 |
| `save_fail_permanent` | 每次保存均在提交前失败；最终明确 `failed`，无草稿 |
| `save_fail_before_commit` | 一次提交前失败并回滚；同请求可安全重试 |
| `conflict_on_save` | 查询后、保存前插入占用；事务拒绝，模型须重查 |
| `save_response_lost` | 实际提交后丢失返回；状态未知，同请求重试返回原草稿 |

例如：

```sh
python -m src.main --input examples/inputs/conflict-on-save.json --mode offline --fault conflict_on_save --db .runtime/race.sqlite --output .runtime/race.output.json --trace .runtime/race.trace.jsonl
```

评估报告中的 `provider_calls` 来自服务实际响应。重复提交通常是零模型调用的幂等恢复，不能把它计为新的模型决策路径。用例与有限重复结果不能推导一般场景准确率。

## 输入、输出与终止语义

每次请求处理一个素材；[输入示例](examples/inputs/)和[完整约定](docs/01-requirements.md)可直接查看。

- `fixed`：用户要求的时刻不变。其他可用时间仅生成待确认提案。
- `window`：整个素材时段必须在用户明确授权窗口内；软风险仍需要确认。
- 同一 `request_id` 绑定全局原始载荷；改变素材、时间或备注需要新请求号。
- 已保存请求再次提交返回原草稿快照，不重新审核或重排。需要依据新资料重新决策时使用新请求。
- 素材与规则在每次工具实例初始化时加载；已有排期快照会同步到本地数据库。

`confirmation_status`、`save_status`、`task_status` 是独立维度。`completed` 只表示草稿任务完成；`pending_confirmation` 表示草稿已保存但仍需确认。没有实际发布状态。

默认 `--max-steps 12`。每次模型决策，包括错误或被拒绝的动作，都计入预算。达到上限时 `task_status=incomplete`，即使最后一步已经落库，也保留 `save_status=saved`、`draft_id` 和 `draft_status`；不能把已保存写成失败。使用原请求重试可以恢复。

CLI 退出码：`0` 表示正常表达的业务结果（含阻断、待补充、保存失败）；`1` 表示运行未完成；`2` 表示输入或初始化被拒绝。业务成功与否应读取输出 JSON，不能仅看退出码。

## 目录与文档

```text
src/        模型适配、决策循环、四工具、SQLite 存储
tests/      独立、无网络的业务与对抗测试
data/       合成素材、规则、已有排期
examples/   输入、实际输出、Trace、数据库快照、评估与迭代证据
scripts/    经真实 CLI 执行的评估与交付检查
docs/       需求、方案、评估、AI 使用、复盘、独立验收报告
```

- [01 需求](docs/01-requirements.md)
- [02 方案与取舍](docs/02-solution.md)
- [03 评估与限制](docs/03-evaluation.md)
- [04 AI 使用与贡献边界](docs/04-ai-usage.md)
- [05 复盘](docs/05-retrospective.md)

## 已知限制

合成规则使用标签和关键词，没有真实版权核验、视频识别或平台审核；单次只处理一个素材，没有批量最优调度。固定时刻冲突后最多搜索未来七天形成提案，返回最多五个候选。待确认草稿也占用本地规划时段，目前没有取消、确认升级或过期释放接口。

系统保证的是单个 SQLite 数据库内的事务和唯一性，不声称实现分布式发布或生产级 Runtime。模型不可用会明确未完成，不会悄悄切换 Mock 冒充真实决策。
