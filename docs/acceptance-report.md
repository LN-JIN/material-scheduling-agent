# 独立验收报告

## 最终结论

**通过项目限定范围的验收，未发现阻断交付的缺陷。** 修订后14个真实模型场景全部达到预期；5个关键场景各重复3次，15次均达到预期。最终独立验收核查了实际输入、JSONL Trace、输出、数据库快照和本地原始 SQLite，重新计算产物与源码哈希，并只独立重跑58项测试一次，全部通过。

这不是一般场景准确率或生产可靠性结论。真实模型运行由主 Agent 执行；本次独立验收没有调用模型、没有读取或打印真实密钥、没有修改核心源码、历史运行产物或测试，也没有操作外部服务。

## 独立核对方法

- 逐一重算三批共43次场景执行的172个产物 SHA-256，与各自 summary 一致。产物为 input、output、trace 和 db-snapshot。
- 核验每批11个源码、数据和评估器哈希，共33个。后续 live 和 repeats 均匹配当前冻结源码；v1 匹配其 source_snapshot，没有用当前实现代替初轮实现。
- 以 SQLite `mode=ro` 打开本地保留的原始数据库，对43份快照的 requests、drafts、reservations 和实际 PRAGMA integrity_check 逐项比对，完全一致；完整性检查均为 ok。duplicate-submit 按设计复用 normal 数据库。
- 核对原始载荷、载荷哈希、身份、草稿ID、UTC时间列、持久化 result_json 与实际输出。成功保存的模型显式参数、最新候选、工具结果及实际保存时间一致；无草稿的场景没有伪造 draft_id 或 saved。
- 核对逐步原始动作、执行工具和参数、工具观察、连续步数、终态、运行日志stdout和退出码。独立按预期业务状态及真实草稿数重新判定通过情况。
- 201条服务响应均记录 requested_model=response_model=qwen3.8-max、非空且全局唯一的 response_id、正数耗时及生成token。按实际Trace重新计数，与记录一致。重复提交为零新响应，没有计作新模型决策。

哈希和响应元数据提供一致性与可追溯证据，不是服务方签名或不可篡改证明。本次没有独立观察历史网络传输，结论为“产物、实现及数据库一致，未发现伪造证据”，不能绝对证明历史日志无法伪造。源码审查确认工具结果由执行循环记录；模型自报成功不能替代工具返回，对抗测试覆盖了这一点。

## 三批真实模型记录

| 批次 | run_id | 独立核对的通过次数 | 实际服务响应 |
|---|---|---:|---:|
| [初轮v1](../examples/iterations/v1/report.md) | 20261030T010339Z-465443 | 10/14 | 56 |
| [修订后全量](../examples/live/report.md) | 20261030T011004Z-9aa762 | 14/14 | 61 |
| [关键场景重复](../examples/live-repeats/report.md) | 20261030T011613Z-d3322d | 15/15 | 84 |

修订后两批是14个不同场景、29次执行、145条服务响应；不是29个不同场景，也不是145个独立任务。加上v1才是43次执行及201条响应。评估器断言统计为v1的147/164、后续全量176/176、重复批次210/210，不与单元测试数量相加。

## 逐项验收结果

下表来自修订后全量的实际文件；五个重复场景的相同关键约束在三次运行中也全部成立。

