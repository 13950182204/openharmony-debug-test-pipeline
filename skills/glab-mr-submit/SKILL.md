---
name: glab-mr-submit
description: 用 glab 为本地 git 改动创建、审查、修复并更新合规的 GitLab 合并请求，包括结构化的 OpenHarmony 动作/芯片/XTS 标题、自动标签与非阻塞里程碑匹配、标准化的 XTS/DCTS/测试报告 Markdown 记录、自动测试结果截图上传、v6.1.0.31 发布 worktree 隔离、精确文件暂存、创建后六维 Subagent 审查、同 MR 修复更新、worktree 安全检查、推送与创建后 MR 验证。当 agent 被要求提交或准备 MR、对照目标分支审查既有 MR 或当前分支、推送修复分支、附加测试证据、修复审查发现或执行公司 MR 流程时使用。
---

# glab MR 提交

## 用途

用本 skill 把确认的本地修复转成合规的 GitLab MR。执行时优先使用随附脚本，因为它会校验 commit/MR 格式、分支命名、持久 GitLab 认证、worktree/index 完整性、精确文件暂存、截图上传、`glab` 参数与最终远端 MR 内容。每个真实 MR 创建并验证后，继续执行下方的创建后六维审查循环；`--dry-run` 永远不跑该循环。

执行真实 commit、push 或 MR 创建前，先列出要提交的文件与生成的 commit 标题/正文。绝不使用 `git add .`。提交后不要登出 GitLab。

## 标题与 Commit 格式

每个 MR 标题使用按此顺序的空格分隔方括号字段：

```text
[动作] [芯片] [XTS] <说明>
```

动作从 `修改`、`优化`、`升级`、`新增`、`修复`、`同步`、`回退`、`重构`、`适配`、`迁移`、`移除` 中选一个。芯片字段可选，只能是 `[RK3568]` 或 `[A333/A537]`，两者不可同时出现。公共改动省略芯片字段。XTS/HATS/ACTS/DCTS 或 `test/xts` 改动加 `[XTS]`。不要使用旧式紧凑形式 `[修改][XTS]`。

示例：

```text
[修改] [RK3568] 修改 RK3568 EDP 屏幕系统调光失效问题
[修改] [A333/A537] 同步全志开源鸿蒙v0.9版本GPU库与HWC
[修改] [XTS] 修改 validator PlayerVideo、CapiDrag 测试通过状态异常问题
[优化] [RK3568] 优化 DNAKE 侧启动时序
```

跨双芯片的公共改动保持标题无芯片字段，需要时用重复的 `--label` 选项附加芯片标签。

使用这个普通修复正文格式：

```text
[修改] 修改 <问题对象> <问题现象>问题

具体:
    修改<失败原因或报错现象> 的问题
```

XTS/HATS/ACTS/DCTS/测试报告相关修复使用同样的标题语法：

```text
[修改] [XTS] 修改 <测试模块或用例名> <修改内容或失败现象>问题

具体:
    修改<测试失败原因、环境差异或报错现象> 的问题
```

规则：

- 使用上述动作/芯片/XTS 标题语法；随附脚本拒绝未知动作、重复芯片字段、紧凑方括号字段与缺失摘要。
- 路径、标题或上下文提到 XTS、HATS、ACTS、DCTS、测试报告分诊或 `test/xts` 下的测试模块时，一律加 `[XTS]`。
- 脚本从标题推导项目标签：芯片字段加对应芯片标签，`[XTS]` 加 `XTS`，无芯片标题加 `通用框架层修改`。附加标签（如跨芯片范围或项目特定标签）用重复的 `--label` 选项。
- 脚本在创建 MR 前对项目校验每个推导/显式标签。
- 第一行 commit 内容原样作为 MR 标题。
- commit 正文必须包含 `具体:`。`具体:` 后的文字要解释原因或观察到的失败，不能只说改了哪些文件。
- commit 正文原样作为 MR 描述。不要用 `glab mr create --fill`。
- 测试相关 MR 与任何使用 `--screenshot` 的 MR，使用 [references/mr-description-template.md](references/mr-description-template.md) 中要求的 Markdown 记录格式。每个必需小节都是以 `:` 结尾的二级标题。
- 测试命令放在围栏 `bash` 代码块中。命令或证据不要用四个空格缩进。
- 包含 `## 测试结果截图:` 小节，放一个或多个 Markdown 图片。优先 `--screenshot PATH`，脚本会上传本地证据并插入返回的 `/uploads/...` 链接。
- 把每个 HATS/ACTS/DCTS 可执行体或测试 MODULE 视为独立提交单元。其直接测试修正单独一个 commit 与 MR；不要把多个模块名、改动测试文件或它们的通过证据合并进一个 MR。
- 当一个不可避免的改动属于共享测试夹具而非单一 MODULE 时，单独提交一个以共享夹具为标题的 MR。不要与直接模块修复混在一起。在该 MR 中独立校验并记录每个受影响的 MODULE。

