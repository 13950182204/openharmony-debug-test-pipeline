# MR 审查协议

本参考用于真实 MR 创建后或显式请求审查既有 MR 时的六维审查。

## 目录

- [权威与上下文](#authority-and-context)
- [审查者边界](#reviewer-boundaries)
- [Prompt 与发现契约](#prompt-and-finding-contract)
- [聚合与修复门槛](#aggregation-and-repair-gates)
- [重试与复审](#retry-and-re-review)
- [最终对话报告](#final-conversation-report)

## 权威与上下文

GitLab MR 是权威。解析项目路径与 MR IID，然后查询当前 MR 元数据与变更：

```bash
glab api --hostname 192.168.11.238 \
  "projects/<encoded-project-path>/merge_requests/<iid>"
glab api --hostname 192.168.11.238 \
  "projects/<encoded-project-path>/merge_requests/<iid>/changes"
```

用 `diff_refs.base_sha` 作 base，用当前 MR `sha` 作 head。把 API 的 `source_branch`、`target_branch` 与 `/changes` 路径放进 prompt。用以下命令本地校验精确 diff：

```bash
git diff --name-status <base_sha>...<head_sha>
git diff --check <base_sha>...<head_sha>
```

本地检出与 MR 源分支不一致时，不要用 `HEAD` 代替。审查用 MR head 处的干净审查 worktree；修复用能推送 MR 源分支的干净 worktree。

## 审查者边界

每个类别恰好一个独立的只读审查 Subagent。类别刻意保持狭窄：

| 类别 | 报告 | 不要报告 |
| --- | --- | --- |
| 安全 | 权限绕过、敏感数据暴露、不安全的输入/路径处理、注入、资源耗尽与安全回归 | 无安全影响的泛泛健壮性或风格问题 |
| 代码质量 | 错误的 API 契约、错误处理、资源生命周期、兼容性与有风险的本地设计 | 纯命名或格式偏好 |
| 缺陷 | 可观察的功能回归、错误状态迁移、边界错误与损坏的用户/测试行为 | 无具体触发条件的理论问题 |
| 竞态 | 异步顺序、生命周期回调、并发读-改-写、陈旧事件与清理交错 | 普通顺序逻辑错误 |
| 测试稳定性 | 不稳定时序、非确定性顺序、状态泄漏、不完整清理、设备/环境耦合与重复运行失败 | 除非直接破坏测试稳定，否则不报产品行为缺陷 |
| 可维护性 | 重复、隐藏耦合、归属不清、脆弱的测试数据，以及可能造成未来缺陷或高维护成本的改动 | 主观风格偏好与低影响重构 |

同一根因可能被多个类别发现。父 Agent 保留一条规范发现，并把其他类别记为关联标签。

## Prompt 与发现契约

每个 prompt 必须包含：

- 仓库绝对路径。
- 可用时的 MR IID 与 URL。
- 源与目标分支名。
- 精确的 `base_sha` 与 `head_sha`。
- `git diff --name-status base_sha...head_sha` 输出或等价的精确路径清单。
- 分配的类别及其上述边界。
- 只读约束：不编辑文件、不提交、不推送、不发 MR 评论、不清理 worktree。

使用这个 prompt 形状，替换方括号值：

```text
Review MR ![iid] in [repo] for [category] only.
Authoritative diff: [base_sha]...[head_sha].
Source branch: [source_branch]. Target branch: [target_branch].
Inspect the exact diff and necessary base-code context. Do not modify files,
commit, push, comment on the MR, or include unrelated worktree changes.
Report only evidence-backed findings. For each finding provide:
severity (P0/P1/P2/P3), confidence (high/medium/low), absolute file path,
one-based line, short title, trigger, evidence, impact, and recommendation.
If no issue is found, return exactly: no findings.
```

聚合前把每个结果规范化为这个逻辑形状：

```text
category: security|code-quality|bug|race|test-stability|maintainability
severity: P0|P1|P2|P3
confidence: high|medium|low
path: /absolute/path/to/file
line: 123
title: concise problem statement
trigger: concrete triggering condition
evidence: changed code and relevant base behavior
impact: user, test, data, security, or maintenance impact
recommendation: minimal repair direction
```

## 聚合与修复门槛

严重级基于影响，不基于 diff 大小：

- `P0`：阻塞发布的安全或数据完整性失败，或大范围崩溃/损坏路径。
- `P1`：高置信度功能失败、安全绕过、持久数据丢失，或可使 MR 主行为失效的竞态。
- `P2`：有界触发条件下的明显回归、资源泄漏、间歇性失败或运维风险。
- `P3`：低风险缺陷或可维护性关切，应记录但不自动修复。

自动修复要求高置信度或可复现的发现、具体触发条件，以及已被 MR 改动的路径。P0-P2 发现符合条件；P3 不符合。编辑前，父 Agent 必须检查精确 diff 并核实发现，而不是盲目套用建议。

修复必须：

- 留在既有 MR 源分支及其干净源码 worktree 上。
- 只碰原始 MR 路径。
- 保留原始 MR 描述并避免 MR 评论。
- 使用精确路径暂存，跟进 commit 使用适用的 OpenHarmony 标题/正文格式。
- 推送前运行 `git diff --check` 与相关定向测试。

任何修复需要新路径、改变公共契约、大范围重构或未决产品决策时，停止自动修复并报告范围扩大。

## 重试与复审

先并行生成全部六个审查者，再等待。等待六个终态全部到达。只对失败的类别用兜底模型重试一次。重试后仍缺类别会使审查不完整并阻止自动修复。

修复推送后，从 GitLab 刷新 `base_sha`、`head_sha` 与变更路径。六个类别全部再跑恰好一次。不要只审查变更的 hunk：对照最新的权威 base 审查完整的最新 MR diff。剩余确认的 P0-P2 发现或任何不完整类别都会让 MR 保持未解决并结束自动循环。

## 最终对话报告

使用这个顺序：

1. 审查范围：MR IID、源/目标分支、base/head SHA 与变更路径数。
2. 发现按严重级排序，含类别、置信度、path:line、触发条件、影响与建议。
3. 干净类别显式标记 `no findings`。
4. 有修复时，修复 commit 与推送的源 SHA。
5. 测试命令/结果与任何不可用的验证。
6. 复审状态与未解决风险。

任何类别不完整、任何确认的 P0-P2 发现未解决、或必需测试未运行且差距重大时，不要声称「审查通过」。