| 验收点 | 实际观察和数据库事实 | 结果 |
|---|---|---|
| 正常流程 | [normal](../examples/live/normal.trace.jsonl)：read→check→query→save→finish，5步，17:00保存，completed/saved，库中一条 | 通过 |
| 信息缺失 | [missing-information](../examples/live/missing-information.output.json)：needs_input及真实缺失字段，3步，无草稿 | 通过 |
| 硬阻断 | [rule-block](../examples/live/rule-block.trace.jsonl)：审核后结束，blocked，无草稿；直接保存等绕过另由测试覆盖 | 通过 |
| 软风险 | [risk-confirmation](../examples/live/risk-confirmation.output.json)：保存pending草稿，保留risk_terms_1和“绝对领先”证据，未声称风险消除或用户已确认 | 通过 |
| fixed边界 | [time-conflict](../examples/live/time-conflict.trace.jsonl)：原18:00不变，模型显式选19:00；实际保存仍pending_confirmation，列出改时待确认项 | 通过 |
| window边界 | [window-authorized](../examples/live/window-authorized.input.json)：授权18:00–21:00，完整19:00–20:00落在窗口内，not_required/completed | 通过 |
| 19:00→20:00应变 | time-conflict与[changed-slot](../examples/live/changed-slot.trace.jsonl)业务输入字节相同；后者增加19:00–20:00占用。模型save参数从19:00变20:00，工具与DB同步，两者均pending | 通过 |
| 保存时新增占用 | [conflict-on-save](../examples/live/conflict-on-save.trace.jsonl)：第4步保存17:00被事务拒绝；第5步重查；第6步显式选19:00保存；第7步结束。DB保留新增占用及唯一19:00草稿 | 通过 |
| 提交后响应未知 | [save-response-lost](../examples/live/save-response-lost.trace.jsonl)：第4步unknown；第5步同时间参数重试，得到idempotent=true及原草稿；第6步结束，DB只有一条 | 通过 |
| 跨进程重复提交 | [duplicate-submit](../examples/live/duplicate-submit.trace.jsonl)：steps=0、idempotent_recovery，草稿ID与normal相同，DB仍一条，零新模型响应 | 通过 |
| 永久保存失败 | [save-failure](../examples/live/save-failure.trace.jsonl)：提交前故障retryable=false后结束；failed、draft_id=null，DB无草稿 | 通过 |
| 暂时工具失败 | [tool-failure](../examples/live/tool-failure.trace.jsonl)：read返回可重试错误后模型重试read，再完成保存，6步，DB一条 | 通过 |
| 最大步数 | [step-limit](../examples/live/step-limit.output.json)：两步后incomplete/max_steps_reached，无草稿。最后一步已保存及unknown边界另由测试覆盖 | 通过 |
| 无可用位 | [no-slot](../examples/live/no-slot.trace.jsonl)：无候选后结束，no_available_slot，DB无草稿 | 通过 |
| 可信日志及输出 | 动作与工具参数一致，保存结果与DB一致，拒绝动作不产生伪工具成功。对抗模型不能伪造审核、确认、观察或可信输出 | 通过 |

## 至少两种真实反馈循环

1. **冲突后重查并改选**：conflict-on-save的工具返回slot_conflict后，模型下一次服务响应选择query_slots；观察新候选后，另一次响应显式选择19:00保存。
2. **未知后同参数核对**：save-response-lost返回unknown后，模型下一次响应再次选择save_draft并保留原时间；观察idempotent=true后才finish。
3. **暂时错误后重试**：tool-failure在retryable=true的read错误后重试read；永久保存错误retryable=false后则finish。

前两种在全量批次及三次独立重复中均成立。这些是反馈驱动的不同动作循环，不是把离线模拟或候选变化冒充模型应变；实际业务模型始终只有指定的qwen3.8-max。

## 58项测试复核

实际阅读当前测试源码并核对[现有测试日志](../examples/test-results.txt)，58个方法与当前58个测试方法对应。该文件记录主 Agent 的一次 `58 tests in 4.718s / OK`，没有被本次覆盖。

本次最终验收仅独立运行一次。子进程清空真实模型密钥变量，测试只用OfflineModel、ScriptedModel、临时合成数据和本地SQLite，不进行模型或网络调用：

```text
C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest discover -s tests -v

Ran 58 tests in 4.117s
OK
exit_code=0
```

完整本地日志保留在work/acceptance-final/independent-tests.txt，逐场景机器审计在work/acceptance-final/artifact-audit.json。work/及原始.runtime/数据库按忽略规则不公开交付；上面的命令、结果、公开测试源码和运行快照可供复核。旧版验收报告的7.054秒属于更早独立运行，不能和4.718秒或本次4.117秒合为一次运行；这些耗时不作为性能基准。

