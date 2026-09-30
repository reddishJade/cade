# Experience Memory v0：验证与 coding 实验

基线为 `491d57a4`，实验使用当前修改后的 Cade harness。设计与历史依据见
[设计审查](experience-memory-design.md)。这里区分 runtime 回归与真实 solver 收益；
前者通过不代表后者成立。

## 已完成的本地验证

- Ruff check / format、全量 Pyright：通过。
- 默认 pytest：36 passed；E2E 和外部环境测试按仓库约定不进入默认套件。
- Experience E2E：8 passed，真实 build_app/session/tools，只在 provider 网络边界
  使用协议驱动，不用于证明模型收益。
- CLI `cade --help`：通过。

E2E 覆盖可见写入后新 session recall、弱查询和标识符子串排除、scope 不能绕过
anchor、缺字段/空字段/重复字段排除、人工调整字段顺序、只读无副作用、resume
保留普通规则而排除经验，以及 ContextPolicy 裁剪后完整证据仍在历史。
此外，真实待审批 bash 确认固定人工 router 只调用本地回调，没有 reviewer inference。

按仓库 testing.md，E2E 源码和运行轨迹是 local-only，不提交到 origin。复现：

```sh
.venv/bin/pytest src/cade/tests/e2e/test_experience_memory_e2e.py \
  --override-ini 'addopts=' -m e2e -q --tb=short
```

轨迹和命令在 `e2e-results/experience-memory/`。

## Coding-task 实验设计

公开可复现脚本：[evals/experience_memory.py](../evals/experience_memory.py)。
使用真实历史 commit `972a09363bbec9d83067179a8cc06e9df16efdcb` 前一版本，
修复 fd 缺失时的 .gitignore/.fdignore whitelist 优先级及被排除父目录的剪枝。
验收包含 Python/ripgrep 的 find/glob、显式文件、普通 grep 忽略行为、隐藏文件
和截断，共 23 项；源码缺陷版本通过 17 项，历史修复版本通过全部 23 项。
验收代码由 Host 在 solver 完成后独立执行，不提供给 solver。

每个条件从同一源码快照开始，使用配置中的真实 DeepSeek `deepseek-flash`，
18 次模型请求/step 的固定上限、240 秒超时、相同工具和题目。第一次 explicit
recall 被统一要求，A 返回空；因此实验不评价自然 recall 时机，也不是旧提示词
与新提示词的 A/B。所有条件含同一新增 Memory 指导。

- A：没有 Experience。
- B：从真实历史修复提取的完整已验证经验，属于 oracle 对照。
- C-irrelevant：anchor 不匹配。
- C-stale：anchor 匹配但修复建议错误；synthetic control 明确标记未验证。
- D：字段仍完整，但 lesson 被 160 行 transcript 式噪声淹没。
- E：只剩一句 atomic fact，缺少原因和适用边界。

`sparse` 使用生产 recall gate；`form` 只在实验装配中让首次 recall 返回各组原文，
隔离 gate 和 form 的影响。两组都使用真实 provider、普通工具结果和 ContextPolicy。
不存在脚本预设 solver 探索路径或 mock 的成功分数。

快照建立空 git 仓库，阻止向上发现当前 repo 历史；不包含 gold patch。真实用户
Memory 指向空的实验文件，Skills discovery 返回空 registry；不加载用户 hooks
或外部 MCP 配置。审批使用固定本地回调并在启动时断言，shell 继续使用现有
workspace/network sandbox。公开性经 GitHub API 的 `private=false` 核查。

此前三类诊断尝试不计入正式结果：默认网络不可达；approval_router 接线缺陷
导致 reviewer 调用；快照向上发现父 git 仓库。后一轮虽然修正 router，但仍有
git 历史泄漏风险。所有有效结果必须来自 `sparse-isolated` / `form-isolated`。

## 结果与解释

正式结果保存在 `eval-results/experience-memory/sparse-isolated/`，6 个条件各运行一次：