### 标签与里程碑

`create_glab_mr.py` 把解析后的标签传给 `glab mr create --label`。可选的 `--milestone` 接受生效中的里程碑标题、全局 ID 或 IID。未提供时，脚本先用生成的源分支、再用标题/正文，与生效中的里程碑版本（如 `V1.2.0`）匹配；`v1.2.x` 可能匹配唯一的 `V1.2.*` 里程碑。无匹配、有歧义或冲突时保持里程碑未设置，不阻塞提交。仅当项目明确要求缺失匹配即失败时，才设置 `MILESTONE_REQUIRED=true`。

## 分支与 MR 默认值

从 `assets/defaults.env` 加载默认值：

- Remote: `origin`
- Base 版本: `v6.1.0.31`
- 目标分支: `v6.1.0.31_release`
- 必需的 worktree 基准 ref: `origin/v6.1.0.31_release`
- 迭代版本: `auto`
- 迭代兜底: `v1.1.x`
- MR 指派人: `@cx`
- GitLab 主机: `192.168.11.238`
- GitLab API 协议: `http`
- Git 协议: `ssh`
- 里程碑匹配: `branch_then_message`，默认非阻塞

分支格式：

```text
<iteration-version>/v6.1.0.31_<problem-name>
```

迭代为 `auto` 时，从匹配 `v*/v6.1.0.31_*` 的远端分支推断最高语义版本并取下一个补丁版本，例如 `v1.1.3` -> `v1.1.4`。推断失败时使用 `defaults.env` 中的兜底值。

`<problem-name>` 由脚本从 commit 标题推导：移除所有结构化动作/芯片/XTS 字段、摘要中重复出现的开头动作词与结尾的 `问题`，再把剩余文本转成安全的分支后缀。

## 发布 Worktree 规则

从基于 `origin/v6.1.0.31_release` 的新建 worktree 提交。不要直接从长期存在的产品、modem、功能或脏集成分支提交。

修复在另一个 worktree 开发时用这个流程：

```bash
git diff -- path/one path/two > /tmp/fix.patch
git worktree add -b <temporary-release-branch> /home/cx/os/worktrees/<slug> origin/v6.1.0.31_release
cd /home/cx/os/worktrees/<slug>
git apply /tmp/fix.patch
```

辅助脚本在创建最终源分支、commit、push 或 MR 前，验证 `origin/v6.1.0.31_release` 是当前 `HEAD` 的祖先。校验失败时，新建发布 worktree 并只在那里应用选中的补丁。

对完整检出不现实的大仓库，只有初始化 index 与 skip-worktree 位后才允许 index-only worktree。必需序列：

```bash
git worktree add --no-checkout /home/cx/os/worktrees/<slug> origin/v6.1.0.31_release
git -C /home/cx/os/worktrees/<slug> read-tree HEAD
git -C /home/cx/os/worktrees/<slug> ls-files -z | git -C /home/cx/os/worktrees/<slug> update-index --skip-worktree -z --stdin
git -C /home/cx/os/worktrees/<slug> update-index --no-skip-worktree -- path/one path/two
git -C /home/cx/os/worktrees/<slug> checkout HEAD -- path/one path/two
```