| 测试组 | 数量 | 实质覆盖 |
|---|---:|---|
| BasicFlowTests | 13 | 必填项、未知素材、版权元数据、硬阻断、软风险、直接保存及跳过审核 |
| AuthorizationTests | 7 | fixed/window、整个区间授权、素材身份、防伪确认及review |
| PersistenceTests | 13 | 全局请求载荷、重启幂等、旧审核恢复、新快照、排期同步、保存时冲突、双连接竞争、时区及相邻区间 |
| FaultTests | 6 | 提交前回滚、永久失败、提交后unknown、原请求恢复、unknown后模型异常及上限 |
| AgentBoundaryTests | 10 | 错误动作/参数/格式/模型异常的有限预算、防伪成功/观察、脱敏、最后一步保存事实 |
| TrustedOutputTests | 8 | 原时间和实际选时、风险及缺失项、组合待确认项、防止模型覆盖可信输出 |
| CliTests | 1 | 实际离线CLI进程、持久化结果及工具JSONL Trace |

脚本模型测试验证程序边界，不能证明真实模型面对未知输入必然正确。真实模型样例没有覆盖表中每一种对抗情况。

## v1失败与修订核对

对[v1](../examples/iterations/v1/)全部14例核验了产物哈希、Trace、输出、DB和归档源码，独立复核仍为10/14。四项失败没有重新标为通过：

| v1失败 | 实际行为 | 事实 |
|---|---|---|
| time-conflict | 查询首候选19:00后直接finish，无save | incomplete/not_saved，0条 |
| changed-slot | 查询首候选20:00后直接finish，无save | incomplete/not_saved，0条 |
| risk-confirmation | check返回软风险、无硬阻断后finish | incomplete/not_saved，0条 |
| conflict-on-save | save被冲突拒绝，重查后仍finish，无第二次save | incomplete/not_saved，0条 |

v1的19:00→20:00只证明工具候选改变，不能证明模型完成选时；后续全量和重复批次才具有显式参数与落库一致的证据。

归档到当前变化文件为src/agent.py、src/main.py、src/tools.py、scripts/evaluate.py。差异为明确pending本地草稿语义、增加可信待确认项及保留实际提议时段、评估新增显式选时断言和运行前后源码比较。store.py、domain.py、models.py及业务数据未改变。保存数量、固定改期待确认及软风险待确认等原产品断言未放宽。

v1没有source_unchanged_during_run字段，其历史实现通过source_snapshot及哈希核验；后续live/repeats明确记录该字段true，且哈希匹配当前冻结实现。哈希比对不能独立证明历史运行期间每个瞬间都没有改动。

## README承诺与限制

README关于本地草稿、指定live模型、离线身份、fixed/window、事务冲突、幂等恢复、独立任务/保存/确认状态及退出码的承诺，与实际产物或测试一致；本地文件链接可达。实现没有实际发布工具；live失败没有切换OfflineModel的路径。错误决定计入预算，最后一步已保存仍返回任务未完成并保留真实保存事实，也由测试验证。

验收结论不扩大以下边界：

- 合成标签和关键词审核，未验证真实版权、视频识别、外部平台规则或发布。
- 14个已知合成场景、关键条件各3次重复，不能推导泛化准确率、长期稳定性或生产成功率。
- 单个SQLite内的事务和唯一性，没有分布式一致性或外部发布幂等验证。
- pending草稿占用规划时段，没有确认升级、取消或过期释放流程。
- 新ToolSet初始化时读取素材/规则；已保存请求恢复当时草稿和审核，不能冒充按新资料重新审核。变更原始请求载荷需要新request_id。
- 固定时刻替代搜索范围、候选数量有限，未实现批量最优调度。真实样例通常选择首候选；非首候选一致性由脚本模型测试验证。
- 真实服务历史证据依赖已保存响应元数据和本地记录；本次没有新增付费模型调用，没有服务端签名或计费记录。

此前边界验收发现的全局request_id绑定、早期拒绝脱敏、种子排期同步、版权元数据白名单问题，当前对应测试均通过；本次最终审核没有修改核心代码。
