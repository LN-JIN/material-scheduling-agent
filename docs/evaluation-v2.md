# 03 评估、实际结果与证据边界

## 结论与计数口径

当前真实模型评估使用 `qwen3.8-max`。修复后全量评估的 14 个场景全部达到各自预期，共通过 176/176 条断言，记录 61 次实际服务响应。随后对 5 个关键场景各独立运行 3 次，15/15 次达到预期，通过 210/210 条断言，记录 84 次实际服务响应。

这些结果说明本次有限合成样例中的任务和工程约束符合预期，不是通用准确率、线上成功率或泛化可靠性证明。

| 证据批次 | 不同场景数 | 场景执行通过/总次数 | 断言通过/总数 | `provider_calls` | 原始记录 |
|---|---:|---:|---:|---:|---|
| 首轮真实调用 v1 | 14 | 10/14 | 147/164 | 56 | [报告](../examples/iterations/v1/report.md)、[summary](../examples/iterations/v1/summary.json) |
| 修复后全量真实调用 | 14 | 14/14 | 176/176 | 61 | [报告](../examples/live/report.md)、[summary](../examples/live/summary.json) |
| 修复后关键场景重复调用 | 5，每项 3 次 | 15/15 | 210/210 | 84 | [报告](../examples/live-repeats/report.md)、[summary](../examples/live-repeats/summary.json) |
| 离线流程模拟 | 14 | 14/14 | 163/163 | 0 | [报告](../examples/offline/report.md)、[summary](../examples/offline/summary.json) |
| 独立无网络验收测试 | 不按模型场景计数 | 58/58 个测试 | 不与上述断言合并 | 0 | [测试输出](../examples/test-results.txt)、[验收报告](acceptance-report.md) |

“场景数”是不同测试条件数；“执行次数”包括重复运行；一轮执行又含多项业务和证据断言。`provider_calls` 按 Trace 中实际服务返回的 `response_id` 计数，一次场景可以有多次模型决策。重复提交场景直接恢复已有草稿，没有新模型调用。因此修复后两批合计是 14 个不同场景、29 次场景执行和 145 次服务响应；不能称为 29 个不同场景或 145 个独立任务。5 个重复场景是全量场景的子集。

独立测试输出记录 `Ran 58 tests in 4.718s / OK`。验收报告此前记录的 `58 tests in 7.054s / OK` 属于另一次运行；这两个耗时不能合并为同一次执行，也不作为性能基准。

## 怎样执行和判断

[评估器](../scripts/evaluate.py)没有直接调用内部函数拼装成功结果，而是为每次场景启动独立的 `python -m src.main` 进程，读取实际业务 JSON、JSONL Trace 和 SQLite 数据库。通常每个场景使用隔离数据库；`duplicate-submit` 特意复用 `normal` 的数据库，以另一个 CLI 进程验证恢复。

评估器在执行后导出 `requests`、`reservations`、`drafts` 和 `PRAGMA integrity_check` 的数据库快照，比较预期状态、真实草稿数、`draft_id`、原请求身份、原时间、确认边界、决策预算及动作路径。真实调用还检查服务返回的模型标识和响应 ID，不能仅凭请求里写了模型名认定调用成功。

用例名称、预期答案、断言及 `--fault` 故障开关都不作为模型输入。故障开关作用于本地工具或存储，模型只能看到执行后产生的工具观察，例如 `slot_conflict` 或 `save_response_lost`，再决定下一动作。素材和排期为合成数据；模型可以读取业务资料，但不会收到评估答案。

各批次的 `summary.json` 记录运行 ID、逐项断言、产物 SHA-256 和源码/数据/评估器 SHA-256；修复后的全量、重复及离线三批评估均记录 `source_unchanged_during_run=true`，v1 尚无此字段。首轮另有完整 [source_snapshot](../examples/iterations/v1/source_snapshot/)，用于核对失败发生时的实现。归档保留原输入、输出、Trace、数据库快照和失败结果。

## 六项必验点：预期与实际

下表中的状态均来自修复后的真实 CLI 输出和实际数据库快照；文件链接可逐项核对。`pending_confirmation` 表示本地待确认提案已保存，未完成确认或发布。