运行提交脚本前，`git status --porcelain` 必须只包含选中的文件。脚本拒绝空 index、意外删除或 `--files` 之外的已暂存路径。

## 推荐执行流程

标题推导的标签是自动的。下面的 `--label` 用于附加标签（可选），`--milestone` 是显式覆盖（可选）；省略它则使用非阻塞的分支-再-消息匹配器。

1. 检查 `git status --short`，只识别属于该 MR 的文件。
2. 从 `origin/v6.1.0.31_release` 新建发布 worktree，只在那里应用选中的补丁。
3. 把 commit 消息写入临时文件，例如 `/tmp/mr_message.txt`。
4. 先 dry-run 脚本：

```bash
python3 {{SKILLS_DIR}}/glab-mr-submit/scripts/create_glab_mr.py \
  --repo /home/cx/os/worktrees/<slug> \
  --message-file /tmp/mr_message.txt \
  --files path/one path/two \
  --screenshot /tmp/test-result.png \
  --base-ref origin/v6.1.0.31_release \
  --target-branch v6.1.0.31_release \
  --assignee cx \
  --label 应用修复 \
  --milestone '开源鸿蒙V6.1 DNAKE V1.2.0 release版本' \
  --hostname 192.168.11.238 \
  --dry-run
```

5. 审查生成的分支、MR 标题、标准化 MR 描述、截图上传计划、指派人与精确暂存文件清单。Dry-run 绝不上传、提交、推送或创建 MR。
6. 只有用户要求提交 MR 后才真实执行：

```bash
python3 {{SKILLS_DIR}}/glab-mr-submit/scripts/create_glab_mr.py \
  --repo /home/cx/os/worktrees/<slug> \
  --message-file /tmp/mr_message.txt \
  --files path/one path/two \
  --screenshot /tmp/test-result.png \
  --base-ref origin/v6.1.0.31_release \
  --target-branch v6.1.0.31_release \
  --assignee cx \
  --label 应用修复 \
  --milestone '开源鸿蒙V6.1 DNAKE V1.2.0 release版本' \
  --hostname 192.168.11.238
```

脚本把每个截图上传到 `projects/<encoded-project-path>/uploads`，把返回的 Markdown 图片追加到截图小节，并用结果正文同时作为 commit 与 MR 的正文。创建后通过 GitLab API 验证标题语法、标签、里程碑、目标分支、描述、截图链接与精确变更路径。真实创建成功后，保留仓库路径与源分支，继续创建后审查循环。

## 创建后六维审查

开始该阶段前阅读 [references/mr-review.md](references/mr-review.md)。本 skill 创建的每个真实 MR 与显式请求审查的既有 MR 都会执行该阶段。dry-run 不执行，且它不发布评论也不修改 MR 描述。

### 解析审查上下文

1. 从已验证的脚本输出解析创建的 MR URL 与 IID。对既有 MR，使用提供的 URL 或 IID。
2. 通过 GitLab API 查询 MR，以其当前的 `diff_refs.base_sha`、`diff_refs.head_sha`/`sha`、`source_branch`、`target_branch` 为审查权威。不要假定当前检出或同名本地分支就是 MR 源。
3. 查询 `/changes` 捕获精确的原始变更路径。修复轮与复审轮开始时刷新 MR 元数据与变更路径。
4. 只审查 `base_sha...head_sha`，每个 Subagent prompt 都要包含仓库绝对路径、MR 身份、分支名与精确 diff 文件清单。保持所有 Subagent 只读：不编辑、不提交、不推送、不发 MR 评论、不改无关 worktree。

### 运行并聚合审查者

1. 并行生成六个独立的只读审查 Subagent，每个类别一个：安全、代码质量、缺陷、竞态、测试稳定性、可维护性。使用参考文档中的公共上下文与类别专属 prompt。
2. 等待六个终态结果全部返回。某 agent 报错、超时或触达模型容量时，用兜底模型重试该类别一次。重试仍失败则标记审查不完整，不要自动修复或声称审查干净。
3. 要求每个发现都包含类别、严重级、置信度、绝对文件路径、行号、触发条件、证据、影响与建议。干净的类别要求显式 `no findings` 结果。
4. 对同根因发现去重。保留最高合理严重级，保留跨类别标签，把推测性发现降级而非当作确认缺陷。
5. 报告顺序：先按严重级排列发现，然后干净类别、测试状态、不完整维度与残余风险。报告只留在对话中。

