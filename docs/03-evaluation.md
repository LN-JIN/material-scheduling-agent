# 03 修订评估与证据边界

当前修订已实跑79项测试、14组原验收场景，以及4组自然语言偏好条件各2次。所有结果为实际执行记录，不以旧版通过记录代替本轮验证。

| 验证 | 实际结果 | 证据 |
|---|---|---|
| 程序回归与原生SQLite故障 | 79/79，8.304秒（单次记录，不是性能基准） | [日志](../examples/revision/test-results.txt)、[新增21项](../tests/test_revision.py) |
| 无密钥场景 | 14/14 | [报告](../examples/revision/offline/report.md) |
| 真实Qwen场景 | 14/14，176/176断言，61次服务响应 | [报告](../examples/revision/live/report.md)、[原始断言与哈希](../examples/revision/live/summary.json) |
| 真实模型偏好选择 | 8/8；安全边界8/8；40次响应 | [对照报告](../examples/revision/preferences/report.md)、[原始证据](../examples/revision/preferences/summary.json) |
| 缺陷前后对照 | 规则缺失拒绝；数据库拒写明确failed且零草稿；损坏库结构化rejected | [修复后](../examples/revision/failures/results.json)、[修复前](../examples/iterations/v2/self-review/results.json) |

## 六个必验点

正常、素材缺失、硬规则、软风险、固定冲突、窗口授权、保存时冲突、重复提交、保存失败均见当前14组报告。每个场景旁边有 input/output/trace/db-snapshot 四份文件。normal为5步模型循环；conflict-on-save为观察保存冲突后重查再保存；save-response-lost为unknown后同请求核对。duplicate-submit零新模型响应，不能计作新的模型决策路径。上限场景保持incomplete。

## 新对照实际证明了什么

候选时间顺序相同，客服优先选择19:30，互动指数优先选择20:00；改变指数后选择19:15；客服全不在线时依照用户明确的备选偏好选择20:00。两组对照使用字节相同请求，仅改变候选事实。期望时间、case名称、结构化标签不会发给模型。

最早候选基线偏好命中0/8，全部安全；模型8/8；接收人工标注结构化目标的规则oracle也8/8。该oracle只验证选择算法，不执行自然语言理解或完整工具链，所以不能当成公平端到端竞品。结果支持模型在这4个已知样本上将自然语言偏好映射到事实候选，不支持“模型普遍优于规则”或一般准确率。

候选事实是合成指标，不是实际流量预测。客服缺口由评估器根据候选数据计算；模型原始简短依据保留，不用关键词匹配冒充语义评审。服务延迟与token用量逐次保存，不估算未经核实的金额。

## 自审发现与修复

旧版缺失rules.json会当作空规则，导致软风险素材pass并无需确认。现在必需文件缺失或格式/类型不合法时拒绝；显式空对象表示只执行内置字段检查。未知severity也不能静默降级，required_field=null不会被str(None)误判为已提供。

旧版主要覆盖人为OSError，原生SQLite异常会被误标model_call_failed。现在根据事务阶段、是否回滚分类failed/unknown，锁竞争可重试；初始化或绑定错误明确storage_unavailable。测试使用真实触发器、query_only和第二数据库连接，非仅Mock异常。

发布间隔是合成业务规则，同渠道发布保留一段排期窗口；与视频播放秒数分开。新测试使用37分钟、+05:45跨午夜、另一渠道及组合风险。新条件用于回归，已被开发看到，不声称盲测。

## 复跑与版本

```sh
python -m scripts.verify
python -m scripts.evaluate --mode live --out-dir .runtime/new-live
python -m scripts.compare_preferences --mode live --out-dir .runtime/new-preferences --repeat 2
```

真实命令需自行配置密钥。统一验证入口清除密钥，运行离线行为并核对归档哈希；它不重新调用真实模型。GitHub工作流覆盖Python3.10/3.12和Linux/Windows，远端运行结论以Actions记录为准。

原examples/live、live-repeats、offline属于v2，哈希与[原源码](../examples/iterations/v2/source_snapshot/)核对；v1失败保持独立快照。[旧评估](evaluation-v2.md)仅为历史。当前examples/revision/live和preferences分别绑定本轮源码、输入与结果。文件哈希只证明一致性，不是服务方签名。

本轮独立验收Agent完成新增测试并复跑21项后因额度停止；79项全量、真实实验、最终复跑由主Agent执行，不能宣称完成另一轮独立全量审计。Trace仍于运行结束写出，没有中途崩溃日志保障或完整Agent断点恢复；未新增真实发布、人工确认生效或草稿取消接口。