| 验收点 | 预期 | 本次实际 | 证据 |
|---|---|---|---|
| 1. 正常链路 | 读取、审核、查询、保存并观察结果后结束；库中一份草稿 | `normal` 5 步完成，原 17:00 保存，`completed`、`saved`、`not_required`，1 份草稿 | [输入](../examples/live/normal.input.json)、[输出](../examples/live/normal.output.json)、[Trace](../examples/live/normal.trace.jsonl)、[DB](../examples/live/normal.db-snapshot.json) |
| 2. 信息缺失 | 返回缺失字段及 `needs_input`，不虚构内容，不保存 | `missing-information` 3 步结束，`needs_input`，0 份草稿 | [输入](../examples/live/missing-information.input.json)、[输出](../examples/live/missing-information.output.json)、[Trace](../examples/live/missing-information.trace.jsonl)、[DB](../examples/live/missing-information.db-snapshot.json) |
| 3a. 硬规则 | `blocked`，不能保存 | `rule-block` 3 步结束，`blocked`，0 份草稿 | [输出](../examples/live/rule-block.output.json)、[Trace](../examples/live/rule-block.trace.jsonl)、[DB](../examples/live/rule-block.db-snapshot.json) |
| 3b. 软风险 | 可保存提案，但保留风险证据并待确认 | `risk-confirmation` 5 步保存 1 份草稿，`pending_confirmation`；`pending_items` 保留 `risk_terms_1` 与“绝对领先”证据 | [输出](../examples/live/risk-confirmation.output.json)、[Trace](../examples/live/risk-confirmation.trace.jsonl)、[DB](../examples/live/risk-confirmation.db-snapshot.json) |
| 4a. 固定时刻冲突 | 改期只形成待确认提案 | `time-conflict` 原 18:00 冲突，模型显式选 19:00 保存；`pending`，1 份草稿 | [输出](../examples/live/time-conflict.output.json)、[Trace](../examples/live/time-conflict.trace.jsonl)、[DB](../examples/live/time-conflict.db-snapshot.json) |
| 4b. 已授权窗口 | 整段在窗口内且无软风险时可完成，不额外要求改时确认 | `window-authorized` 在 18:00–21:00 授权内保存 19:00–20:00，`completed`、`not_required`，1 份草稿 | [输入](../examples/live/window-authorized.input.json)、[输出](../examples/live/window-authorized.output.json)、[Trace](../examples/live/window-authorized.trace.jsonl)、[DB](../examples/live/window-authorized.db-snapshot.json) |
| 4c. 查询后新增占用 | 保存时拒绝过期候选，重新查询再选时；固定改期待确认 | `conflict-on-save` 首次保存 17:00 遇真实事务冲突；重查后模型显式选 19:00，7 步保存 1 份待确认草稿 | [输出](../examples/live/conflict-on-save.output.json)、[Trace](../examples/live/conflict-on-save.trace.jsonl)、[DB](../examples/live/conflict-on-save.db-snapshot.json) |
| 5. 重复提交 | 跨进程恢复同一草稿，不重复占位 | `duplicate-submit` 与 `normal` 的 `draft_id` 相同；`steps=0`，`idempotent_recovery`，库中仍为 1 份草稿，0 次新模型响应 | [输入](../examples/live/duplicate-submit.input.json)、[输出](../examples/live/duplicate-submit.output.json)、[Trace](../examples/live/duplicate-submit.trace.jsonl)、[DB](../examples/live/duplicate-submit.db-snapshot.json) |
| 6a. 持续保存失败 | 提交前回滚，明确失败，不伪造草稿 ID | `save-failure` 5 步结束，`failed`、`draft_id=null`，0 份草稿 | [输出](../examples/live/save-failure.output.json)、[Trace](../examples/live/save-failure.trace.jsonl)、[DB](../examples/live/save-failure.db-snapshot.json) |
| 6b. 提交后响应丢失 | 先为 `unknown`，相同原请求核对恢复，只保存一份 | `save-response-lost` 第 4 步观察 `unknown`；第 5 步再次保存返回 `idempotent=true`；第 6 步结束为 `completed`，1 份草稿 | [输出](../examples/live/save-response-lost.output.json)、[Trace](../examples/live/save-response-lost.trace.jsonl)、[DB](../examples/live/save-response-lost.db-snapshot.json) |

保存前一次故障的回滚与恢复、请求键改载荷拒绝、独立连接竞争、等价时区与相邻区间、伪造动作及模型异常等由 [58 项验收测试](../tests/test_acceptance.py)另行覆盖；不能把这些脚本模型测试写成新增的真实模型场景。