### 修复并更新既有 MR

1. 只自动修复已确认、可复现的 P0-P2 发现。不要自动实现 P3 发现或主观风格建议。
2. 主 Agent 在既有源码 worktree 与分支中做编辑；审查 Subagent 从不编辑。编辑前重新确认 worktree 干净，且每个拟改文件都在原始 MR 路径集内。
3. 修复需要新文件或原始 MR 之外的任何路径时，停下并报告范围扩大，而不是悄悄扩大 MR。
4. 修复后运行 `git diff --check` 与最窄的相关构建/测试命令。用 `git add -- <files>` 精确暂存路径，用结构化标题格式创建跟进 commit，并推送既有源分支。绝不创建第二个 MR 或使用 `git add .`。
5. 保留原始 MR 描述，不添加自动 MR 评论。跟进 commit 可以描述确认的审查发现与测试证据。

### 只复审一次

修复推送后，刷新 MR 元数据，对最新 MR diff 再跑全部六个审查者。这是一次完整复审，不是无界循环。复审不完整或仍报告确认的 P0-P2 发现时，在最新推送后保持 MR 不变，报告未解决项与需要的人工跟进。干净时，报告更新后的 MR SHA、修复 commit、测试与六个干净维度。

## 持久认证

在本机配置一次内部 GitLab 主机。优先系统 keyring；仅当 keyring 不可用时回退 glab 本地配置：

```bash
glab auth login \
  --hostname 192.168.11.238 \
  --api-host 192.168.11.238 \
  --api-protocol http \
  --git-protocol ssh \
  --use-keyring
```

token 必须通过 glab 的提示或标准输入输入，绝不写进本 skill、仓库、commit 消息、MR 描述或命令输出。脚本在提交前运行 `glab auth status --hostname 192.168.11.238`，认证缺失时以可操作的错误停止。它不会吊销或登出持久凭据。

## 标准记录

写测试相关消息或任何包含截图的 MR 前，阅读 [references/mr-description-template.md](references/mr-description-template.md)。标准顺序：

~~~markdown
## 具体:

## 问题分析:

## 修改内容:

## 测试环境:

## 测试命令:

```bash
command
```

## 测试结果:

## 测试结果截图:
~~~

无测试证据的普通改动仍需要结构化标题语法与 `具体:`，但不需要标准记录。一旦包含人工测试、命令或 `--screenshot`，就使用完整 Markdown 记录，让 GitLab 一致渲染每个小节。

## 安全检查

出现以下情况停下并询问用户：

- 本地或远端找不到目标分支。
- 当前 worktree 不是基于 `origin/v6.1.0.31_release`。
- `192.168.11.238` 的 GitLab 认证缺失或无效。
- Git index 为空、不完整或包含意外 worktree 改动。
- 任何请求的文件不存在或没有改动。
- 工作树包含无关改动而用户没有明确选择文件。
- commit 消息校验失败。
- 标题不是受支持的 `[动作] [芯片] [XTS] <说明>` 形式，或测试相关标题省略 `[XTS]`。
- 推导或显式标签在目标 GitLab 项目中缺失，或显式芯片/XTS 标签与标题字段冲突。
- 测试相关或含截图的消息缺少必需标题、有非围栏命令或没有截图链接/路径。
- 已暂存路径或创建后 MR 改动与请求文件不完全一致。
- 生成的远端分支已存在且无法生成唯一后缀。
- 任一类别重试一次后六维审查仍不完整。
- 自动修复会修改原始 MR 变更集之外的路径。
- 审查发现是推测性的、仅 P3 或无法从精确 MR diff 复现。
- 无法完成修复后测试或一次性复审；报告 MR 未解决而不是声称成功。

生成的本地或远端分支已存在时，脚本自动追加 `_2`、`_3` 等后缀。
