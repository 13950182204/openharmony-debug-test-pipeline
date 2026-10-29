---
name: openharmony-demo-loop
description: 演示模式（demo mode）：拿一份真实失败的 XTS 报告与一个用例文件，10 分钟内从 report 跑到 regression——报告/分诊/设备部署/包预检/单模块回归现场真跑，门禁与 OTA 用可辨认的替身并逐段明说。当用户说「演示模式 / demo / 彩排一遍闭环 / 拿 MR260 讲一遍全流程」时使用。
metadata:
  short-description: 10 分钟跑通 OpenHarmony 调测闭环的演示模式
---

# 演示模式（demo loop）

把「失败报告 → 分诊 → 修复 → MR → 门禁 → OTA → 回归 → 收尾」压进 10 分钟。
**演示模式的产出不是幻灯片，是一条真跑记录**：8 个阶段各留一份证据，来源标注可核对。

## 硬边界（先读，违反即演示失信）

1. **每个阶段必须自带来源**：`live`（现场真跑）／`local-staged`（本地准备、替代平台）／
   `recorded`（历史回放）。开讲前一句话说明，不许含糊。
2. **门禁是替身**：`gate_stage.py` 产出 `mode=demo-local` 的产物契约，**不是** Jenkins 构建。
   不伪造构建号、不访问 Jenkins API；真实构建页只作对照链接。
3. **OTA 三证据是回放**（lane a）：升级器结果与启动标志来自历史真实 run；
   包预检是现场真跑。lane b 才真刷。
4. **测试侧问题不得改用例**：分诊结论是测试/环境侧就走豁免，这条在演示里也要说出口。
5. **不碰真实状态**：演示状态只在 `~/.dsh/demo/`；`~/.dsh/pipeline-state.json`
   与 ci-orchestrator 状态目录零写入。
6. 演示结束跑 `reset --yes`，并报告残留（设备是否仍是修复版、包是否被移动）。

## 骨架（lane a，约 9:30）

| 时间 | 阶段 | 现场动作 |
| --- | --- | --- |
| 0:00 | 开场 | 一句话定位 + `democtl start` |
| 0:30 | report | `summarize_report.py <报告>`（0.3 秒出表） |
| 1:50 | triage | 两簇证据 → 当场说分流（修 / 豁免） |
| 3:10 | fix | `deploy_fix.py`（推送 + 重启 + sha256） |
| 5:10 | （后台） | **回归此时启动**，边跑边讲 |
| 5:10 | mr | MR 页面 / `glab api` + 六维审查结论 |
| 5:50 | ci | `gate_stage.py`（≈19 秒） |
| 6:30 | ota | `ota_preflight.sh` 真跑 + 三证据 |
| 7:30 | regression | 收后台结果 |
| 9:00 | done | 8/8 + 价值 + 后续 |

**关键技巧**：把最慢的回归藏在 MR/门禁/OTA 讲解后面**并行跑**（单模块实测 ≈60 秒，16 例；2026-10-29 彩排全程 1:32，fix 为 dry-run）。

## 命令

```bash
REPO=/home/cx/os/openharmony-debug-test-pipeline
DEMO="{{DEMO_SCRIPT_DIR}}"

python3 $DEMO/democtl.py preflight --case mr260        # 演出前（≈2s）
python3 $DEMO/democtl.py start --case mr260 --lane a
python3 $DEMO/democtl.py status                        # 投屏状态卡
python3 $DEMO/democtl.py stage report --note "648/644/4"
python3 $DEMO/democtl.py stage triage --provenance live --evidence <file>
```

真跑三件套（`RUN=~/.dsh/demo/runs/<时间戳>`）：

```bash
# fix（≈2 分钟，设备会重启）
python3 $DEMO/deploy_fix.py --build <patched.z.so> --device <SN> \
  --json-out $RUN/evidence/fix.json

# ci（≈19 秒）
python3 $DEMO/gate_stage.py --package <update.zip> --mr-iid 260 \
  --source-sha <head-sha> --out $RUN/gate

# ota 预检（≈19 秒）
bash $REPO/skills/openharmony-ota-upgrade/scripts/ota_preflight.sh <update.zip>

# regression（后台起，实测 ≈60 秒；session_root 落在 WSL 私有路径时会自动改到运行器目录）
python3 $DEMO/regression_fast.py --module <Module> --serial <SN> \
  --session-root $RUN/evidence/regression --json-out $RUN/evidence/regression.json
```

报告摘要与 hilog 取证用现成 skill 的脚本：
`{{SKILLS_DIR}}/openharmony-test-report-triage/scripts/summarize_report.py`、
`extract_case_hilog.py`。

用例数据在 `{{DEMO_SCRIPT_DIR}}/cases/<case>.json`：报告路径与期望计数、两簇根因、
修复件、MR、包、回归模块、每阶段口播（`cues`）。**换用例只改这个文件**。
完整 runbook（含彩排清单与裁员线）见 `{{DOCS_DIR}}/演示模式.md`。

## 投屏 UI 契约

每个阶段结束追加一个 `dsh-ui` 围栏，让屏幕上始终有"进度 + 证据"：

- 顶部 `steps`：8 阶段，当前阶段 `status:"running"`；
- 中间按阶段选一个：`stat`（数字，如 648/644/4）、`table`（失败用例与根因）、
  `keyvalue`（包版本/sha256）、`progress`（回归 passed/total）；
- 底部 `callout`：本段来源（`live` / `local-staged` / `recorded`）+ 一句话结论。

≥3 节点或含 `table` 时先 `validate_dsh_ui`。正文不复述围栏里的数字。

## 降级矩阵

| 现场故障 | 处理 | 来源改标 |
| --- | --- | --- |
| GitLab 令牌失效（`401 revoked`） | MR 段用 `democtl snapshot --mr` 的快照 | `recorded` |
| Jenkins 不可达 | 门禁本就本地（只展示对照链接） | `local-staged` |
| 设备离线 / 重启不回连 | 跳过 fix 与 regression，`stage ... --degraded` | `recorded` |
| 回归超时（>2 分钟） | 展示历史结果（8941 修复版 8/8×2、全模块 929/929） | `recorded` |
| 脚本报错 | 不现场 debug：念 runbook 的 canned 输出继续 | `recorded` |

## 常见故障

- **回归 `INCONCLUSIVE` / `0 devices were found`**：共享 `user_config.xml` 的 `<sn>` 是别的板子，
  `regression_fast.py` 会自动复制配置并覆盖 `<sn>`；若仍失败，检查 Windows 侧 `hdc list targets`。
- **模块名不存在**：模块名取运行器 `testcases/<Module>.json`，不是报告里的类名
  （`ActsAACommandPrintSyncTest` 是模块名，`DrawChildrenTest` 不是）。
- **`阶段越序`**：说明上一段没记；先补记，确实要跳用 `--force`（状态卡会标红）。