其余真实场景的预期及实际如下，完整断言见 [全量 summary](../examples/live/summary.json)：

| 场景 | 预期 | 实际 | 证据 |
|---|---|---|---|
| `tool-failure` | 首次工具临时失败后恢复 | 先重试 `read_material`，6 步 `completed`，1 份草稿 | [输出](../examples/live/tool-failure.output.json)、[Trace](../examples/live/tool-failure.trace.jsonl)、[DB](../examples/live/tool-failure.db-snapshot.json) |
| `step-limit` | 预算耗尽明确未完成 | 2 步后 `incomplete`、`max_steps_reached`，0 份草稿 | [输出](../examples/live/step-limit.output.json)、[Trace](../examples/live/step-limit.trace.jsonl)、[DB](../examples/live/step-limit.db-snapshot.json) |
| `changed-slot` | 19:00 也被占用后选择 20:00 | 模型显式选择 20:00 保存，5 步 `pending_confirmation`，1 份草稿 | [输出](../examples/live/changed-slot.output.json)、[Trace](../examples/live/changed-slot.trace.jsonl)、[DB](../examples/live/changed-slot.db-snapshot.json) |
| `no-slot` | 授权范围内无位则不保存 | 4 步 `no_available_slot`，0 份草稿 | [输入](../examples/live/no-slot.input.json)、[输出](../examples/live/no-slot.output.json)、[Trace](../examples/live/no-slot.trace.jsonl)、[DB](../examples/live/no-slot.db-snapshot.json) |

## 两条实际决策路径

第一条来自 [normal Trace](../examples/live/normal.trace.jsonl)：

```text
1 read_material(M01) -> 素材存在
2 check_rules(M01) -> pass
3 query_slots(M01) -> 17:00 可用
4 save_draft(suggested_time=2026-11-01T17:00:00+08:00) -> saved
5 finish -> completed
```

模型第 4 步显式给出所选时间，第 5 步在观察保存结果后结束。实际草稿为 `draft_04e562c95c444ca4b460bee305cf7fa9`，与 [输出](../examples/live/normal.output.json)及 [DB 快照](../examples/live/normal.db-snapshot.json)一致；5 步均有真实服务响应。

第二条来自 [conflict-on-save Trace](../examples/live/conflict-on-save.trace.jsonl)：

```text
1 read_material -> 素材存在
2 check_rules -> pass
3 query_slots -> 17:00 当时可用
4 save_draft(suggested_time=17:00) -> conflict / slot_conflict
5 query_slots -> 17:00 已占用，新候选从 19:00 开始
6 save_draft(suggested_time=19:00) -> saved / confirmation_status=pending
7 finish -> pending_confirmation
```

故障注入实际新增 17:00–18:00 外部占用，保存事务检测冲突后拒绝旧候选。模型收到新观察后重查、显式选 19:00，再观察保存结果结束；不是预先写死一条成功路径。[DB 快照](../examples/live/conflict-on-save.db-snapshot.json)保留外部占用和唯一的 19:00 草稿，未出现过期时段草稿。固定原要求仍是 17:00，改期没有被自动确认。

另一个恢复分支见 [save-response-lost Trace](../examples/live/save-response-lost.trace.jsonl)：第 4 步工具在提交后返回 `unknown`，模型第 5 步用相同请求和时间核对，得到 `saved`、`idempotent=true`，随后结束。这里的 `unknown` 不能等同于“未写入”或确定失败；[数据库](../examples/live/save-response-lost.db-snapshot.json)只有同一份草稿。

## 成对证据：占用变化与授权变化

`time-conflict` 与 `changed-slot` 使用字节相同的 [固定时间请求](../examples/live/time-conflict.input.json)和 [对照请求](../examples/live/changed-slot.input.json)，分别在隔离数据库执行。差别是后者的 [排期资料](../examples/live/fixtures/occupied-19/schedules.json)在原有 18:00–19:00 占用外，增加了 19:00–20:00 占用；素材和规则保持相同。

| 条件 | 工具第 3 步首候选 | 模型第 4 步 `save_draft.arguments.suggested_time` | 实际保存与确认状态 |
|---|---|---|---|
| [基准 Trace](../examples/live/time-conflict.trace.jsonl) | 19:00 | `2026-11-01T19:00:00+08:00` | 19:00，`pending` |
| [新增占用 Trace](../examples/live/changed-slot.trace.jsonl) | 20:00 | `2026-11-01T20:00:00+08:00` | 20:00，`pending` |

