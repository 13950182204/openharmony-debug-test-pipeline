---
name: openharmony-ci-orchestrator
description: 为既有 MR 或分支触发并协调 OpenHarmony Jenkins 固件构建，包括 RK3568 与 A333 作业选择、Jenkins crumb 认证、持久完成状态、认证 webhook、定时 reconcile，以及受控的 phase-3 OTA/回归交接。当 OpenHarmony 修复需要 Jenkins 验证、且已批准的板级 profile 可在已登记硬件上执行 OTA 与回归时使用。
---

# OpenHarmony CI 编排

## 交付边界

Phase 1 校验目标 Jenkins 作业并入队一次构建。Phase 2 记录真实触发结果，接收或 reconcile 完成状态，核对原始 Jenkins 参数，并在匹配的构建成功后启动一个新的 dsh headless 交接会话。Phase 3 为可选：交接只调用受信任的板级 profile，该 profile 在 OTA 前校验归档包与目标身份。

Phase 1 使用 [scripts/trigger_jenkins_build.py](scripts/trigger_jenkins_build.py)，持久状态与交接使用 [scripts/ci_orchestrator.py](scripts/ci_orchestrator.py)，[scripts/phase3_runner.py](scripts/phase3_runner.py) 只能通过已注册 profile 调用。部署前阅读 [references/phase2-operations.md](references/phase2-operations.md)，已安装的 A333 流程见 [references/phase3-a333.md](references/phase3-a333.md)。

## 工作流

1. 确认 MR 源分支，以及可用时的当前源 SHA。不要为未提交或含糊的分支触发构建。
2. 用 `--dry-run --verify-job` 运行辅助脚本。这执行只读的 Jenkins 作业检查，并验证必需参数存在。
3. 在用户明确授权启动 Jenkins 后，在环境中设置 `JENKINS_USER` 与 `JENKINS_API_TOKEN`，不带 `--dry-run` 运行辅助脚本。
4. 把真实触发 JSON 管道给 `ci_orchestrator.py register`，并传入仓库绝对路径。它写入一个锁定的状态文件，包含 queue/build 身份与原始参数集。
5. 运行 [references/phase2-operations.md](references/phase2-operations.md) 中描述的认证 webhook 接收器与每小时 systemd 定时器。
6. 接收器与定时器都调用 `reconcile`；它信任 Jenkins API 数据而非回调体，核对所有跟踪参数，然后在 `SUCCESS` 后启动恰好一个 `dsh --profile headless` 会话。
7. 对已批准的 phase-3 profile，用 `--phase3-profile` 与 `--agent-sandbox danger-full-access` 注册。交接运行固定的 runner，而不是 Jenkins 或 MR 文本里的命令。

## RK3568 调用

显式使用已验证的 RK3568 产品参数：

```bash
export JENKINS_BASE_URL="${JENKINS_BASE_URL:-http://192.168.13.121:8080}"
export JENKINS_JOB="${JENKINS_JOB:-OpenHarmony-V6.1-RockChip}"

python3 "{{SKILLS_DIR}}/openharmony-ci-orchestrator/scripts/trigger_jenkins_build.py" \
  --branch <mr-source-branch> \
  --source-sha <mr-source-sha> \
  --mr-iid <mr-iid> \
  --build-mode INCREMENTAL \
  --firmware-type XTS \
  --parameter Openharmony_Devices=rk3568_dsi_800x1280 \
  --verify-job \
  --dry-run
```

只有审查 JSON 并获得入队授权后，才移除 `--dry-run`。仅当调用方明确需要 Jenkins 在返回前分配构建号时，才使用 `--wait-seconds`。

## 认证与安全

- Jenkins API token 不要写进文件、提示、日志、JSON 输出或 MR 文本。辅助脚本只从 `JENKINS_API_TOKEN` 读取。
- 真实 POST 必须同时具备 `JENKINS_USER` 与 `JENKINS_API_TOKEN`。允许匿名的只读作业验证。
- 仅当 Jenkins 端点有意配置为允许匿名构建请求且其身份已被核查时，才使用 `--allow-anonymous`。这是显式例外，绝不是默认。
- 可用时使用 Jenkins crumb issuer；不要绕过 CSRF 防护，也不要用 Script Console 做常规构建触发。
- 作业路径与所有参数值都通过辅助脚本编码。不要用分支或参数输入拼 shell 命令。
- 把返回的 queue URL 视为不透明标识。Phase 2 在 phase 3 消费产物前核对分配的构建参数。
- POST 成功只意味着「已入队」，不意味着「已构建」「已打包」「OTA 就绪」或「设备已更新」。

## 失败处理

作业缺失、被禁用、必需参数缺失、凭据不完整、Jenkins 拒绝请求或参数畸形时，以可操作的错误停止。不要自动重试真实 POST，因为超时可能发生在 Jenkins 已经入队构建之后。

## Phase 2 安全

- 只注册 phase 1 报告 `action: trigger` 的 JSON；`action: dry-run` 会被拒绝。
- 在认定结果前，把分配的构建提交参数与已注册参数核对。忽略其他作业/构建的 webhook。
- Jenkins 失败、取消、参数不匹配或 agent 启动失败，都不会启动 OTA 或重试设备动作。
- 定时器只是兜底。它不入队 Jenkins 构建，也不会在首个 agent 已记录为启动后再次启动 agent。
- 回调 HMAC 材料与 Jenkins 凭据放在 `0600` 的 systemd 环境文件中，绝不放进运行状态 JSON 或 MR 文本。

## Phase 3 安全

- profile 是 `profiles/` 下的受信任 JSON 文件；分支名、Jenkins 参数与 MR 文本永远不会提供可执行的设备命令。
- 已安装的 `a333-2g-primary-standby` profile 只接受 `OpenHarmony-V6.1-AllWinner`、`a333_medical_dsi_800x1280` 及预期的 DHong/76A/DNAKE 产品参数。它只下载归档的 `openharmony_V6.1/out/ota/update.zip`。
- 其主设备 serial 是 `ea010e325333324247102b4ed1988ce7`；备机 serial 是 `ea010e325333324247102b4ed1a48c99`。仅当主设备升级失败或未能恢复正常 HDC 启动后，才动备机。
- 每次 OTA 都需要包 ZIP/版本预检、源版本兼容、`/data` 容量、重连后的设备身份、`updater_result=pass` 与 `bootevent.boot.completed=true`。
- Phase 3 要求已注册的源 SHA，并在下载产物前扫描 Jenkins `consoleText` 中实际的 `HEAD is now at` 检出记录。仅分支的构建证据不足，会被拦截。
- 内置 OTA 冒烟检查始终运行。没有显式功能回归 profile 的运行报告 `INCONCLUSIVE`，绝不报告功能回归通过。