| 输入 | 任务成功 | 验收 | 工具调用 | 检查与 shell 调用 | 首次关键文件调用 | 秒 |
| --- | --- | --- | --- | --- | --- | --- |
| 没有经验 | 否 | 17/23 | 27 | 26 | 6 | 56.4 |
| 完整验证经验 | 否 | 17/23 | 26 | 25 | 4 | 139.0 |
| 无关经验 | 否 | 17/23 | 26 | 25 | 4 | 108.8 |
| 错误修复建议 | 否 | 17/23 | 27 | 26 | 6 | 70.3 |
| 冗长 transcript 形式 | 否 | 验收异常中断 | 23 | 17 | 4 | 194.1 |
| 过度压缩的 atomic fact | 否 | 验收异常中断 | 29 | 27 | 6 | 181.7 |

所有条件都在 18 次模型请求时达到 step_limit；没有条件完成任务。完整经验
提前两次调用触及关键文件，但总体仅少一次工具调用，耗时和 tokens 更高，
没有证据证明本轮 Experience 带来收益。任务预算存在明显 floor effect，
不能把全部失败解释成 Memory 无效。

无关记录和 atomic fact 被生产 gate 排除；错误经验和完整 transcript 被召回。
前四组没有新增原本通过的验收失败；后两组留下未完成修改导致的缺失函数引用，
属于具体的新增损坏风险。atomic fact 实际没有进入 context，因此它的损坏也
提示 solver 随机性和预算截断是重要混杂因素；不能把 transcript 组的异常全部
归因于 Memory，也不能以两次 C 的未新增损坏宣称安全。

有效运行共 108 次 solver 请求，harness 估算输入 3,079,026 tokens、输出
156,997 tokens。诊断试跑及其中的 reviewer 调用不计入这些数字，也未汇总为
账单。单组估算如下：

| 输入 | 输入 tokens | 输出 tokens |
| --- | --- | --- |
| 没有经验 | 424,208 | 9,898 |
| 完整验证经验 | 516,405 | 28,660 |
| 无关经验 | 430,496 | 21,975 |
| 错误修复建议 | 387,194 | 13,040 |
| 冗长 transcript 形式 | 763,914 | 43,467 |
| 过度压缩的 atomic fact | 556,809 | 39,957 |

`form` 模式的实验装配已提供，但本轮未执行。主实验全部达到预算上限，继续
比较形式也不足以可靠判定成功率改善；先记录这个限制，后续应预先校准任务
预算、保留全部重复运行，再评估受控形式及跨任务迁移，不挑选成功 seed。

报告包含工具调用、elapsed、模型请求数、估算输入/输出 tokens、关键文件相关
读取/命令首次出现的序号、独立验收、patch 和完整 session。`inspection_and_shell_calls`
包括 bash 测试与验证，不能把它全部解释为纯探索。首次关键文件调用也不能证明
根因已经得到验证。

本试验重新呈现同一历史缺陷，不证明跨任务迁移或正常在线写入质量。每个条件
仅一次运行，且 budget 可能造成 floor effect；不能据此宣称统计显著或 C 永不损害
baseline。不得将 provider_error、受污染试跑或协议驱动计为模型行为结果。

## 成本与复现

Memory 不新增 Host provider 请求。固定 Memory protocol 的本地 token 估算由
255 增到约 490（约 +235）；工具描述也略增。这是正常请求中可见的指导成本，
不是零成本。普通独立 write 工具仍可能带来后续正常模型轮次；E2E 观测到一次
write 后 final 需要 2 次请求。完整 recall、当前代码检查和验证占用现有预算。

以下命令会使用当前配置的真实 provider，必须在允许该外部调用的环境中运行；
每次使用新的输出目录。默认 repeats=2；本轮探索试验显式使用 repeats=1。

```sh
.venv/bin/python evals/experience_memory.py --preflight-only \
  --output eval-results/experience-memory/preflight-new
.venv/bin/python evals/experience_memory.py --repeats 1 \
  --output eval-results/experience-memory/sparse-new
.venv/bin/python evals/experience_memory.py --mode form --repeats 1 \
  --output eval-results/experience-memory/form-new
```

脚本保留超时的已发生工具事件与最终源码验收；输入/输出 tokens 是 harness 的
估算指标，不提供费用或精确账单归因。

本轮收尾还将“Memory 是历史假设”的表述限定为 Experience，避免把普通用户
约束也降为代码假设；补充直接 Python 验证应设置 PYTHONPATH=workspace/src。
这两项文字澄清未回跑已完成的单轮实验，未来复现应以保存的实际任务和版本为准。
