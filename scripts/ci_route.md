# OpenHarmony CI 零-token 监控与触发路线（固化记录）

更新日期：2026-08-26。适用仓库：`openharmony-debug-test-pipeline` + 本机（WSL Ubuntu 22.04，systemd PID1）部署的 Jenkins 监控。

## 1. 零-token 状态监控架构（现状，已验证）

LLM（agent）**不参与监控轮询**。监控 tick 全部由宿主/系统进程承担：

```
Jenkins (192.168.13.121:8080)
   │  buildWithParameters (phase1 触发, trigger_jenkins_build.py)
   ▼
openHarmony-ci-orchestrator 状态: ~/.local/state/openharmony-ci-orchestrator/runs/<run_id>.json
   ▲                                          │
   │  reconcile 轮询(只读 Jenkins API, 纯 python, 零 token)   │ 构建 SUCCESS 跃迁
   │                                          ▼
systemd: openharmony-ci-reconcile.timer   ──→  ci_orchestrator.py reconcile
   OnCalendar=*:0/5 （每 5 分钟，Persistent，RandomizedDelaySec=1m）
   EnvironmentFile=~/.config/openharmony-ci-orchestrator/jenkins.env (0600)
   │
   └── 可选增强：openharmony-ci-webhook.service（HMAC 回调接收器，当前 inactive，
       因监听 127.0.0.1 且 Jenkins 侧未配置回调；启用需 Jenkins 管理员配置
       POST /jenkins + X-CI-Signature，密钥 CI_WEBHOOK_SECRET 已在 jenkins.env）
```

token 消耗点唯一：reconcile 确认构建 `SUCCESS` 后启动 **恰好一个** `dsh --profile headless` 会话（守护进程内的固定 phase3/profile runner），一次回合完成后续（OTA 或回测）。

已部署单元：
- `~/.config/systemd/user/openharmony-ci-reconcile.timer`（*:0/5）
- `~/.config/systemd/user/openharmony-ci-reconcile.service`（reconcile）
- `~/.config/systemd/user/openharmony-ci-webhook.service`（未启用运行；保留）
- 配置：`~/.config/openharmony-ci-orchestrator/jenkins.env`（0600；CI_WEBHOOK_SECRET、GITLAB_HOST/PROJECT、`JENKINS_TRUSTED_ORIGINS=jenkins-chenxin.local:8080`）

### 已修复的两个部署级缺陷（2026-08-26）
1. **Jenkins 自指 URL 与访问地址不一致**：Jenkins 配置的 Jenkins URL = `http://jenkins-chenxin.local:8080`（本机不可解析），queue 返回的 executable URL 使用该 origin，导致 reconcile 的 origin 校验拒绝 + 后续 API 读不可达。
   - 修复 1（`ci_orchestrator.py safe_url`）：支持 `JENKINS_TRUSTED_ORIGINS`（逗号分隔 `host:port` 或 `http(s)://host:port`），仅作为 tracked origin 之外的显示白名单（安全不放松）。
   - 修复 2（`ci_orchestrator.py` queue executable 分支）：origin 校验通过后，把可执行 URL **重写到访问 origin**（`http://192.168.13.121:8080` + 原 path），后续 API 读在本机可解析。
2. **reconcile timer 频率**：1 小时 → 每 5 分钟（`install_systemd_units.py` 新增 `--reconcile-timer` 参数，默认仍 hourly 向后兼容；OnCalendar 表达式做了白名单校验）。

### 已验证
- `systemctl --user list-timers`：openharmony-ci-reconcile.timer 正常（5min 触发，最近 01:10:50）。
- `ci_orchestrator.py reconcile`（手动 + systemd 路径）：对注册 run 正确推进（queue→building→参数核对 verified_parameters=true→SUCCESS 后触发 headless）。
- 注入检查：`safe_url('http://evil.local:8080/x', tracked)` 在无白名单时仍被拒绝。

## 2. 触发分流规则（草案 v1，待落地为 classify_diff.py）

| 类别 | 判据（MR diff 路径） | Jenkins 作业 | 后续 |
|---|---|---|---|
| XTS-only | 全部 `test/xts/**` | `OpenHarmony-V6.1-XTS`（suite=HAP 名） | 下载 acts/haps.zip → 设备回测（不 OTA） |
| PRODUCT-only | 全部非 `test/xts` | `OpenHarmony-V6.1-AllWinner`/`-RockChip` | FULL 固件 → phase3 OTA → 回归 |
| MIXED | 两者都有 | 固件作业（保守）；可选并行 XTS 作业 | OTA + 回测 |

注意（2026-08-26 调查）：
- `OpenHarmony-V6.1-XTS` 作业参数：`Openharmony_Soc`(a333_newpines/rk3568)、`suite`(HAP 名，留空全量)、`Test_Case`(ALL)、`BUILD_MODE`(FULL/INCREMENTAL)、`FIRMWARE_BRANCH`(**Choice：仅 v6.1.0.31_release/master**——自定义分支触发返回 HTTP 500，见"待办"）。
- `trigger_jenkins_build.py` 新增 `--required-parameters`（作业契约参数集覆盖，默认 FIRMWARE_BRANCH,BUILD_MODE,FIRMWARE_TYPE）与"仅发送作业声明参数"过滤（严格参数作业需求）；XTS 作业触发**仍待 Jenkins 侧适配（500）**。
- 历史教训（runs/a333-mr168-build29.json）：OTA 链路存在设备身份预检噪声（hdc FreeChannelContinue）与 updater 版本校验失败；XTS 路线（回测）可规避整条 OTA 链路。

## 3. 待办/记录

- [ ] `OpenHarmony-V6.1-XTS` 作业自定义 FIRMWARE_BRANCH → 500：需 Jenkins 侧（作业 Choice 参数扩展自定义分支，或走 v6.1.0.31_release + MR 分支检查）——已搁置（用户指示）。
- [ ] webhook 可选启用：Jenkins 管理员配置回调 → `systemctl --user enable --now openharmony-ci-webhook.service`。
- [ ] 陈旧 run `ci-1787212700-874640cc.json`（Jenkins 404，构建已清理）每次 reconcile 产生噪音；可人工删除或后续在 reconcile 中把"Jenkins 404"转为 blocked 终态。
- [ ] classify_diff.py 落地（分流表→脚本）与 XTS 回测 runner（artifacts 下载→解包→设备 xdevice run→报告）。
