# Phase 2 运维

## 状态流（平台 CI 版）

1. MR 提交/更新 → **平台自动**创建 GitLab 流水线 `openharmony-mr-gate` → 触发并等待
   Jenkins 门禁 `OpenHarmony-V6.1-MR-Gate` → 顺序构建 `OpenHarmony-V6.1-MR-<chip>`。
   本插件在这一步**什么都不触发**。
2. 用 `ci_orchestrator.py observe` 登记 run（MR iid / 精确 head SHA / 目标分支 / profile），
   同时只读读一次平台流水线状态。
3. `reconcile`（手动或 systemd 定时器）只读轮询 GitLab：
   `gate_pending` → （流水线结束）→ `gate_succeeded` / `gate_failed` / `gate_aborted` /
   `gate_not_in_scope`。
4. 平台流水线成功 → 认领芯片构建：按 `MR_IID/SOURCE_SHA/TARGET_BRANCH` 在
   `OpenHarmony-V6.1-MR-<chip>` 最近构建里找到 `result=SUCCESS` 的那一个，
   并读门禁 `gate-plan.json`/console 确认本次确实构建了该芯片。
5. 认领成功 → 启动恰好一个受信任 phase-3 systemd 单元（`openharmony-ci-phase3@<run-id>.service`），
   runner 负责包下载、预检、OTA 与回归。

认领时以**芯片构建**为硬门禁；汇总门禁构建若已被保留策略清理（或本机无该作业读权限），
会降级为"仅按芯片构建身份 + console 检出证据认领"，并把原因写进
`trigger.platform.gate_build_lookup_error` 与 `gate_build_unavailable` 事件（不静默）。

状态取值：`gate_pending`、`gate_succeeded`、`gate_failed`、`gate_aborted`、
`gate_not_in_scope`、`build_succeeded`、`blocked`。phase-3 结果独立存于 `phase3.status`
（`not_started`/`running`/`passed`/`inconclusive`/`failed`/`dry_run`），
失败的 OTA 永远不会与门禁失败混淆。

## 观察与手工推进

```bash
# 注册（只读观察；--phase3-profile 决定认领哪个芯片）
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" observe \
  --mr-iid <iid> --source-sha <head-sha> \
  --source-branch <source-branch> --target-branch <target-branch> \
  --repo-dir <absolute-repository-path> \
  --phase3-profile a333-2g-primary-standby --agent-sandbox danger-full-access

# 推进（默认跳过已终结的 run；-prune 归档旧 run）
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" reconcile --prune-days 14
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" reconcile --run-id <run-id>

# 审计事件（例如记录最终选中的 DUT 与理由）
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" note \
  --run-id <run-id> "selected serial <serial> because ..."

# 恢复：平台构建早已完成时一步登记并认领
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" adopt \
  --mr-iid <iid> --source-sha <head-sha> --target-branch <target-branch> \
  --repo-dir <absolute-repository-path> \
  --phase3-profile a333-2g-primary-standby --agent-sandbox danger-full-access
```

`observe`/`adopt`/`reconcile` 都不产生构建。缺 Jenkins 只读凭据时，认领会以
`blocked` + 明确的中文提示结束（指出要改 `jenkins.env` 里的哪几项），不会静默降级。

## 端到端包演练（不写设备）

换新主机、换 profile 或第一次接入某个芯片时，先跑一次演练：认领平台构建并下载校验真包，
但绝不访问 HDC / 写设备。

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" observe \
  --mr-iid <iid> --source-sha <head-sha> --target-branch <target-branch> \
  --repo-dir <absolute-repository-path> \
  --phase3-profile a333-2g-primary-standby --agent-sandbox danger-full-access \
  --dry-run
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" reconcile
```

`--dry-run` 落进状态的 `phase3.dry_run`，由 `phase3_unit_entry.py` 转发给 runner；结束后
run 的 `phase3.status=dry_run`，`phase3.package` 里有解析到的产物名、字节数、SHA-256、
包内目标版本与源版本白名单。手工跑单元入口时记得给它 `CI_STATE_DIR`（默认指向生产状态目录）：

```bash
CI_STATE_DIR=<state-dir> python3 ".../scripts/phase3_unit_entry.py" <run-id>
```

## 配置

`~/.config/openharmony-ci-orchestrator/jenkins.env`（0600）：

```
JENKINS_BASE_URL=http://192.168.97.54:8080     # 平台门禁主机
JENKINS_USER=<门禁主机上的只读账号>
JENKINS_API_TOKEN=<该账号的 API token>
GITLAB_HOST=192.168.11.238
GITLAB_PROJECT=harmony/system/v9611/openharmony_V6.1
# GLAB_BIN=/home/cx/bin/glab                    # systemd 最小 PATH 之外的兜底
```

`JENKINS_TRUSTED_ORIGINS` 只在 Jenkins 返回与访问地址不同的自引用 origin 时才需要设置；
新门禁主机返回自身 origin，通常留空。

## 安装兜底定时器

在具备 GitLab 与门禁 Jenkins 网络访问、且会控制目标设备的主机上运行一次：

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/install_systemd_units.py" \
  --reconcile-timer '*:0/5' --prune-days 14 --enable
```

该命令写入 `openharmony-ci-reconcile.{service,timer}`（含显式 `PATH`，保证 systemd 能找到
`glab`），并**禁用** legacy 的 `openharmony-ci-webhook.service`（平台信号来自 GitLab；
单元文件保留便于回滚，需要时用 `--with-webhook` 启用）。用户服务要求机器保持开机；
`loginctl enable-linger <user>` 让它在登出后存活，用 `loginctl show-user <user> -p Linger` 验证。

## 检查与恢复

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" reconcile
systemctl --user status openharmony-ci-reconcile.timer
ls -1 ~/.local/state/openharmony-ci-orchestrator/runs/
systemctl --user status 'openharmony-ci-phase3@<run-id>.service'
```

不要为重试删除生效中的状态文件；先看它的 `events`、`trigger.platform.pipeline_*`、
`trigger.platform.gate_build_url` 与 `build.adopt_error`。`blocked`/`gate_failed` 的 run
绝不会启动 phase-3 单元。终结 run 可用 `--prune-days` 归档到 `runs/archive/`（审计保留）。

## legacy webhook（可选）

`serve` 子命令仍保留（HMAC 校验、校验后拉取 Jenkins 构建 API），但**不再是主路径**：
平台流水线本身就是要等的结果，`reconcile` 每 5 分钟检查一次即可。若启用它，只在
systemd 环境文件里设置 `CI_WEBHOOK_SECRET`，并把监听地址配成 Jenkins 可达的地址。

## Phase 3 登记

只对已审查、且其精确设备池在本机可用的 profile 使用。runner 拒绝所有未指定的包路径与 HDC serial；
芯片由 `--phase3-profile` 决定，认领时会与门禁 `gate-plan.json` 的 `chips` 交叉核对：

```bash
... ci_orchestrator.py observe --mr-iid <iid> --source-sha <sha> \
  --target-branch v6.1.0.35_lts --repo-dir <absolute-repository-path> \
  --phase3-profile a333-2g-primary-standby \
  --regression-profile <trusted-feature-profile> \
  --agent-sandbox danger-full-access
```

启用该路径前阅读 [phase3-a333.md](phase3-a333.md)。在同一 systemd 环境文件中设置
`GITLAB_HOST` 与 `GITLAB_PROJECT`，以接收幂等的 MR 证据评论。