这里同时核对工具观察、模型显式参数和落库时间，才支持“本次模型根据新可用性选择了不同时间”。仅有最终 `suggested_time` 从 19:00 变 20:00 不足以证明模型选时，因为未保存时导出器也可能展示工具首候选。

授权对照则比较 `time-conflict` 与 `window-authorized`：两者原时间均为 18:00，实际都保存到 19:00；固定要求返回 `pending` 并列出 `time_change`，窗口请求已授权 18:00–21:00，完整素材区间落在窗口内，返回 `not_required`、无改期待确认项。它证明的是这两次运行遵守原始授权差别；软风险仍需待确认，窗口授权不能清除风险。

## 重复运行结果

重复批次运行 ID 为 `20261030T011613Z-d3322d`。每项运行使用独立进程和独立数据库，3 次结果如下，逐次产物以 `.r01`、`.r02`、`.r03` 命名并保存在 [live-repeats](../examples/live-repeats/)。

| 场景 | 执行通过/总次数 | 断言通过/总数 | 服务响应合计 | 每次实际终态 |
|---|---:|---:|---:|---|
| `time-conflict` | 3/3 | 42/42 | 15 | `pending_confirmation` |
| `window-authorized` | 3/3 | 36/36 | 15 | `completed` |
| `conflict-on-save` | 3/3 | 45/45 | 21 | `pending_confirmation` |
| `save-response-lost` | 3/3 | 45/45 | 18 | `completed` |
| `changed-slot` | 3/3 | 42/42 | 15 | `pending_confirmation` |
| 合计 | 15/15 | 210/210 | 84 | 见[逐次报告](../examples/live-repeats/report.md)及[断言](../examples/live-repeats/summary.json) |

3 次重复是对这些已知条件的补充观察，样本较少且未引入新场景，不能据此推导一般可靠性概率。

## 首轮失败与修复可比性

首轮运行 `20261030T010339Z-465443` 的真实结果为 10/14 通过。四个失败是 `time-conflict`、`risk-confirmation`、`conflict-on-save`、`changed-slot`，均未完成预期的待确认草稿保存，最终为 `incomplete`、0 份草稿。模型把“需要后续确认”理解成“本地草稿也不能先保存”，在工具提供候选或软风险结果后提前 `finish`。[初轮报告](../examples/iterations/v1/report.md)及 [完整归档](../examples/iterations/v1/)保留这些失败。

修复明确了 `save_draft` 只记录本地提案，保存 `pending` 不等于用户已确认、排期已生效或已发布；固定改期与软风险可先保存待确认草稿。工具的业务授权和存储约束保留，原有“必须保存提案”“改期和软风险不得自动确认”等业务断言没有放宽。后续评估额外增加 `model_explicitly_selects_saved_time`，要求非幂等恢复的成功保存存在模型显式选时参数，并与真实保存时间一致。因此两轮的全部断言集合并非完全相同，164 与 176 的断言总数也不应直接当成同一分母比较。

特别是 v1 的 19:00→20:00 仅证明工具首候选随占用改变；那两例没有模型保存动作，不能回写为“模型已成功选时”。后续显式动作与落库证据才支持修复后的结论。

## 复现与未覆盖范围

在仓库根目录运行下列命令；真实模式要求调用凭据已在本地环境中配置。使用新的输出目录可保留已归档证据。

```powershell
python -m unittest discover -s tests -v
python scripts/evaluate.py --mode offline --out-dir .runtime/repro-offline
python scripts/evaluate.py --mode live --out-dir .runtime/repro-live
python scripts/evaluate.py --mode live --cases time-conflict,window-authorized,conflict-on-save,save-response-lost,changed-slot --repeat 3 --out-dir .runtime/repro-live-repeats
```

实际已执行命令、耗时和返回码见[全量运行日志](../examples/live/run-log.txt)与[重复运行日志](../examples/live-repeats/run-log.txt)。重新调用模型可能产生不同动作、耗时与结果，应生成新的证据，不能覆盖后声称仍是同一轮运行。

本项目未验证真实平台审核政策、真实版权证据、正式确认流程、外部发布接口、分布式数据库或生产流量。离线模拟和脚本模型用于验证流程及防绕过边界；真实模型证据限于上述合成场景。已保存请求的幂等恢复返回原草稿快照，不等于按最新规则重新审核，也不等于确认或发布。
