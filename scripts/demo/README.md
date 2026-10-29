# 演示模式脚本（demo mode）

给一份**真实报错的报告**、一个用例（`cases/mr260.json`），10 分钟内从 `report` 走到
`regression`。真跑的地方真跑（报告解析、设备部署、包预检、单模块回归），
预置的地方**必须明说**（门禁产物契约、OTA 三证据回放）。

> 这些脚本**不需要重装插件**，在仓库里直接可用。
> 插件命令 `/demo` 与 skill `openharmony-demo-loop` 是同一套脚本的薄封装（需重装）。

## 现状能力对照

| 阶段 | 谁在执行 | 来源标记 |
| --- | --- | --- |
| report / triage | `skills/openharmony-test-report-triage/scripts/*`（真解析） | `live` |
| fix | `deploy_fix.py`（真推送 + 重启 + sha256 校验） | `live` |
| mr | `glab api`（令牌有效时）／快照 | `live` / `recorded` |
| ci | `gate_stage.py`（本地包 → 门禁产物契约） | `local-staged` |
| ota | `ota_preflight.sh`（真跑）+ 三证据回放 | `live` / `recorded` |
| regression | `regression_fast.py`（复用 phase3 受信任实现，真跑） | `live` |

## 一分钟上手

```bash
cd /home/cx/os/openharmony-debug-test-pipeline

# 1. 演出前预检（≈2 秒，含设备/GitLab/磁盘）
python3 scripts/demo/democtl.py preflight --case mr260

# 2. 开场
python3 scripts/demo/democtl.py start --case mr260 --lane a

# 3. 每完成一段就记一笔（越序会被拒绝）
python3 scripts/demo/democtl.py stage report --note "648/644/4"
python3 scripts/demo/democtl.py stage triage --provenance live --evidence /path/to/triage.json

# 4. 随时看状态卡（可投屏）
python3 scripts/demo/democtl.py status
```

演示状态与产物全部落在 `~/.dsh/demo/`（`runs/<时间戳>/`），**绝不读写**
`~/.dsh/pipeline-state.json` 与 ci-orchestrator 的状态目录。

## 三个阶段的真跑命令

```bash
RUN=~/.dsh/demo/runs/<时间戳>

# fix：推送修复库 + 重启 + 校验（约 2 分钟，设备会重启）
python3 scripts/demo/deploy_fix.py \
  --build /home/cx/.dsh/pipeline-runs/2026-10-21-acts-LTS-f/patches/libace_compatible.z.so.patched \
  --device 150100424a5444345206517525298929 \
  --json-out $RUN/evidence/fix.json

# ci：门禁产物契约（约 19 秒）
python3 scripts/demo/gate_stage.py \
  --package /home/cx/os/v6.1/out/ota_final/update.zip \
  --mr-iid 260 --source-sha ddde621d74487a7828883270da8f8096773a7909 \
  --out $RUN/gate

# ota：预检真跑（约 19 秒）
bash skills/openharmony-ota-upgrade/scripts/ota_preflight.sh /home/cx/os/v6.1/out/ota_final/update.zip

# regression：单模块真跑（实测 PASS 16/16 · 60s；建议后台跑、边讲边等）
# session_root 落在 ~/.dsh/demo/... 时会自动改到 <runner_dir>/reports/demo
# （Windows 侧 xdevice 读不了 UNC 路径，会枚举到 0 台设备），证据 JSON 仍写到 $RUN。
python3 scripts/demo/regression_fast.py \
  --module ActsAACommandPrintSyncTest \
  --serial 150100424a5444345206517525298929 \
  --json-out $RUN/evidence/regression.json
```

`regression_fast.py --dry-command` 只打印将要执行的 xdevice 命令行，不碰设备。

## 为什么有两条"非真跑"的路

1. **门禁**：真实链路是 GitLab 流水线 → Jenkins `MR-Gate` → 芯片作业，数十分钟。
   演示用 `gate_stage.py` 产出**同形状**的 `build.json` + `mr-metrics.tsv`，
   `mode` 固定为 `demo-local`，不伪造构建号、不访问 Jenkins。
2. **OTA 三证据**：全量包 1 GiB，真刷约 3–4 分钟；而且预置包（10-19）早于
   本次修复（10-22），刷完反而会丢修复。所以 lane a 只做"预检真跑 + 三证据回放"，
   真刷放 lane b，且刷完必须重新部署修复件。

## lane 选择

- `--lane a`（默认）：10 分钟整，回归后台并行；
- `--lane b`：a 之后加"真刷预置包 → 重新部署修复件 → 回归"，约 +4 分钟；
- `--lane c`：无设备/无网络时全回放，约 6 分钟。

## 排障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `阶段越序` | 想跳过未完成阶段 | 先补前一段，或 `--force`（会在状态卡标红） |
| 预检 GitLab `WARN 令牌失效` | 令牌被 revoke | 设置页「GitLab 凭据」重存，或 MR 段用快照 |
| `runner_dir 不存在` | XTS 目录改名 | 用 `--runner-dir` 显式指定（现为 `office/acts`） |
| 回归 `INCONCLUSIVE` | 模块名不在运行器 | 模块名来自 `testcases/<Module>.json`，不是报告里的类名 |
| 部署后设备不回连 | 重启异常 | 脚本超时退出；演示层记录 `degraded` 并切 lane c |
