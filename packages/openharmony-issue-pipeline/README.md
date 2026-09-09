# openharmony-issue-pipeline

父插件 [`openharmony-debug-test-pipeline`](../../README.md)(github.com/13950182204/openharmony-debug-test-pipeline)的
**子插件**:GitLab Issue 驱动看板流水线。定期从 GitLab 项目
`harmony/system/v9611/openharmony_V6.1` 抓取 Assignee=cx 的待解决 issue,按标签映射
工作区/分支、按 Due date 分级模型档位(含图片强制视觉模型),按父插件八阶段闭环处理
(第一阶段改为分析 issue Description),每阶段同步 DSH 任务看板(`@linxin666/dsh-client-ui-task-board`)
卡片状态与 issue 评论。

## 组成

| 部件 | 说明 |
|---|---|
| `skills/openharmony-issue-pipeline/SKILL.md` | 编排 skill(sweeper 会话与 issue 会话加载) |
| `scripts/issue_pipeline.py` | CLI:init-config / sync / run / rerun / move / stage / close / status / bootstrap |
| `src/` | 插件注册(host-only,无 client 半身):注册上述 skill 与 `/issue status` 命令 |

- 本插件**不 import 父插件代码**:运行期通过 `skill` 工具按名加载父插件六个模块 skill;
  `{{PARENT_PLUGIN_DIR}}` 占位符替换为父插件根目录(默认 packages 上一级,
  可用插件 config `parentPluginDir` 覆盖),供引用 `scripts/pipeline_state.py` 等脚本。
- 编译产物 `lib/` 无任何 `@deepseek-ai` 运行时导入,零外部运行时依赖。

## 依赖与安装

- 依赖父插件已安装(web profile bundles 需同时含 `openharmony-debug-test-pipeline`)。
- 构建(复用父仓库工具链):`npm run build`(在父仓库根,或本包内 `pnpm build`)。
- 安装:`dsh plugin --profile web add link:$(pwd)/packages/openharmony-issue-pipeline`
  后重启 `dsh web`(等价手工步骤:profile package.json 加
  `"openharmony-issue-pipeline": "link:<父仓库>/packages/openharmony-issue-pipeline"`
  并把包名追加进 `dsh.profile.bundles`,再在 profile node_modules 建同名 symlink)。

## 一次性初始化

```bash
python3 packages/openharmony-issue-pipeline/scripts/issue_pipeline.py init-config
python3 packages/openharmony-issue-pipeline/scripts/issue_pipeline.py bootstrap --dry-run
python3 packages/openharmony-issue-pipeline/scripts/issue_pipeline.py bootstrap
```

`bootstrap` 在任务看板创建「GitLab 问题流水线轮询」卡片并启用 Host cron(默认 `*/30 * * * *`)。
前置条件:映射表用到的所有工作区目录已登记进 DSH 工作区列表(尤其
`/home/cx/os/6.1_rk3568`);GitLab 令牌已在 设置 → GitLab 凭据 保存。

## 配置

`~/.dsh/issue-pipeline/config.json`(`init-config` 生成默认值),关键项:

- `gitlab`:baseUrl / project / assignee(默认 cx);
- `board`:baseUrl / sweepCron(默认 `*/30 * * * *`) / sweepTaskTitle;
- `workspaces` + `branchDefaults`:工作区 key → 目录、默认分支;
- `labelWorkspace` / `labelBranch` / `chipPriority`:标签 → 工作区 key、分支、芯片优先级;
- `modelTiers`:档位表(maxDays 升序,None 兜底);档位通过**子代理模型**实现(主会话模型由宿主固定,预设级模型被宿主拒绝,见 SKILL.md);
- `vision`:图片规则(hasImages → 读图子代理强制 `deepseek-v4-flash-vision-exp`);
- `autoRun`(true)/ `commentPerStage` / `closeOnDone`(false,回归通过后人工确认关闭)/
  `requireApproval`(false,无人值守;true 则真实动作前停下等待);
- `autoRetry`:失败/阻塞任务自动重试(6 小时 / 3 次)。

## 使用

- 轮询:看板 sweeper 任务自动执行 `sync`(建卡 + run/rerun)。
- 手工:`python3 issue_pipeline.py sync --dry-run` 预览;`stage <iid> <stage> <摘要>`
  由 issue 会话每阶段调用;`close <iid> --confirm` 人工确认后关闭;
  `/issue status`(会话内)输出看板↔issue 对照。
- 阶段映射:`progress`(进行中+评论)、`done`(已完成)、`failed`(已失败)、
  `blocked`(待办,等待重试)。

## 安全

- GitLab 令牌只从 `~/.dsh/gitlab-credentials.json`(0600)读取,绝不打印/落日志;
- 任务看板 API 仅本机 loopback + Origin 同源标记,403 明确报错;
- 所有写操作命令支持 `--dry-run`;设备操作遵循父插件 `hdc list targets` 唯一设备纪律。
