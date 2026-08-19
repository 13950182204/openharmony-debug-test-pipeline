# Phase 2 运维

## 状态流

1. 用 `trigger_jenkins_build.py` 触发 Jenkins 并保存其 JSON 输出。
2. 用 `ci_orchestrator.py register` 登记该真实触发结果。
3. 构建结束后 Jenkins 带 HMAC 签名调用 `POST /jenkins`。
4. webhook 接收器拉取 Jenkins 构建 API，在记录成功或失败前核对所有原始提交的参数。
5. 匹配的构建成功后启动恰好一个新的 `dsh --profile headless` 会话。Jenkins 无法触达 webhook 接收器时，每小时定时器重复同一 reconcile 路径。

没有 phase-3 profile 时，agent 交接保持只读。显式命名受信任 phase-3 profile 并使用 `danger-full-access` sandbox 的登记，会在验证构建成功后调用固定的 phase-3 runner。运行状态使用 `queued`、`building`、`build_succeeded`、`build_failed`、`blocked`；phase-3 结果独立存于 `phase3.status`，失败的 OTA 永远不会与 Jenkins 失败混淆。

## 登记运行

用管道保证只有真实、成功的 phase-1 触发才能创建状态文件：

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/trigger_jenkins_build.py" \
  --branch <source-branch> --source-sha <source-sha> --mr-iid <iid> \
  --parameter Openharmony_Devices=rk3568_dsi_800x1280 \
  --wait-seconds 30 \
| python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" \
  register --repo-dir <absolute-repository-path>
```

真实触发必须有 `JENKINS_USER` 与 `JENKINS_API_TOKEN`。登记命令拒绝 phase-1 dry-run 结果。

## Webhook 契约

只在 systemd 环境文件中设置 `CI_WEBHOOK_SECRET`。Jenkins 必须发送以下任一形式的 JSON 体：

```json
{"job_name":"OpenHarmony-V6.1-RockChip","build_number":46}
```

```json
{"job":"OpenHarmony-V6.1-RockChip","build":{"number":46}}
```

添加 `X-CI-Signature: sha256=<hex-hmac-sha256-of-raw-body>`。接收器绝不信任 webhook 声称的结果。它读取 Jenkins 构建 API，在变更状态前校验跟踪的分支与参数。

默认绑定 `127.0.0.1`。要接收远端 Jenkins 回调，绑定明确可达的地址或在前面放反向代理。两种情况下都保持 HMAC 校验开启。

## 安装兜底定时器

在具备 Jenkins 网络访问、且之后会控制目标 RK3568 的主机上运行一次：

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/install_systemd_units.py"
```

在 `~/.config/openharmony-ci-orchestrator/jenkins.env` 中填入随机的 `CI_WEBHOOK_SECRET`，需要时填 Jenkins 凭据。然后启用服务与每小时定时器：

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/install_systemd_units.py" \
  --listen-host <reachable-host-or-ip> --enable
```

用户服务要求机器保持开机。`loginctl enable-linger <user>` 让它在登出后存活；用 `loginctl show-user <user> -p Linger` 验证。

## 检查与恢复

```bash
python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" reconcile
systemctl --user status openharmony-ci-webhook.service openharmony-ci-reconcile.timer
ls -1 ~/.local/state/openharmony-ci-orchestrator/runs/
```

不要为重试删除生效中的状态文件。先检查它的 `events` 与 Jenkins 构建 URL。失败或阻塞的构建绝不启动跟进 agent。

状态文件使用原子替换与进程锁。重复 webhook、定时器 tick 或另一个排队运行的 webhook 都无法启动第二个 phase-3 agent。

## Phase 3 登记

只对已审查、且其精确设备池在本机可用的 profile 使用。runner 拒绝所有未指定的包路径与 HDC serial：

```bash
... trigger_jenkins_build.py <verified A333 arguments> \
| python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/ci_orchestrator.py" \
    register --repo-dir <absolute-repository-path> \
    --phase3-profile a333-2g-primary-standby \
    --regression-profile <trusted-feature-profile> \
    --agent-sandbox danger-full-access
```

启用该路径前阅读 [phase3-a333.md](phase3-a333.md)。在同一 systemd 环境文件中设置 `GITLAB_HOST` 与 `GITLAB_PROJECT`，以接收幂等的 MR 证据评论。
